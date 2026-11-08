"""A08.8 release tooling for the Iris update channel.

    python scripts/build_update_package.py keygen --private <key> --public <key.json> [--protect]
    python scripts/build_update_package.py backup-key --private <protected key> --output <backup.pem>
    python scripts/build_update_package.py package --staging <A08.7 staging> --output <iris-x.y.z.zip>
        [--minimum-from 1.0.0] [--notes "..."]
    python scripts/build_update_package.py manifest --channel stable --sequence <n> --private <key.pem>
        --release <package.zip>=<public URL> [...] --output <stable.json> [--expires-days 30] [--test]
    python scripts/build_update_package.py renew --previous <published.json> --key <production key .json> [...]
        --private <key> --output <next.json> [--expires-days 90]
    python scripts/build_update_package.py check --manifest <stable.json> --channel-url <https://.../stable.json>
        --key <production key .json> [...] [--package <package.zip> ...] [--remote]
        (--previous <old.json> | --first-publication)
    python scripts/build_update_package.py verify-key --private <key or backup> --key <production key .json>

``package`` zips exactly the files a staging manifest lists and writes
``<zip>.release.json`` next to it (hashes, sizes, versions, profile format).
``manifest`` turns those entries into the signed channel document. The private
key never belongs in the repository or in a version; only the public entry is
embedded through ``build_staging.py --channel-key``. Keys made here for QA are
test keys: the production key and where the channel is hosted are decisions
for the release owner (A08.9).

A production key is made with ``keygen --protect``: the private key is written
only encrypted with Windows DPAPI for the current user (it never touches the
disk in clear) and the commands below decrypt it in memory. ``backup-key``
writes a passphrase-encrypted copy for offline storage; the owner types the
passphrase, and that copy can also sign (the passphrase is asked for).

``renew`` re-signs a published manifest that the production keys signed: the
same releases with the next sequence and new dates (a manifest expires, so a
channel without new versions is renewed before ``expires_at``).
``verify-key`` confirms that a key or its backup opens and matches a public key.

``check`` is the gate before a real channel is published: the manifest is
signed only by the production keys and valid for a real channel (HTTPS to a
public host, not about to expire, a sequence above the previous one), and each
listed package matches it, trusts exactly the production keys, points at the
real channel with ``test: false`` and carries no private key. With ``--remote``
a package without a local copy is downloaded anonymously, as Iris does, and
checked the same way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core import iris_install  # noqa: E402
from core import iris_updates as updates  # noqa: E402
from core.update_signing import (  # noqa: E402
    SignatureError,
    generate_private_key,
    load_private_key,
    parse_trusted_keys,
    public_entry,
    sign_payload,
    strict_json,
    verify_envelope,
)

# A PEM private key block (plain or encrypted), also as an escaped JSON string;
# a mere mention of the marker in source code does not match.
_NEWLINE = rb"(?:\r?\n|(?:\\r)?\\n)"
_PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----" + _NEWLINE
                          + rb"(?:[A-Za-z0-9-]+: [^\r\n]*" + _NEWLINE + rb")*" + _NEWLINE + rb"?[A-Za-z0-9+/=]{40,}")
_KEY_STORES = (".p12", ".pfx", ".jks", ".keystore")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_PROTECTED = b"IRIS-OS-DPAPI-1\n"
_ENTROPY = b"iris-os update channel signing key"


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """Windows DPAPI for the current user; nothing else can decrypt the result."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> tuple[Blob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32, kernel32 = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32")
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR if protect else ctypes.c_void_p,
                         ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    (source, _keep), (entropy, _keep_entropy), result = blob(data), blob(_ENTROPY), Blob()
    description = "Iris OS update signing key" if protect else None
    if not function(ctypes.byref(source), description, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(result)):
        raise SystemExit(f"Windows DPAPI could not {'protect' if protect else 'open'} the key "
                         f"(error {ctypes.get_last_error()}); it only opens for the Windows user that made it.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def read_private_key(path: Path):
    """A DPAPI-protected key, a passphrase-encrypted backup (asks) or a plain QA key."""
    raw = path.read_bytes()
    if raw.startswith(_PROTECTED):
        return load_private_key(_dpapi(raw[len(_PROTECTED):], protect=False))
    if b"ENCRYPTED PRIVATE KEY" in raw:
        import getpass

        return load_private_key(raw, getpass.getpass(f"Frase de la copia {path.name}: ").encode("utf-8"))
    return load_private_key(raw)


def keygen(private: Path, public: Path, protect: bool = False) -> dict[str, str]:
    if private.exists() or public.exists():
        raise SystemExit("Refusing to overwrite an existing key.")
    pem, entry = generate_private_key()
    private.parent.mkdir(parents=True, exist_ok=True)
    data = _PROTECTED + _dpapi(pem, protect=True) if protect else pem
    with private.open("xb") as handle:
        handle.write(data)
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(entry, indent=1) + "\n", "utf-8")
    return entry


def backup_key(private: Path, output: Path) -> dict[str, str]:
    """Passphrase-encrypted PKCS#8 copy of a key, for storage away from this PC."""
    import getpass

    from cryptography.hazmat.primitives import serialization

    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    key = read_private_key(private)
    phrase = getpass.getpass("Frase para la copia (mínimo 12 caracteres): ")
    if len(phrase) < 12 or phrase != getpass.getpass("Repite la frase: "):
        raise SystemExit("La frase es demasiado corta o no coincide; no se escribió la copia.")
    data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.BestAvailableEncryption(phrase.encode("utf-8")))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(data)
    return public_entry(key.public_key())


def package(staging: Path, output: Path, minimum_from: str | None = None, notes: str = "") -> dict:
    """Deterministic zip of one verified staging folder plus its release entry."""
    staging, output = staging.absolute(), output.absolute()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    info = iris_install.verify_version(staging)
    manifest = json.loads((staging / iris_install.STAGING_MANIFEST).read_text("utf-8"))
    inventory = json.loads((staging / iris_install.INVENTORY).read_text("utf-8"))
    names = sorted([iris_install.STAGING_MANIFEST, iris_install.INVENTORY]
                   + [item["path"] for item in manifest["files"]] + [item["path"] for item in inventory["files"]])
    unpacked = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in names:
            source = staging / name
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            with source.open("rb") as handle, archive.open(entry, "w", force_zip64=True) as target:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    target.write(block)
            unpacked += source.stat().st_size
    release = {
        "id": info["id"], "platform": "windows", "architecture": "x86_64",
        "iris": {"version": info["iris"], "revision": info["iris_revision"]},
        "hermes": {"version": info["hermes"], "revision": info["hermes_revision"]},
        "profile_format": info["profile_format"], "minimum_from": minimum_from or "0",
        "max_relative_path": info["max_relative_path"], "staging_sha256": info["staging_sha256"],
        "package": {"size": output.stat().st_size, "sha256": _sha256(output), "unpacked_size": unpacked,
                    "files": len(names)},
        "notes": notes,
    }
    output.with_name(output.name + ".release.json").write_text(json.dumps(release, indent=1) + "\n", "utf-8")
    return release


def manifest(channel: str, sequence: int, private: Path, releases: list[str], output: Path,
             expires_days: int = 30, test: bool = False) -> dict:
    entries = []
    for spec in releases:
        archive, _, url = spec.partition("=")
        if not url:
            raise SystemExit("--release needs <package.zip>=<URL>")
        try:
            updates.channel_url(url, test=test, what="la versión")
        except updates.UpdateError as exc:
            raise SystemExit(f"{url}: {exc} (loopback HTTP needs --test)") from exc
        entry = json.loads(Path(archive + ".release.json").read_text("utf-8"))
        entry["package"]["url"] = url
        entries.append(entry)
    published = datetime.now(timezone.utc).replace(microsecond=0)
    document = {"schema": 1, "product": "iris-os", "channel": channel, "sequence": sequence,
                "published_at": published.isoformat(),
                "expires_at": (published + timedelta(days=expires_days)).isoformat(), "releases": entries}
    payload = json.dumps(document, indent=1, ensure_ascii=False).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(sign_payload(payload, [read_private_key(private)]))
    return document


def _production(key_paths: list[Path]) -> dict[str, bytes]:
    return parse_trusted_keys([json.loads(path.read_text("utf-8")) for path in key_paths])


def renew(previous: Path, key_paths: list[Path], private: Path, output: Path, expires_days: int = 90) -> dict:
    """The releases of a manifest the production keys signed, with the next sequence and new dates."""
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    production = _production(key_paths)
    try:
        old = strict_json(verify_envelope(previous.read_bytes(), production))
    except SignatureError as exc:
        raise SystemExit(f"The published manifest is not signed by the production keys: {exc}") from exc
    if (not isinstance(old, dict) or old.get("schema") != 1 or old.get("product") != "iris-os"
            or type(old.get("sequence")) is not int or old["sequence"] < 1 or not isinstance(old.get("releases"), list)):
        raise SystemExit("The published manifest does not have the expected format.")
    key = read_private_key(private)
    if public_entry(key.public_key())["keyid"] not in production:
        raise SystemExit("The private key is not one of the production keys given.")
    published = datetime.now(timezone.utc).replace(microsecond=0)
    document = {**old, "sequence": old["sequence"] + 1, "published_at": published.isoformat(),
                "expires_at": (published + timedelta(days=expires_days)).isoformat()}
    payload = json.dumps(document, indent=1, ensure_ascii=False).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(sign_payload(payload, [key]))
    return document


def verify_key(private: Path, key_paths: list[Path]) -> dict:
    """A key (DPAPI, passphrase backup or plain) opens and is one of the given public keys."""
    raw = private.read_bytes()
    kind = ("dpapi" if raw.startswith(_PROTECTED) else
            "backup with passphrase" if b"ENCRYPTED PRIVATE KEY" in raw else "plain")
    keyid = public_entry(read_private_key(private).public_key())["keyid"]
    production = _production(key_paths)
    if keyid not in production:
        raise SystemExit(f"{private.name} opens but its key {keyid} is not {sorted(production)}.")
    return {"file": str(private), "format": kind, "keyid": keyid, "matches": True}


def anonymous_client():
    """No credentials, cookies or environment configuration: what anyone gets."""
    import httpx

    return httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=False, trust_env=False)


def private_keys_in(archive: zipfile.ZipFile, chunk: int = 4 * 1024 * 1024) -> list[str]:
    """Entries that hold a private key or a key store. Public certificates pass."""
    found = []
    for info in archive.infolist():
        if info.filename.lower().endswith(_KEY_STORES):
            found.append(info.filename)
            continue
        tail = b""
        with archive.open(info) as handle:
            for block in iter(lambda: handle.read(chunk), b""):
                window = tail + block
                if b"PRIVATE KEY-----" in window and _PRIVATE_KEY.search(window):
                    found.append(info.filename)
                    break
                tail = window[-512:]
    return found


def _package_problems(path: Path, release: updates.Release, channel: str, url: str,
                      production: dict[str, bytes]) -> list[str]:
    problems = []
    if path.stat().st_size != release.size:
        problems.append("el tamaño no coincide con el manifiesto")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) != release.files or sum(info.file_size for info in infos) != release.unpacked_size:
            problems.append("el número o el tamaño de los archivos no coincide con el manifiesto")
        names = {info.filename for info in infos}
        if iris_install.STAGING_MANIFEST not in names:
            return problems + ["no contiene iris-staging.json"]
        staging = hashlib.sha256(archive.read(iris_install.STAGING_MANIFEST)).hexdigest()
        if staging != release.staging_sha256:
            problems.append("iris-staging.json no corresponde a la versión del manifiesto")
        if iris_install.CHANNEL not in names:
            return problems + ["no contiene channel.json: la versión no recibiría actualizaciones"]
        try:
            document = strict_json(archive.read(iris_install.CHANNEL), 64 * 1024)
            trusted = parse_trusted_keys(document["keys"])
        except (SignatureError, KeyError, TypeError) as exc:
            return problems + [f"channel.json no es válido: {exc}"]
        if document.get("test") is not False:
            problems.append("channel.json es de prueba (test: true)")
        if document.get("url") != url:
            problems.append(f"channel.json apunta a {document.get('url')!r}, no a {url!r}")
        if document.get("channel") != channel:
            problems.append(f"channel.json es del canal {document.get('channel')!r}, no de {channel!r}")
        if set(trusted) != set(production):
            problems.append(f"channel.json confía en {sorted(trusted)}; se esperaban solo {sorted(production)}")
        leaked = private_keys_in(archive)
        if leaked:
            problems.append(f"contiene claves privadas: {leaked[:5]}")
    return problems


def check(manifest_path: Path, channel_url: str, key_paths: list[Path], packages: list[Path],
          previous: Path | None = None, first_publication: bool = False, min_days: int = 7,
          remote: bool = False, client=None) -> dict:
    """Gate before publishing a real channel; ``ok`` only when every item passes."""
    if (previous is None) == (not first_publication):
        raise SystemExit("Use --previous <published manifest> or --first-publication (only one).")
    results: list[dict] = []

    def result(name: str, ok: bool, detail: str) -> None:
        results.append({"check": name, "ok": ok, "detail": detail})

    def report() -> dict:
        return {"ok": all(item["ok"] for item in results), "checks": results}

    production = _production(key_paths)
    raw = manifest_path.read_bytes()
    try:
        payload = verify_envelope(raw, production)
    except SignatureError as exc:
        result("firma", False, str(exc))
        return report()
    signers = [item["keyid"] for item in strict_json(raw)["signatures"]]
    foreign = [keyid for keyid in signers if keyid not in production]
    result("firma", not foreign, f"firmas de claves ajenas: {foreign}" if foreign
           else f"firmado solo con claves de producción {signers}")
    document = strict_json(payload)
    name = str(document.get("channel"))
    try:
        updates.channel_url(channel_url, test=False)
        parsed = updates.parse_manifest(payload, updates.Channel(name, channel_url, production, False))
    except updates.UpdateError as exc:
        result("canal real", False, str(exc))
        return report()
    result("canal real", bool(parsed["releases"]),
           f"canal {name} en {channel_url}; {len(parsed['releases'])} versiones con URLs HTTPS públicas"
           if parsed["releases"] else "el manifiesto no lista versiones para Windows x86_64")
    expires = datetime.fromisoformat(document["expires_at"].replace("Z", "+00:00"))
    days = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
    result("vigencia", days >= min_days, f"vence en {days:.1f} días ({document['expires_at']})")
    if previous is not None:
        try:
            old = strict_json(verify_envelope(previous.read_bytes(), production))
        except SignatureError as exc:
            result("secuencia", False, f"el manifiesto anterior no está firmado por las claves de producción: {exc}")
        else:
            result("secuencia", old.get("channel") == name and parsed["sequence"] > old.get("sequence", 0),
                   f"{parsed['sequence']} tras {old.get('sequence')} del canal {old.get('channel')}")
    else:
        result("secuencia", True, f"{parsed['sequence']}; primera publicación del canal")
    by_hash = {_sha256(path): path for path in packages}
    for release in parsed["releases"]:
        label = f"paquete Iris {release.iris} ({release.id})"
        path, downloaded = by_hash.get(release.sha256), None
        if path is None and remote:
            downloaded = Path(tempfile.mkdtemp(prefix="iris-check-"))
            path = downloaded / f"{release.id}.zip"
            try:
                updates.download(release, path, client=client or anonymous_client())
            except updates.UpdateError as exc:
                shutil.rmtree(downloaded, ignore_errors=True)
                result(label, False, f"no se pudo descargar sin autenticación desde {release.url}: {exc}")
                continue
        if path is None:
            result(label, False, "falta el paquete local con ese hash; no se puede comprobar")
            continue
        try:
            problems = _package_problems(path, release, name, channel_url, production)
        finally:
            if downloaded:
                shutil.rmtree(downloaded, ignore_errors=True)
        source = "descargado sin autenticación" if downloaded else path.name
        result(label, not problems, "; ".join(problems) if problems else
               f"{source}: hash y tamaño exactos, canal real con test: false, solo claves de producción, "
               "sin claves privadas")
    return report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("keygen")
    key.add_argument("--private", type=Path, required=True)
    key.add_argument("--public", type=Path, required=True)
    key.add_argument("--protect", action="store_true", help="encrypt with Windows DPAPI (production keys)")
    backup = commands.add_parser("backup-key")
    backup.add_argument("--private", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    pack = commands.add_parser("package")
    pack.add_argument("--staging", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--minimum-from")
    pack.add_argument("--notes", default="")
    sign = commands.add_parser("manifest")
    sign.add_argument("--channel", default="stable")
    sign.add_argument("--sequence", type=int, required=True)
    sign.add_argument("--private", type=Path, required=True)
    sign.add_argument("--release", action="append", default=[])
    sign.add_argument("--output", type=Path, required=True)
    sign.add_argument("--expires-days", type=int, default=30)
    sign.add_argument("--test", action="store_true", help="test channel: allows loopback HTTP package URLs")
    again = commands.add_parser("renew")
    again.add_argument("--previous", type=Path, required=True)
    again.add_argument("--key", type=Path, action="append", required=True)
    again.add_argument("--private", type=Path, required=True)
    again.add_argument("--output", type=Path, required=True)
    again.add_argument("--expires-days", type=int, default=90)
    verify = commands.add_parser("verify-key")
    verify.add_argument("--private", type=Path, required=True)
    verify.add_argument("--key", type=Path, action="append", required=True)
    gate = commands.add_parser("check")
    gate.add_argument("--manifest", type=Path, required=True)
    gate.add_argument("--channel-url", required=True)
    gate.add_argument("--key", type=Path, action="append", required=True)
    gate.add_argument("--package", type=Path, action="append", default=[])
    gate.add_argument("--previous", type=Path)
    gate.add_argument("--first-publication", action="store_true")
    gate.add_argument("--min-days", type=int, default=7)
    gate.add_argument("--remote", action="store_true", help="download packages without a local copy anonymously")
    args = parser.parse_args(argv)
    if args.command == "keygen":
        result = keygen(args.private, args.public, args.protect)
    elif args.command == "backup-key":
        result = backup_key(args.private, args.output)
    elif args.command == "package":
        result = package(args.staging, args.output, args.minimum_from, args.notes)
    elif args.command == "manifest":
        result = manifest(args.channel, args.sequence, args.private, args.release, args.output, args.expires_days,
                          args.test)
    elif args.command == "renew":
        result = renew(args.previous, args.key, args.private, args.output, args.expires_days)
    elif args.command == "verify-key":
        result = verify_key(args.private, args.key)
    else:
        result = check(args.manifest, args.channel_url, args.key, args.package, args.previous,
                       args.first_publication, args.min_days, args.remote)
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 0 if result["ok"] else 1
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
