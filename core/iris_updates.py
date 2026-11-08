"""Joint Iris + Hermes updates from the Iris channel, with rollback (A08.8).

Only the packaged runtime uses this module. The channel manifest is signed
(``core.update_signing``) by a key the installed version already trusts; it
lists complete versions (Iris, the bundled Hermes pin and the Desktop
launcher) with the hash of each version's staging manifest. A version is
downloaded, checked and extracted into its own folder; activation happens in a
separate helper process, because Core, the Gateway and the Desktop window of
this installation must be closed before the new version starts. Nothing here
runs ``hermes update`` or Git: development keeps its external updaters.

Helper commands (``python -m core.iris_updates ...``) are a contract between
versions: an older version activates a newer one and vice versa.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from core import iris_install as inst
from core.iris_install import Install, InstallError
from core.update_signing import SignatureError, parse_trusted_keys, strict_json, verify_envelope

MAX_MANIFEST = 1024 * 1024
MAX_PACKAGE = 4 * 1024 ** 3
MAX_UNPACKED = 8 * 1024 ** 3
MAX_FILES = 200_000
CONFIRM_TIMEOUT = 240.0
_ID = re.compile(r"[0-9a-f]{8}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Release hosting may answer with redirects (GitHub Releases sends downloads to
# its asset hosts). Only these hosts are followed, over HTTPS, a few hops at
# most; the content is still checked against the signed manifest.
REDIRECT_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                            "release-assets.githubusercontent.com", "github-releases.githubusercontent.com"})
MAX_REDIRECTS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z", re.I)
_DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
_NO_WINDOW = 0x08000000


class UpdateError(RuntimeError):
    """User-facing reason why the update cannot continue."""


# ── Channel and manifest ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Channel:
    name: str
    url: str
    keys: dict[str, bytes]
    test: bool


@dataclass(frozen=True)
class Release:
    id: str
    iris: str
    iris_revision: str
    hermes: str
    hermes_revision: str
    profile_format: int
    minimum_from: str
    max_relative_path: int
    staging_sha256: str
    url: str
    size: int
    sha256: str
    unpacked_size: int
    files: int
    notes: str
    test: bool = False  # from a test channel: loopback HTTP allowed (QA only)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.iris, "hermes_version": self.hermes,
                "size": self.size, "notes": self.notes}


def _private_host(host: str) -> bool:
    """This machine or a private network, which a real channel never uses."""
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return "." not in host


def channel_url(value: Any, *, test: bool, what: str = "el canal") -> str:
    """HTTPS to a public host; a test channel may also use loopback HTTP."""
    if not isinstance(value, str) or len(value) > 2048:
        raise UpdateError(f"La dirección de {what} no es válida.")
    parts = urlsplit(value)
    if parts.username or parts.password or not parts.hostname:
        raise UpdateError(f"La dirección de {what} no es válida.")
    if parts.scheme == "https":
        if not test and _private_host(parts.hostname):
            raise UpdateError(f"La dirección de {what} apunta a este equipo o a una red privada.")
        return value
    if parts.scheme == "http" and test and parts.hostname in _LOOPBACK:
        return value
    raise UpdateError(f"La dirección de {what} debe usar HTTPS.")


def load_channel(folder: Path) -> Channel | None:
    """The channel a version trusts, verified as part of that version."""
    path = Path(folder) / inst.CHANNEL
    if not path.exists():
        return None
    try:
        data = strict_json(path.read_bytes(), 64 * 1024)
        if not isinstance(data, dict) or set(data) != {"schema", "channel", "url", "keys", "test"}:
            raise UpdateError("channel.json no tiene el formato esperado.")
        if data["schema"] != 1 or type(data["test"]) is not bool:
            raise UpdateError("channel.json no tiene el formato esperado.")
        if not isinstance(data["channel"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", data["channel"]):
            raise UpdateError("Nombre de canal inválido.")
        return Channel(data["channel"], channel_url(data["url"], test=data["test"]),
                       parse_trusted_keys(data["keys"]), data["test"])
    except SignatureError as exc:
        raise UpdateError(f"channel.json no es válido: {exc}") from exc


def _version(value: Any):
    from packaging.version import InvalidVersion, Version

    try:
        return Version(str(value))
    except InvalidVersion as exc:
        raise UpdateError(f"Versión inválida en el canal: {value!r}") from exc


def _timestamp(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateError("Fecha inválida en el manifiesto del canal.") from exc
    if moment.tzinfo is None:
        raise UpdateError("Fecha sin zona horaria en el manifiesto del canal.")
    return moment


def parse_manifest(payload: bytes, channel: Channel) -> dict[str, Any]:
    try:
        data = strict_json(payload, MAX_MANIFEST)
    except SignatureError as exc:
        raise UpdateError("El manifiesto del canal no es válido.") from exc
    if (not isinstance(data, dict) or data.get("schema") != 1 or data.get("product") != "iris-os"
            or data.get("channel") != channel.name):
        raise UpdateError("El manifiesto no corresponde a este canal de Iris.")
    sequence = data.get("sequence")
    if type(sequence) is not int or sequence < 1:
        raise UpdateError("El manifiesto del canal no tiene secuencia.")
    published, expires = _timestamp(data.get("published_at")), _timestamp(data.get("expires_at"))
    current = datetime.now(timezone.utc)
    if expires <= current or published > expires:
        raise UpdateError("El manifiesto del canal está vencido.")
    releases = data.get("releases")
    if not isinstance(releases, list) or len(releases) > 50:
        raise UpdateError("El manifiesto del canal no lista versiones válidas.")
    parsed = []
    for item in releases:
        try:
            if item["platform"] != "windows" or item["architecture"] != "x86_64":
                continue  # other builds may share a channel; never offered here
            package = item["package"]
            release = Release(
                id=item["id"], iris=str(item["iris"]["version"]), iris_revision=item["iris"]["revision"],
                hermes=str(item["hermes"]["version"]), hermes_revision=item["hermes"]["revision"],
                profile_format=item["profile_format"], minimum_from=str(item["minimum_from"]),
                max_relative_path=item["max_relative_path"], staging_sha256=item["staging_sha256"],
                url=channel_url(package["url"], test=channel.test, what="la versión"), size=package["size"],
                sha256=package["sha256"], unpacked_size=package["unpacked_size"], files=package["files"],
                notes=str(item.get("notes", ""))[:2000], test=channel.test)
        except (KeyError, TypeError) as exc:
            raise UpdateError("Una versión del canal está incompleta.") from exc
        _version(release.iris), _version(release.minimum_from)
        if (not _SHA.match(str(release.staging_sha256)) or release.id != release.staging_sha256[:8]
                or not _REVISION.match(str(release.iris_revision)) or not _REVISION.match(str(release.hermes_revision))
                or not _SHA.match(str(release.sha256))
                or type(release.size) is not int or not 0 < release.size <= MAX_PACKAGE
                or type(release.unpacked_size) is not int or not 0 < release.unpacked_size <= MAX_UNPACKED
                or type(release.files) is not int or not 0 < release.files <= MAX_FILES
                or type(release.profile_format) is not int or release.profile_format < 1
                or type(release.max_relative_path) is not int or not 1 <= release.max_relative_path <= 4096):
            raise UpdateError("Una versión del canal tiene datos inválidos.")
        parsed.append(release)
    return {"sequence": sequence, "published_at": data["published_at"], "releases": parsed}


def _redirect_target(current: str, location: str | None, *, test: bool, what: str) -> str:
    if not location:
        raise UpdateError(f"El servidor de {what} redirigió sin indicar el destino.")
    target = urljoin(current, location)
    parts = urlsplit(target)
    host = (parts.hostname or "").rstrip(".").lower()
    if test and host in _LOOPBACK and parts.scheme in {"http", "https"}:
        return target  # local QA channel
    channel_url(target, test=False, what=what)  # HTTPS, public host, no credentials
    if host not in REDIRECT_HOSTS:
        raise UpdateError(f"El servidor de {what} redirigió a un sitio no permitido ({host}).")
    return target


@contextmanager
def _get(client: Any, url: str, *, test: bool, what: str):
    """GET that follows at most MAX_REDIRECTS redirects, each one re-validated."""
    for _hop in range(MAX_REDIRECTS + 1):
        with client.stream("GET", url) as response:
            if response.status_code not in _REDIRECT_CODES:
                yield response
                return
            location = response.headers.get("location")
        url = _redirect_target(url, location, test=test, what=what)
    raise UpdateError(f"Demasiadas redirecciones al consultar {what}.")


def fetch_manifest(channel: Channel, client: Any = None) -> dict[str, Any]:
    import httpx

    owned = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=False)
    try:
        with _get(client, channel.url, test=channel.test, what="el canal") as response:
            if response.status_code != 200:
                raise UpdateError(f"El canal respondió HTTP {response.status_code}.")
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_MANIFEST:
                    raise UpdateError("El manifiesto del canal es demasiado grande.")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise UpdateError("No se pudo consultar el canal de actualizaciones. Revisa la conexión.") from exc
    finally:
        if owned:
            client.close()
    try:
        payload = verify_envelope(b"".join(chunks), channel.keys)
    except SignatureError as exc:
        raise UpdateError(f"El manifiesto del canal no es auténtico: {exc}") from exc
    return parse_manifest(payload, channel)


def choose_release(manifest: dict[str, Any], current: str, profile_format: int | None,
                   root: Path) -> tuple[Release | None, str]:
    """Newest release this installation can move to, or why none is offered."""
    current_version = _version(current)
    reasons = []
    best: Release | None = None
    for release in manifest["releases"]:
        version = _version(release.iris)
        if version <= current_version:
            continue
        if _version(release.minimum_from) > current_version:
            reasons.append(f"Iris {release.iris} requiere actualizar antes a {release.minimum_from}.")
            continue
        if profile_format is not None and release.profile_format < profile_format:
            reasons.append(f"Iris {release.iris} no puede abrir el formato actual del perfil.")
            continue
        if not inst.fits(root, release.max_relative_path):
            reasons.append(f"Iris {release.iris} no cabe en la ruta de esta instalación (límite de Windows).")
            continue
        if best is None or version > _version(best.iris):
            best = release
    return best, " ".join(reasons)


# ── Download, extraction and staging ───────────────────────────────────────

def _member(name: str) -> str:
    if (not name or len(name) > 1024 or name.startswith("/") or "\\" in name or ":" in name
            or any(ord(char) < 32 or char in '<>"|?*' for char in name)):
        raise UpdateError(f"El paquete contiene una ruta no permitida: {name!r}")
    for part in name.rstrip("/").split("/"):
        if part in {"", ".", ".."} or part != part.rstrip(" .") or _DEVICE.match(part):
            raise UpdateError(f"El paquete contiene una ruta no permitida: {name!r}")
    return name


def safe_extract(archive: Path, destination: Path, release: Release) -> None:
    """Extract only plain files with confined names, within the declared sizes."""
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > MAX_FILES * 2:
            raise UpdateError("El paquete contiene demasiadas entradas.")
        seen: set[str] = set()
        total = files = 0
        for member in members:
            # zipfile turns "\" into "/" on Windows; the stored name must be clean too.
            _member(member.orig_filename)
            name = _member(member.filename)
            mode = (member.external_attr >> 16) & 0o170000
            if mode not in (0, 0o100000, 0o040000) or member.flag_bits & 0x1:
                raise UpdateError(f"El paquete contiene un enlace o una entrada cifrada: {name}")
            if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise UpdateError("El paquete usa una compresión no admitida.")
            key = name.rstrip("/").casefold()
            if key in seen:
                raise UpdateError(f"El paquete repite una ruta: {name}")
            seen.add(key)
            if not member.is_dir():
                files += 1
                total += member.file_size
        if files != release.files or total != release.unpacked_size:
            raise UpdateError("El contenido del paquete no coincide con el canal.")
        destination.mkdir(parents=True)
        for member in members:
            target = destination / member.filename
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with package.open(member) as source, target.open("xb") as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    written += len(block)
                    if written > member.file_size:
                        raise UpdateError("El paquete declara un tamaño menor que su contenido.")
                    output.write(block)


def download(release: Release, target: Path, progress: Callable[[int, int], None] | None = None,
             client: Any = None) -> None:
    import httpx

    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    digest, written = hashlib.sha256(), 0
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=False)
    try:
        with _get(client, release.url, test=release.test, what="la descarga") as response:
            if response.status_code != 200:
                raise UpdateError(f"La descarga respondió HTTP {response.status_code}.")
            with partial.open("xb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    written += len(chunk)
                    if written > release.size:
                        raise UpdateError("La descarga es mayor que la versión publicada.")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(written, release.size)
                output.flush()
                os.fsync(output.fileno())
        if written != release.size or digest.hexdigest() != release.sha256:
            raise UpdateError("La descarga no coincide con la versión publicada; se descartó.")
        os.replace(partial, target)
    except httpx.HTTPError as exc:
        raise UpdateError("Se perdió la conexión durante la descarga; no se cambió nada.") from exc
    except OSError as exc:
        raise UpdateError(f"No se pudo guardar la descarga ({exc.strerror or exc}); no se cambió nada.") from exc
    finally:
        partial.unlink(missing_ok=True)
        if owned:
            client.close()


def _self_check(folder: Path, release: Release) -> None:
    """The new version loads its own descriptor and reports what the channel says."""
    probe = ("import json;from core.runtime_descriptor import get_runtime;from core.version import __version__;"
             "from core.profile_paths import PROFILE_FORMAT_VERSION as f;r=get_runtime();"
             "print(json.dumps([__version__,r.iris_revision,r.hermes.version,f]))")
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV"}}
    env.update(IRIS_RUNTIME_DESCRIPTOR=str(folder / inst.DESCRIPTOR), PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1")
    python = folder / "runtimes" / "iris" / "python.exe"
    try:
        result = subprocess.run([str(python), "-E", "-s", "-B", "-P", "-X", "utf8", "-c", probe],
                                cwd=str(folder / "runtimes" / "iris"), env=env, capture_output=True,
                                text=True, timeout=120, creationflags=_NO_WINDOW if os.name == "nt" else 0)
        reported = json.loads(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        reported = None
    if reported != [release.iris, release.iris_revision, release.hermes, release.profile_format]:
        raise UpdateError("La versión descargada no arrancó su comprobación interna.")


def prepare_release(install: Install, release: Release,
                    progress: Callable[[str, int, int], None] | None = None, client: Any = None) -> Path:
    """Download, verify and place the release in ``v/<id>``; activates nothing."""
    folder = install.folder(release.id)
    report = progress or (lambda *_: None)
    if folder.exists():
        # Fully hashed when it was first prepared (or installed); kept since.
        report("verifying", 0, 0)
        info = inst.verify_version(folder, release.staging_sha256, hashes=False, executables=True)
        if info["iris"] != release.iris:
            raise UpdateError("La versión conservada no coincide con el canal.")
        return folder
    free = shutil.disk_usage(install.root).free
    if free < release.size + release.unpacked_size + 512 * 1024 ** 2:
        raise UpdateError("No hay espacio suficiente en el disco para descargar e instalar la actualización.")
    install.updates.mkdir(exist_ok=True)
    archive = install.updates / f"{release.id}.zip"
    # Same length as v/<id>: the version's own MAX_PATH budget (A08.7) applies
    # while it is extracted and self-checked. "_" never names a real version.
    staging = install.versions / f"_{release.id[1:]}"
    try:
        if not archive.exists() or inst.sha256_file(archive) != release.sha256:
            archive.unlink(missing_ok=True)
            download(release, archive, lambda done, total: report("downloading", done, total), client)
        report("extracting", 0, 0)
        if staging.exists():
            shutil.rmtree(staging)
        safe_extract(archive, staging, release)
        report("verifying", 0, 0)
        info = inst.verify_version(staging, release.staging_sha256)
        if (info["iris"], info["iris_revision"], info["hermes"], info["hermes_revision"], info["profile_format"]) != (
                release.iris, release.iris_revision, release.hermes, release.hermes_revision, release.profile_format):
            raise UpdateError("El contenido de la versión no coincide con el canal.")
        _self_check(staging, release)
        for attempt in range(50):
            try:
                os.replace(staging, folder)
                break
            except PermissionError:
                # An antivirus may still hold a freshly extracted file for a moment.
                if attempt == 49:
                    raise
                time.sleep(0.2)
        archive.unlink(missing_ok=True)
        return folder
    except InstallError as exc:
        raise UpdateError(str(exc)) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


# ── Snapshot for the API ───────────────────────────────────────────────────

def _record(state: dict[str, Any], version: str | None) -> dict[str, Any] | None:
    record = state["versions"].get(version or "")
    if not record:
        return None
    return {"id": version, "version": record["iris"], "hermes_version": record["hermes"]}


def update_snapshot(check_channel: bool = True) -> dict[str, Any]:
    """Packaged update state; queries only the channel of the active version."""
    from core.runtime_descriptor import get_runtime, runtime_public_info
    from core.version import __version__

    runtime = get_runtime()
    base = {"ok": True, "checked_at": inst.now(), "runtime": runtime_public_info(), "can_update": False,
            "can_rollback": False, "available": None, "previous": None, "last_activation": None,
            "channel": None}
    item = {"id": "iris-os", "label": "Iris (con Hermes incluido)", "version": __version__,
            "hermes_version": runtime.hermes.version if runtime.hermes else "",
            "current_ref": runtime.iris_revision[:12], "status": "managed", "can_update": False}
    install = inst.find_install(runtime)
    if install is None:
        return {**base, "summary": "unavailable", "items": [{**item, "detail": "Copia sin instalador."}],
                "message": "Esta copia de Iris no se instaló con el instalador, así que no recibe actualizaciones."}
    try:
        state = inst.read_state(install)
    except InstallError as exc:
        return {**base, "summary": "error", "items": [item], "message": str(exc)}
    base.update(previous=_record(state, state.get("previous")), last_activation=state.get("last_activation"))
    previous_folder = install.folder(state["previous"]) if state.get("previous") else None
    base["can_rollback"] = bool(previous_folder and previous_folder.is_dir() and not state.get("pending"))
    if state.get("pending"):
        return {**base, "summary": "updating", "can_rollback": False, "items": [item],
                "message": "Hay una actualización en curso."}
    channel = load_channel(install.folder(state["active"]))
    if channel is None:
        return {**base, "summary": "unavailable", "items": [{**item, "detail": "Sin canal."}],
                "message": "Esta versión de Iris no tiene un canal de actualizaciones configurado."}
    base["channel"] = {"name": channel.name, "test": channel.test}
    if not check_channel:
        return {**base, "summary": "standby", "items": [item], "message": "Pulsa Buscar actualizaciones."}
    try:
        manifest = fetch_manifest(channel)
        with inst.state_lock(install):
            fresh = inst.read_state(install)
            seen = fresh["channel_sequences"].get(channel.url, 0)
            if manifest["sequence"] < seen:
                raise UpdateError("El canal devolvió un manifiesto anterior al último visto; se ignoró.")
            if manifest["sequence"] > seen:
                others = [(url, value) for url, value in fresh["channel_sequences"].items() if url != channel.url]
                latest = others[-(inst.MAX_CHANNELS - 1):] + [(channel.url, manifest["sequence"])]
                fresh["channel_sequences"] = dict(latest)
                inst.write_state(install, fresh)
        from core.profile_paths import persisted_profile_format

        release, reason = choose_release(manifest, __version__, persisted_profile_format(), install.root)
    except (UpdateError, InstallError) as exc:
        return {**base, "summary": "error", "items": [{**item, "detail": str(exc)}], "message": str(exc)}
    if release is None:
        message = reason or f"Iris {__version__} es la versión más reciente del canal {channel.name}."
        return {**base, "summary": "current", "items": [{**item, "status": "current", "detail": message}],
                "message": message}
    return {**base, "summary": "updates_available", "can_update": True, "available": release.public(),
            "items": [{**item, "status": "update_available", "can_update": True,
                       "detail": f"Disponible Iris {release.iris} con Hermes {release.hermes}."}],
            "message": f"Hay una versión nueva: Iris {release.iris} (Hermes {release.hermes})."}


# ── Activation (helper process) ────────────────────────────────────────────

def _spawn_detached(command: list[str], cwd: Path, env: dict[str, str], log: Path | None = None):
    output = log.open("ab") if log else subprocess.DEVNULL
    try:
        return subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
                                creationflags=_DETACHED if os.name == "nt" else 0, close_fds=True)
    finally:
        if log:
            output.close()


def start_activation(install: Install, target: str, operation: str) -> dict[str, Any]:
    """Record the pending activation and hand it to a detached helper."""
    import psutil
    from core.runtime_descriptor import get_runtime, runtime_subprocess_env

    runtime = get_runtime()
    own = inst.own_version_id(runtime)
    with inst.state_lock(install):
        state = inst.read_state(install)
        if state.get("pending"):
            raise UpdateError("Ya hay una actualización en curso.")
        if state["active"] != own:
            raise UpdateError("Solo la versión activa puede cambiar de versión.")
        if target == own or not install.folder(target).is_dir():
            raise UpdateError("La versión de destino no está disponible.")
        state["pending"] = {"operation": operation, "from": own, "to": target, "phase": "starting",
                            "started_at": inst.now(), "helper": None, "backup": None,
                            "previous_before": state.get("previous")}
        inst.write_state(install, state)
    command = [str(runtime.iris_python), "-E", "-s", "-B", "-P", "-X", "utf8", "-m", "core.iris_updates", "activate",
               "--root", str(install.root), "--to", target, "--operation", operation]
    try:
        process = _spawn_detached(command, install.updates, runtime_subprocess_env(),
                                  install.updates / "activation.log")
        created = psutil.Process(process.pid).create_time()
    except Exception as exc:
        with inst.state_lock(install):
            state = inst.read_state(install)
            state["pending"] = None
            inst.write_state(install, state)
        raise UpdateError(f"No se pudo iniciar el proceso de activación: {exc}") from exc
    with inst.state_lock(install):
        state = inst.read_state(install)
        if state.get("pending") and state["pending"].get("helper") is None:
            state["pending"]["helper"] = {"pid": process.pid, "created": created}
            inst.write_state(install, state)
    return {"pid": process.pid}


class _Refused(Exception):
    pass


def _norm(path: str | os.PathLike | None) -> str:
    if not path:
        return ""
    text = str(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.abspath(text))


def _inside(path: str, folder: Path) -> bool:
    candidate, base = _norm(path), _norm(folder)
    return bool(candidate) and candidate.startswith(base + os.sep)


def _profile_homes() -> set[str]:
    from core.config import get_iris_home
    from core.workspace_sync import get_hermes_home

    return {_norm(get_iris_home()), _norm(get_hermes_home())}


def _process_homes(process: Any) -> set[str]:
    environment = {key.upper(): value for key, value in process.environ().items()}
    user = environment.get("USERPROFILE") or environment.get("HOME") or ""
    return {_norm(environment.get("IRIS_HOME") or (Path(user) / ".iris" if user else "")),
            _norm(environment.get("HERMES_HOME") or "")} - {""}


def processes_of(folder: Path, *, profile_only: bool = True, name: str | None = None) -> list[Any]:
    """Processes started from a version folder (for this profile, by default)."""
    import psutil

    homes = _profile_homes() if profile_only else set()
    found = []
    for process in psutil.process_iter(["pid", "exe", "name"]):
        exe = process.info.get("exe") or ""
        # Some system processes report a bare name ("Registry"); resolving it
        # against the current folder would place it inside the installation.
        if process.pid == os.getpid() or not os.path.isabs(exe) or not _inside(exe, folder):
            continue
        if name and (process.info.get("name") or "").casefold() != name.casefold():
            continue
        if profile_only:
            try:
                if not (_process_homes(process) & homes):
                    continue
            except Exception:
                continue
        found.append(process)
    return found


def _wait_gone(processes: list[Any], timeout: float) -> list[Any]:
    import psutil

    _, alive = psutil.wait_procs(processes, timeout=timeout)
    return alive


def _close_desktop(install: Install, version: str) -> None:
    folder = install.folder(version)
    windows = processes_of(folder, name=inst.EXECUTABLE)
    if not windows:
        return
    from core.runtime_descriptor import runtime_subprocess_env

    env = runtime_subprocess_env()
    env.pop("IRIS_RUNTIME_DESCRIPTOR", None)
    subprocess.run([str(folder / inst.EXECUTABLE), "--iris-exit"], cwd=str(folder), env=env,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=30, creationflags=_NO_WINDOW if os.name == "nt" else 0)
    if _wait_gone(windows, 30):
        raise _Refused("La ventana de Iris no se cerró; ciérrala y vuelve a intentarlo.")


def _stop_own_services() -> dict[str, Any]:
    from core.process_launcher import stop_iris
    from core.hermes_service_registry import stop_owned_gateway
    from core.workspace_sync import get_hermes_home

    core = stop_iris()
    gateway = stop_owned_gateway(get_hermes_home())
    return {"core": bool(core), "gateway": gateway.get("stopped", False), "blocked": gateway.get("blocked", False)}


def _drain_version(install: Install, version: str) -> None:
    """After a stop request, our own leftovers of that version must exit."""
    folder = install.folder(version)
    remaining = _wait_gone(processes_of(folder), 30)
    for process in remaining:
        try:
            process.terminate()
        except Exception:
            pass
    remaining = _wait_gone(remaining, 10)
    if remaining:
        names = sorted({str(getattr(p, "info", {}).get("name") or p.pid) for p in remaining})
        raise _Refused(f"Un proceso de Iris sigue en uso ({', '.join(names)}); no se cambió de versión.")


def _ports_free() -> None:
    from core.config import IRISConfig
    from core.desktop import port_taken

    config = IRISConfig.load()
    ports = [config.websocket_port, getattr(config, "companion_loopback_port", None)]
    hermes = urlsplit(str(getattr(config, "hermes_url", "") or ""))
    if hermes.hostname in _LOOPBACK and hermes.port:
        ports.append(hermes.port)
    for port in [p for p in ports if p]:
        if port_taken(port):
            raise _Refused(f"El puerto {port} sigue ocupado: otra instalación de Iris u otro programa "
                           "lo usa con este perfil. Ciérralo y vuelve a intentarlo.")


def _set_phase(install: Install, phase: str, **fields: Any) -> dict[str, Any]:
    with inst.state_lock(install):
        state = inst.read_state(install)
        state["pending"].update(phase=phase, **fields)
        inst.write_state(install, state)
        return state


def _launch(install: Install) -> None:
    from core.runtime_descriptor import runtime_subprocess_env

    env = runtime_subprocess_env()
    env.pop("IRIS_RUNTIME_DESCRIPTOR", None)
    _spawn_detached([str(install.launcher)], install.root, env)


def _stop_version_services(install: Install, version: str) -> None:
    """Each version stops its own services with its own identity rules."""
    folder = install.folder(version)
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV"}}
    env.update(IRIS_RUNTIME_DESCRIPTOR=str(folder / inst.DESCRIPTOR), PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([str(folder / "runtimes" / "iris" / "python.exe"), "-E", "-s", "-B", "-P", "-X", "utf8", "-m",
                    "core.iris_updates", "stop-services"], cwd=str(folder / "runtimes" / "iris"), env=env,
                   capture_output=True, timeout=60, creationflags=_NO_WINDOW if os.name == "nt" else 0)


def refresh_launcher(install: Install, version: str) -> str:
    """Give the root launcher the active version's Iris.exe when it is not in use.

    The launcher only reads ``active``/``previous``/``confirmed`` from
    install.json, a format every version keeps; an older launcher still works
    if Windows keeps the file busy, so this is best effort.
    """
    source = install.folder(version) / inst.EXECUTABLE
    if inst.sha256_file(source) == inst.sha256_file(install.launcher):
        return "unchanged"
    staged = install.root / (inst.EXECUTABLE + ".new")
    shutil.copyfile(source, staged)
    try:
        os.replace(staged, install.launcher)
    except OSError:
        staged.unlink(missing_ok=True)
        return "busy"
    return "updated"


def prune_versions(install: Install) -> list[str]:
    """Keep the active and previous versions; skip folders still in use."""
    state = inst.read_state(install)
    keep = {state["active"], state.get("previous")}
    removed = []
    for folder in install.versions.iterdir():
        if folder.name in keep or not folder.is_dir() or processes_of(folder, profile_only=False):
            continue
        try:
            shutil.rmtree(folder)
            removed.append(folder.name)
        except OSError:
            continue
    for leftover in install.versions.glob("_*"):
        shutil.rmtree(leftover, ignore_errors=True)
    for leftover in install.updates.glob("*.part"):
        leftover.unlink(missing_ok=True)
    return removed


def _log(message: str) -> None:
    print(f"{inst.now()} {message}", flush=True)


def activate(install: Install, target: str, operation: str, confirm_timeout: float = CONFIRM_TIMEOUT) -> int:
    """Helper process: switch this installation from the active version to *target*."""
    from core.profile_paths import PROFILE_FORMAT_VERSION, ProfileBusyError, persisted_profile_format, profile_writer
    from core.config import get_iris_home
    from core.workspace_sync import get_hermes_home

    own = inst.own_version_id()
    state = inst.read_state(install)
    pending = state.get("pending")
    if not pending or pending["to"] != target or pending["from"] != own or state["active"] != own:
        _log("No pending activation for this helper.")
        return 2
    _log(f"{operation} {own} -> {target}")
    try:
        _set_phase(install, "closing")
        _close_desktop(install, own)
        _set_phase(install, "stopping")
        services = _stop_own_services()
        _log(f"stop services: {services}")
        if services.get("blocked"):
            # The new version would refuse the same Gateway and only fail its
            # trial minutes later; say so now and keep the running version.
            raise _Refused("Un Gateway de Hermes que no se pudo detener sigue usando el perfil.")
        _drain_version(install, own)
        _set_phase(install, "checking")
        _ports_free()
        info = inst.verify_version(install.folder(target), hashes=False, executables=True)
        if info["id"] != target or not inst.fits(install.root, info["max_relative_path"]):
            raise _Refused("La versión de destino no es válida para esta instalación.")
        current = state["versions"][own]
        with profile_writer("iris-updater", format_version=PROFILE_FORMAT_VERSION):
            persisted = persisted_profile_format()
            backup = None
            restore = None
            if operation == "rollback" and persisted is not None and info["profile_format"] < persisted:
                restore = state["versions"][own].get("backup_from_previous")
                if not restore or not Path(restore).is_dir():
                    raise _Refused("La versión anterior no puede abrir el perfil actual y no hay copia de seguridad.")
            elif info["profile_format"] != (persisted or info["profile_format"]) or info["hermes"] != current["hermes"]:
                _set_phase(install, "backup")
                backup = str(inst.backup_profile(get_iris_home(), get_hermes_home(), f"{own}-to-{target}"))
                _log(f"profile backup: {backup}")
            if restore:
                inst.restore_profile(get_iris_home(), get_hermes_home(), Path(restore))
                _log(f"profile restored from {restore}")
            with inst.state_lock(install):
                fresh = inst.read_state(install)
                record = fresh["versions"].get(target) or inst.version_record(info, "update")
                if backup:
                    record["backup_from_previous"] = backup
                fresh["versions"][target] = record
                fresh["active"], fresh["previous"], fresh["confirmed"] = target, own, False
                fresh["pending"].update(phase="switched", backup=backup)
                inst.clear_trial(install)
                inst.write_state(install, fresh)
        _log("switched")
    except Exception as exc:  # before the switch: always bring the running version back
        if not isinstance(exc, (_Refused, ProfileBusyError, InstallError, OSError)):
            import traceback

            _log(traceback.format_exc())
        message = str(exc) or "No se pudo cambiar de versión."
        _log(f"refused before switching: {message}")
        with inst.state_lock(install):
            fresh = inst.read_state(install)
            if fresh.get("pending") and fresh["pending"].get("phase") != "switched":
                inst._outcome(fresh, "failed", f"No se cambió de versión: {message}", fresh["pending"])
                fresh["pending"] = None
                inst.write_state(install, fresh)
        _launch(install)
        return 1
    _launch(install)
    deadline = time.monotonic() + confirm_timeout
    current_state = state
    while time.monotonic() < deadline:
        time.sleep(1)
        current_state = inst.read_state(install)
        if current_state["active"] == target and current_state["confirmed"]:
            _log("confirmed")
            try:
                _log(f"launcher: {refresh_launcher(install, target)}")
                _log(f"pruned: {prune_versions(install)}")
            except Exception as exc:  # cleanup never undoes a confirmed activation
                _log(f"cleanup skipped: {exc}")
            return 0
    _log("target did not confirm; reverting")
    if not _revert(install, own, target, f"{inst.describe(current_state, target)} no terminó de arrancar en "
                   f"{int(confirm_timeout)} s"):
        _log("confirmed just before reverting; kept")
        return 0
    return 1


def _revert(install: Install, own: str, target: str, reason: str) -> bool:
    """Go back to *own*; False when *target* confirmed in the meantime (kept)."""
    from core.config import get_iris_home
    from core.workspace_sync import get_hermes_home

    with inst.state_lock(install):
        state = inst.read_state(install)
        if state["active"] == target and state["confirmed"]:
            return False
        if state.get("pending"):
            state["pending"]["phase"] = "reverting"  # a late confirmation is refused from here
            inst.write_state(install, state)
    for step in (lambda: _close_desktop(install, target), lambda: _stop_version_services(install, target)):
        try:
            step()
        except Exception as exc:
            _log(f"revert step skipped: {exc}")
    for process in _wait_gone(processes_of(install.folder(target)), 30):
        try:
            process.terminate()
        except Exception:
            pass
    with inst.state_lock(install):
        state = inst.read_state(install)
        pending = state.get("pending") or {}
        message = f"{reason}; se volvió a {inst.describe(state, own)}."
        if pending.get("backup"):
            try:
                inst.restore_profile(get_iris_home(), get_hermes_home(), Path(pending["backup"]))
                message += " El perfil se restauró desde la copia previa."
            except Exception as exc:
                message += f" No se pudo restaurar la copia del perfil ({exc}); está en {pending['backup']}."
        state["active"], state["confirmed"] = own, True
        state["previous"] = pending.get("previous_before")
        inst._outcome(state, "reverted", message, pending or {"operation": "upgrade", "from": own, "to": target})
        state["pending"] = None
        inst.clear_trial(install)
        inst.write_state(install, state)
    inst.sync_windows_entry(install)
    _launch(install)
    return True


# ── Core job (download and staging run inside Core) ────────────────────────

_JOB_LOCK = threading.Lock()
JOB: dict[str, Any] = {"active": False, "phase": "idle"}


def job_status() -> dict[str, Any]:
    with _JOB_LOCK:
        return dict(JOB)


def _job(**fields: Any) -> None:
    with _JOB_LOCK:
        JOB.update(fields)


def begin_job(operation: str) -> bool:
    with _JOB_LOCK:
        if JOB.get("active"):
            return False
        JOB.clear()
        JOB.update(active=True, operation=operation, phase="checking", ok=None, progress=None,
                   message="Comprobando la actualización…")
        return True


def run_update_job() -> None:
    """Find, prepare and activate the newest release (runs in a Core thread)."""
    from core.profile_paths import persisted_profile_format
    from core.version import __version__

    try:
        install = inst.find_install()
        if install is None:
            raise UpdateError("Esta copia de Iris no admite actualizaciones.")
        state = inst.read_state(install)
        channel = load_channel(install.folder(state["active"]))
        if channel is None:
            raise UpdateError("Esta versión no tiene canal de actualizaciones.")
        manifest = fetch_manifest(channel)
        if manifest["sequence"] < state["channel_sequences"].get(channel.url, 0):
            raise UpdateError("El canal devolvió un manifiesto anterior al último visto; se ignoró.")
        release, reason = choose_release(manifest, __version__, persisted_profile_format(), install.root)
        if release is None:
            raise UpdateError(reason or "No hay una versión nueva.")

        def progress(phase: str, done: int, total: int) -> None:
            labels = {"downloading": "Descargando la actualización…", "extracting": "Preparando los archivos…",
                      "verifying": "Verificando la versión nueva…"}
            _job(phase=phase, message=labels.get(phase, ""), progress={"done": done, "total": total} if total else None)

        prepare_release(install, release, progress)
        _job(phase="activating", progress=None,
             message=f"Iris se cerrará y volverá a abrirse con la versión {release.iris}.")
        start_activation(install, release.id, "upgrade")
        _job(active=False, ok=True)
    except (UpdateError, InstallError) as exc:
        _job(active=False, ok=False, phase="failed", message=str(exc), progress=None)
    except Exception as exc:  # unexpected: reported, never half-activated
        _job(active=False, ok=False, phase="failed", progress=None,
             message=f"La actualización no se completó: {exc}")


def run_rollback_job() -> None:
    try:
        install = inst.find_install()
        if install is None:
            raise UpdateError("Esta copia de Iris no admite cambiar de versión.")
        state = inst.read_state(install)
        previous = state.get("previous")
        if not previous or not install.folder(previous).is_dir():
            raise UpdateError("No hay una versión anterior conservada.")
        _job(phase="verifying", message="Verificando la versión anterior…")
        # Fully hashed when it was installed; structure, sizes and programs now.
        inst.verify_version(install.folder(previous), hashes=False, executables=True)
        _job(phase="activating", message=f"Iris se cerrará y volverá a abrirse con {inst.describe(state, previous)}.")
        start_activation(install, previous, "rollback")
        _job(active=False, ok=True)
    except (UpdateError, InstallError) as exc:
        _job(active=False, ok=False, phase="failed", message=str(exc))


# ── Command line (helper contract between versions) ────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.iris_updates")
    commands = parser.add_subparsers(dest="command", required=True)
    activate_cmd = commands.add_parser("activate")
    activate_cmd.add_argument("--root", required=True, type=Path)
    activate_cmd.add_argument("--to", required=True)
    activate_cmd.add_argument("--operation", required=True, choices=inst.OPERATIONS)
    commands.add_parser("stop-services")
    install_cmd = commands.add_parser("install")
    install_cmd.add_argument("--staging", required=True, type=Path)
    install_cmd.add_argument("--root", required=True, type=Path)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    if args.command == "activate":
        return activate(Install(args.root.absolute()), args.to, args.operation)
    if args.command == "stop-services":
        print(json.dumps(_stop_own_services()))
        return 0
    if args.command == "install":
        print(json.dumps({"active": inst.create_install(args.staging, args.root)}))
        return 0
    print(json.dumps(inst.verify_version(args.folder), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
