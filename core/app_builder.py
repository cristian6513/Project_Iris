"""Prepare editable app drafts and Store packages in the local Iris profile."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import uuid
from pathlib import Path

# The installed Hermes runtime and the development checkout invoke the same
# file with Iris' interpreter, from the user's workspace and in isolated mode.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.app_package_tool import inspect_package
from core.iris_skill_lifecycle import APP_TEMPLATE_FILES, bundled_app_builder_source
from core.profile_paths import ProfilePaths, _plain_directory, profile_writer

DRAFT_ID = re.compile(r"iris-app-[0-9a-f]{16}")
SDK2_TEMPLATE_FILES = (
    *APP_TEMPLATE_FILES, "ui/view.js", "backend/main.py", "skill/SKILL.md", "LICENSE",
)


def _draft_root(profile: Path) -> Path:
    root = profile / "apps" / "drafts"
    _plain_directory(root, profile)
    return root


def _builder_bundle() -> Path:
    bundle = bundled_app_builder_source()
    if bundle is None:
        raise ValueError("La plantilla de apps incluida con Iris no está disponible.")
    return bundle


def builder_guides() -> dict:
    resources = _builder_bundle().parents[1] / "builder"
    checkout = Path(__file__).resolve().parents[1]
    guides = {}
    for key, source in (("ui", "apps/README.md"), ("sdk2", "docs/guides/community-apps-python.md")):
        path = resources / Path(source).name
        if not path.is_file():
            path = checkout / source
        if not path.is_file():
            raise ValueError(f"No se encontró la guía {key} del constructor.")
        guides[key] = path.as_posix()
    return guides


def create_draft(profile: Path, *, sdk2: bool = False) -> dict:
    bundle = _builder_bundle()
    template_dir = bundle.parents[1] / "builder" / "sdk2" if sdk2 else bundle / "template"
    files = SDK2_TEMPLATE_FILES if sdk2 else APP_TEMPLATE_FILES
    if sdk2 and not template_dir.is_dir():
        template_dir = Path(__file__).resolve().parents[1] / "examples" / "apps" / "contador"
    root = _draft_root(profile)
    root.mkdir(parents=True, exist_ok=True)
    draft_id = "iris-app-" + uuid.uuid4().hex[:16]
    destination = root / draft_id
    with tempfile.TemporaryDirectory(prefix=".preparando-", dir=root) as temporary:
        draft = Path(temporary) / draft_id
        source = draft / "source"
        for name in files:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            template = (template_dir / name).read_text(encoding="utf-8")
            if name.startswith("ui/"):
                prefix = "contador-" if sdk2 else "iris-draft-"
                template = template.replace(prefix, f"{draft_id}-")
                if sdk2:
                    template = template.replace("'contador'", repr(draft_id))
            elif name == "skill/SKILL.md":
                template = template.replace("iris-app-contador", f"iris-app-{draft_id}")
                template = template.replace("`contador`", f"`{draft_id}`")
            target.write_text(template, encoding="utf-8")
        manifest_path = source / "app.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["id"] = draft_id
        for widget in manifest.get("widgets", []):
            widget["id"] = f"{draft_id}.{widget['id'].split('.')[-1]}"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        inspect_package(source)
        draft.rename(destination)
    return {"id": draft_id, "source": (destination / "source").as_posix()}


def pack_draft(profile: Path, draft_id: str) -> dict:
    if not DRAFT_ID.fullmatch(draft_id):
        raise ValueError("Usa el identificador devuelto al crear el borrador.")
    draft = _draft_root(profile) / draft_id
    source = draft / "source"
    _plain_directory(source, profile)
    if not source.is_dir():
        raise ValueError("No se encontró la carpeta del borrador.")
    package, archive = inspect_package(source)
    manifest = package["manifest"]
    if manifest["id"] != draft_id:
        raise ValueError("Conserva el id original del borrador en app.json.")
    if set(package["ignored"]) - {"LICENSE"}:
        raise ValueError("Deja en el borrador solo app.json, ui/, backend/, skill/ y la licencia.")
    output = draft / f"{draft_id}-{manifest['version']}-{uuid.uuid4().hex[:8]}.zip"
    with tempfile.TemporaryDirectory(prefix=".exportando-", dir=draft) as temporary:
        staged = Path(temporary) / output.name
        staged.write_bytes(archive)
        staged.rename(output)
    return {
        "id": draft_id,
        "name": manifest["name"],
        "version": manifest["version"],
        "package": output.as_posix(),
        "risks": package["risks"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new")
    new.add_argument("--sdk2", action="store_true", help="Incluye backend, acciones y skill de ejemplo.")
    commands.add_parser("guides")
    pack = commands.add_parser("pack")
    pack.add_argument("id")
    args = parser.parse_args(argv)
    if args.command == "guides":
        result = builder_guides()
    else:
        with profile_writer("app-builder", paths=ProfilePaths(args.profile)) as paths:
            if args.command == "new":
                result = create_draft(paths.home, sdk2=args.sdk2)
            else:
                result = pack_draft(paths.home, args.id)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
