"""Build a personal Windows x64 installer from a frozen source copy.

--check only inspects local prerequisites. --plan describes the recipe.
--build downloads pinned dependencies, runs tests, compiles and packages.
No signing key, API key, existing Iris installation or developer cache is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = (
    "snapshot",
    "npm-ci",
    "app-catalog",
    "web",
    "python-tests",
    "javascript-tests",
    "cargo-fetch-desktop",
    "cargo-fetch-companion",
    "rust-tests-desktop",
    "rust-tests-companion",
    "companion-build",
    "companion-stage",
    "python-packages",
    "verify-packages",
    "native-inputs",
    "private-runtime",
    "staging",
    "verify-staging",
    "installer",
    "checksums",
)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text("utf-8"))


def executable(name, *fallbacks):
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    return next((path.resolve() for path in fallbacks if path.is_file()), None)


def tool_paths(root: Path, msvc=None, sdk=None, seven_zip=None):
    pin = read_json(root / "scripts/pyav_build_sources.json")["msvc"]
    program_files = [
        Path(os.environ.get(name, "C:/Program Files" + (" (x86)" if "x86" in name else "")))
        for name in ("ProgramFiles", "ProgramFiles(x86)")
    ]
    if msvc is None:
        candidates = (
            [Path(os.environ["VCToolsInstallDir"])] if os.environ.get("VCToolsInstallDir") else []
        )
        for base in program_files:
            candidates.extend(
                base.glob("Microsoft Visual Studio/*/*/VC/Tools/MSVC/" + pin["version"])
            )
        msvc = next(
            (path for path in candidates if path.name == pin["version"] and path.is_dir()), None
        )
    if sdk is None:
        candidates = [base / "Windows Kits/10" for base in program_files]
        sdk = next(
            (
                path
                for path in candidates
                if (path / "Include" / pin["sdk_version"] / "um/Windows.h").is_file()
            ),
            None,
        )
    return {
        "git": executable("git"),
        "node": executable("node"),
        "npm": executable("npm.cmd" if os.name == "nt" else "npm"),
        "uv": executable("uv", Path.home() / ".local/bin/uv.exe"),
        "cargo": executable("cargo", Path.home() / ".cargo/bin/cargo.exe"),
        "rustup": executable("rustup", Path.home() / ".cargo/bin/rustup.exe"),
        "seven_zip": Path(seven_zip).absolute()
        if seven_zip
        else executable("7z", *(base / "7-Zip/7z.exe" for base in program_files)),
        "msvc": Path(msvc).absolute() if msvc else None,
        "windows_sdk": Path(sdk).absolute() if sdk else None,
    }


def preflight(root: Path, work: Path, tools: dict) -> list[str]:
    errors = []
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        errors.append("El instalador completo requiere Windows x64.")
    if sys.version_info[:2] != (3, 11):
        errors.append("Ejecuta esta receta con Python 3.11; COMPILAR.cmd lo solicita a uv.")
    for name, path in tools.items():
        if path is None or not path.exists():
            label = {
                "seven_zip": "7-Zip (7z.exe)",
                "node": "Node.js",
                "windows_sdk": "Windows SDK",
            }.get(name, name)
            errors.append("Falta la herramienta o carpeta: " + label)
    if work.exists():
        errors.append(
            "El destino debe ser nuevo; se conservan los intentos anteriores: " + str(work)
        )
    if work.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        work.resolve()
    ):
        errors.append("El destino debe estar fuera del código y no contenerlo.")
    if len(str(work / "staging")) > 65:
        errors.append("Usa una ruta de build corta, por ejemplo C:\\IrisBuilds\\personal-01.")
    ancestor = work.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < 40 * 1024**3:
        errors.append("Esta receta requiere al menos 40 GiB libres para el build completo.")
    from scripts.prepare_private_runtime import output_directory

    try:
        output_directory(work)
    except ValueError as exc:
        errors.append(str(exc))
    pin = read_json(root / "scripts/runtime_sources.json")
    for name, field in [("pyproject.toml", "pyproject_sha256"), ("uv.lock", "uv_lock_sha256")]:
        if digest(root / name) != pin["iris"][field]:
            errors.append("El pin de runtime no corresponde a " + name)
    versions = (root / "core/version.py").read_text("utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', versions, re.M)
    if (
        not match
        or match[1] != pin["iris"]["version"]
        or read_json(root / "desktop/src-tauri/tauri.conf.json")["version"] != match[1]
    ):
        errors.append("Las versiones de Core, Desktop y runtime deben coincidir.")
    native = read_json(root / "scripts/pyav_build_sources.json")["msvc"]
    if tools.get("msvc"):
        msvc = tools["msvc"]
        if msvc.name != native["version"] or any(
            not (msvc / "bin/Hostx64/x64" / name).is_file()
            for name in ("cl.exe", "link.exe", "lib.exe")
        ):
            errors.append("Se necesita MSVC " + native["version"] + " con herramientas x64.")
    if tools.get("windows_sdk"):
        sdk = tools["windows_sdk"]
        required = [
            f"Include/{native['sdk_version']}/um/Windows.h",
            f"Lib/{native['sdk_version']}/um/x64/kernel32.lib",
            f"bin/{native['sdk_version']}/x64/rc.exe",
        ]
        if any(not (sdk / name).is_file() for name in required):
            errors.append("Se necesita Windows SDK " + native["sdk_version"] + ".")
    if tools.get("node"):
        result = subprocess.run(
            [str(tools["node"]), "--version"], capture_output=True, text=True, timeout=15
        )
        if (
            result.returncode
            or result.stdout.strip().split(".")[0]
            != "v" + (root / ".node-version").read_text().strip()
        ):
            errors.append(
                "Se necesita Node "
                + (root / ".node-version").read_text().strip()
                + "; encontrado: "
                + (result.stdout.strip() or "sin versión reconocible")
            )
    if tools.get("rustup"):
        version = tomllib.loads((root / "rust-toolchain.toml").read_text("utf-8"))["toolchain"][
            "channel"
        ]
        result = subprocess.run(
            [str(tools["rustup"]), "toolchain", "list"], capture_output=True, text=True, timeout=15
        )
        if result.returncode or not any(
            line.startswith(version + "-") for line in result.stdout.splitlines()
        ):
            errors.append("Falta el toolchain Rust fijado: rustup toolchain install " + version)
    return errors


def freeze_source(root: Path, work: Path, git: Path):
    from scripts.export_repositories import private_name, read_plain, scan_secrets

    policy = read_json(root / "tools/repositories/exports.json")
    git_dir = work / "source-selection"
    subprocess.run([str(git), "init", "-q", str(git_dir)], check=True)
    command = [
        str(git),
        "--git-dir",
        str(git_dir / ".git"),
        "--work-tree",
        str(root),
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
    ]
    names = subprocess.run(command, cwd=root, check=True, capture_output=True).stdout.split(b"\0")
    selected = []
    for value in sorted(set(names)):
        if not value:
            continue
        name = value.decode("utf-8")
        if (
            name.split("/")[0] not in policy["main_roots"]
            and name not in policy["main_files"]
            and name != "SOURCE.json"
        ):
            continue
        if private_name(name) or any(
            name.startswith(prefix) for prefix in policy["excluded_prefixes"]
        ):
            continue
        data = read_plain(root, name)
        findings = scan_secrets(name, data, policy.get("secret_exceptions", ()))
        if findings:
            raise ValueError("Posibles secretos en la fuente; revisa " + name + ". No se compila.")
        selected.append((name, data))
    src = work / "source"
    src.mkdir()
    records = []
    for name, data in selected:
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        records.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    tree = hashlib.sha256(
        "".join(row["sha256"] + "  " + row["path"] + "\n" for row in records).encode()
    ).hexdigest()
    (work / "source-files.json").write_text(
        json.dumps({"tree_sha256": tree, "files": records}, indent=2) + "\n", "utf-8"
    )
    return src, tree


class Builder:
    def __init__(self, root, work, tools):
        self.root, self.work, self.tools = root, work, tools
        self.record = {"schema": 1, "personal_build": True, "completed": False, "steps": []}
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("IRIS_", "HERMES_", "ALICE_"))
            and key.upper()
            not in {
                "PYTHONPATH",
                "PYTHONHOME",
                "CARGO_TARGET_DIR",
                "CARGO_ENCODED_RUSTFLAGS",
                "RUSTFLAGS",
            }
        }
        self.env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "IRIS_HOME": str(work / "test-profile/iris"),
                "HERMES_HOME": str(work / "test-profile/hermes"),
            }
        )

    def save(self):
        (self.work / "build-result.json").write_text(
            json.dumps(self.record, indent=2) + "\n", "utf-8"
        )

    def run(self, name, command, *, cwd=None, env=None):
        print("Iniciando: " + name, flush=True)
        started = time.monotonic()
        log = self.work / "logs" / (name + ".log")
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                [str(value) for value in command],
                cwd=cwd or self.src,
                env=env or self.env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        self.record["steps"].append(
            {
                "name": name,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 1),
                "log": "logs/" + log.name,
            }
        )
        self.save()
        if result.returncode:
            raise ValueError("Falló " + name + ". Consulta " + str(log))

    def build(self):
        self.work.mkdir(parents=True, exist_ok=False)
        (self.work / "logs").mkdir()
        self.save()
        self.src, tree = freeze_source(self.root, self.work, self.tools["git"])
        self.record["source_tree_sha256"] = tree
        self.save()
        py, node, cargo, npm = (
            sys.executable,
            self.tools["node"],
            self.tools["cargo"],
            self.tools["npm"],
        )
        self.env["PATH"] = str(cargo.parent) + os.pathsep + self.env.get("PATH", "")
        web = self.work / "web"
        self.env["IRIS_WEB_ASSETS_ROOT"] = str(web)
        self.run("npm-ci", [npm, "ci", "--ignore-scripts"], cwd=self.src / "companion")
        self.run("app-catalog", [node, "scripts/build_app_catalog.mjs", "--check"])
        self.run(
            "web",
            [
                node,
                "scripts/build_web.mjs",
                "--output",
                web,
                "--verify-contract",
                "scripts/release_web_assets.json",
            ],
        )
        self.run("python-tests", [py, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"])
        js = [
            str(path.relative_to(self.src))
            for pattern in ("tests/js/*.test.mjs", "apps/*/tests/*.test.mjs")
            for path in sorted(self.src.glob(pattern))
        ]
        self.run("javascript-tests", [node, "--test", *js])
        for component in ("desktop", "companion"):
            manifest = component + "/src-tauri/Cargo.toml"
            self.run(
                "cargo-fetch-" + component,
                [cargo, "fetch", "--locked", "--manifest-path", manifest],
            )
            self.run(
                "rust-tests-" + component,
                [cargo, "test", "--locked", "--offline", "--manifest-path", manifest],
            )
        ctarget = self.src / "companion/src-tauri/target"
        flags = subprocess.run(
            [
                py,
                "-B",
                "-c",
                "from scripts.build_staging import launcher_rustflags; "
                "from pathlib import Path; import sys; "
                "print(launcher_rustflags(Path.cwd(), Path(sys.argv[1])))",
                str(ctarget),
            ],
            cwd=self.src,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        cenv = {
            **self.env,
            "CARGO_ENCODED_RUSTFLAGS": flags,
            "STATIC_VCRUNTIME": "true",
        }
        cenv.pop("RUSTFLAGS", None)
        self.run(
            "companion-build",
            [npm, "run", "tauri", "--", "build", "--ci"],
            cwd=self.src / "companion",
            env=cenv,
        )
        self.run(
            "companion-stage",
            [
                py,
                "-B",
                "-c",
                "from scripts.build_release import _release_versions, _refresh_manifest_hash; "
                "from scripts.stage_companion_release import stage_companion_release; "
                "from pathlib import Path; _release_versions(); _refresh_manifest_hash(); "
                'stage_companion_release(Path.cwd(), Path("dist/companion"))',
            ],
        )
        self.run(
            "python-packages",
            [py, "-B", "-m", "build", "--no-isolation", "--outdir", self.work / "packages"],
        )
        self.run(
            "verify-packages", [py, "-B", "scripts/verify_distribution.py", self.work / "packages"]
        )
        wheels = list((self.work / "packages").glob("iris_os-*.whl"))
        if len(wheels) != 1:
            raise ValueError("Se esperaba una única wheel de Iris.")
        self.run(
            "native-inputs",
            [
                py,
                "-B",
                "-m",
                "scripts.build_personal_native",
                "--work",
                self.work / "native",
                "--cache",
                self.work.parent / "download-cache",
                "--msvc",
                self.tools["msvc"],
                "--windows-sdk",
                self.tools["windows_sdk"],
                "--seven-zip",
                self.tools["seven_zip"],
            ],
        )
        pin = read_json(self.work / "native/native-pin.json")
        wheel = wheels[0]
        pin["iris"].update(wheel_sha256=digest(wheel), wheel_filename=wheel.name)
        pin_path = self.work / "personal-pin.json"
        pin_path.write_text(json.dumps(pin, indent=2) + "\n", "utf-8")
        self.run(
            "private-runtime",
            [
                py,
                "-B",
                "scripts/prepare_private_runtime.py",
                "--output",
                self.work / "runtime",
                "--cache-root",
                self.work / "native/runtime-inputs",
                "--iris-wheel",
                wheel,
                "--uv",
                self.tools["uv"],
                "--pin",
                pin_path,
            ],
        )
        self.run(
            "staging",
            [
                py,
                "-B",
                "scripts/build_staging.py",
                "--runtime",
                self.work / "runtime",
                "--cargo-target-dir",
                self.work / "cargo-desktop",
                "--output",
                self.work / "staging",
                "--pin",
                pin_path,
                "--companion",
                self.src / "dist/companion",
                "--cargo",
                cargo,
            ],
        )
        self.run(
            "verify-staging",
            [
                py,
                "-B",
                "scripts/build_staging.py",
                "--verify",
                self.work / "staging",
                "--pin",
                pin_path,
            ],
        )
        installers = self.work / "installer"
        self.run(
            "installer",
            [
                py,
                "-B",
                "scripts/build_installer.py",
                "--staging",
                self.work / "staging",
                "--output",
                installers,
                "--pin",
                pin_path,
            ],
        )
        shell = self.work / "native/runtime-inputs" / pin["shell"]["source_filename"]
        shutil.copyfile(shell, installers / shell.name)
        exes = list(installers.glob("*.exe"))
        if len(exes) != 1:
            raise ValueError("Se esperaba un único instalador personal.")
        outputs = [exes[0], installers / shell.name]
        (installers / "SHA256SUMS.txt").write_text(
            "".join(digest(path) + "  " + path.name + "\n" for path in outputs), "utf-8"
        )
        self.record.update(
            completed=True,
            installer=exes[0].name,
            installer_sha256=digest(exes[0]),
            signed=False,
            update_channel=None,
        )
        self.save()
        print("Instalador personal terminado: " + str(exes[0]), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Comprobar requisitos locales sin descargar ni compilar.",
    )
    mode.add_argument(
        "--plan", action="store_true", help="Mostrar la receta sin ejecutar herramientas."
    )
    mode.add_argument(
        "--build", action="store_true", help="Construir el instalador personal completo."
    )
    parser.add_argument("--work", type=Path, help="Carpeta nueva y corta fuera del código.")
    for option in ("msvc", "windows-sdk", "seven-zip"):
        parser.add_argument("--" + option, type=Path)
    args = parser.parse_args(argv)
    if args.plan:
        print(
            json.dumps(
                {"steps": STEPS, "downloads_iris_installer": False, "signs_or_publishes": False},
                indent=2,
            )
        )
        return 0
    work = (
        args.work
        or Path(ROOT.anchor)
        / "IrisBuilds"
        / (datetime.now(UTC).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6])
    ).absolute()
    tools = tool_paths(ROOT, args.msvc, args.windows_sdk, args.seven_zip)
    errors = preflight(ROOT, work, tools)
    print(
        json.dumps(
            {
                "work": str(work),
                "tools": {name: str(value) if value else None for name, value in tools.items()},
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if errors:
        return 1
    if args.build:
        Builder(ROOT, work, tools).build()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
