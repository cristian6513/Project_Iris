"""Publish the manifest of an Iris update channel on GitHub Releases (A08.8).

Two uses, both run by the release owner on the PC that holds the production
key, with their own ``gh`` session:

- **renewal** (no ``--release``): a manifest expires (``expires_at``), so a
  channel without new versions is renewed before that date; the same releases
  are signed again with the next sequence and a new expiry;
- **new versions** (``--release <package.zip>=<public URL>``, repeatable): the
  manifest lists exactly those packages, which must already be uploaded.

    python scripts/publish_update_channel.py --channel-url <published channel URL> --repo <owner/repo>
        --tag <release tag> --key <production key .json> [...] --private <protected key>
        --gh <gh.exe> --work <folder for signed manifests> [--release <zip>=<URL> ...]
        [--expires-days 90] [--dry-run]

Every step must pass before the next; nothing is published before step 4:

1. download the published manifest anonymously, as Iris does (HTTPS, GitHub
   redirects only), and verify it with the production keys;
2. sign with sequence + 1: the same releases (``build_update_package.renew``)
   or the given packages (``build_update_package.manifest``);
3. mandatory ``check --previous --remote``: each package is downloaded
   anonymously and verified completely;
4. publish safely: upload the new manifest under its own name, read it back
   anonymously, then rename the old ``beta.json`` to ``beta-<old sequence>.json``
   and the new one to ``beta.json`` (if that fails the old name is restored);
5. download the channel anonymously again until it serves the new manifest.

``--dry-run`` stops after step 3.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_update_package as tooling  # noqa: E402

from core import iris_updates as updates  # noqa: E402
from core.update_signing import SignatureError, strict_json, verify_envelope  # noqa: E402


class RenewalError(SystemExit):
    """Stops the renewal with a message; nothing after the failing step runs."""


def say(message: str) -> None:
    print(message, flush=True)


def fetch_anonymous(url: str) -> bytes:
    """What anyone downloads: no credentials, GitHub redirects only."""
    with tooling.anonymous_client() as client, updates._get(client, url, test=False, what="el canal") as response:
        if response.status_code != 200:
            raise RenewalError(f"{url} respondió HTTP {response.status_code}.")
        data = b""
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) > updates.MAX_MANIFEST:
                raise RenewalError(f"{url} es demasiado grande para un manifiesto.")
        return data


def gh_runner(gh: Path) -> Callable[[list[str]], str]:
    def run(arguments: list[str]) -> str:
        result = subprocess.run([str(gh), *arguments], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=600)
        if result.returncode != 0:
            raise RenewalError(f"gh {' '.join(arguments[:3])} falló: {(result.stderr or result.stdout).strip()[-800:]}")
        return result.stdout

    return run


def assets(gh, repo: str, tag: str) -> dict[str, int]:
    release = json.loads(gh(["api", f"repos/{repo}/releases/tags/{tag}"]))
    return {asset["name"]: asset["id"] for asset in release.get("assets", [])}


def wait_until_served(fetch, url: str, expected: bytes, seconds: float = 180, step: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if fetch(url) == expected:
                return True
        except (SystemExit, updates.UpdateError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


def publish_safely(gh, fetch, repo: str, tag: str, current: str, new_file: Path, old_sequence: int,
                   wait_seconds: float = 180) -> str:
    """Replace ``current`` in the release by ``new_file`` without ever serving a partial file."""
    stem, suffix = Path(current).stem, Path(current).suffix
    staged, archive = new_file.name, f"{stem}-{old_sequence:04d}{suffix}"
    found = assets(gh, repo, tag)
    if current not in found:
        raise RenewalError(f"El release {tag} no tiene {current}; no se publicó nada.")
    for name in (staged, archive):
        if name in found:
            raise RenewalError(f"El release {tag} ya tiene {name} (¿una renovación anterior a medias?). "
                               "Revísalo antes de seguir; no se publicó nada.")
    expected = new_file.read_bytes()
    gh(["release", "upload", tag, str(new_file), "--repo", repo])
    staged_url = f"https://github.com/{repo}/releases/download/{tag}/{staged}"
    if not wait_until_served(fetch, staged_url, expected, wait_seconds):
        raise RenewalError(f"{staged} se subió pero no se pudo leer igual sin autenticación; {current} no se tocó. "
                           f"Borra {staged} del release antes de reintentar.")
    found = assets(gh, repo, tag)
    gh(["api", "-X", "PATCH", f"repos/{repo}/releases/assets/{found[current]}", "-f", f"name={archive}"])
    try:
        gh(["api", "-X", "PATCH", f"repos/{repo}/releases/assets/{found[staged]}", "-f", f"name={current}"])
    except SystemExit:
        gh(["api", "-X", "PATCH", f"repos/{repo}/releases/assets/{found[current]}", "-f", f"name={current}"])
        raise
    return archive


def renew_channel(args, gh=None, fetch=fetch_anonymous) -> dict:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    production = tooling._production(args.key)
    current = Path(args.channel_url.rsplit("/", 1)[-1]).name

    say("1/5 Leyendo el canal publicado sin autenticación…")
    raw = fetch(args.channel_url)
    try:
        old = strict_json(verify_envelope(raw, production))
    except SignatureError as exc:
        raise RenewalError(f"El manifiesto publicado no está firmado por la clave de producción: {exc}") from exc
    sequence, stem, suffix = old["sequence"], Path(current).stem, Path(current).suffix
    published = work / f"{stem}-{sequence:04d}.publicado{suffix}"
    if published.exists() and published.read_bytes() != raw:
        raise RenewalError(f"{published.name} ya existe con otro contenido; revísalo antes de seguir.")
    published.write_bytes(raw)
    say(f"    canal {old['channel']}, secuencia {sequence}, vence {old['expires_at']}")

    new_file = work / f"{stem}-{sequence + 1:04d}{suffix}"
    if new_file.exists():  # signed by an earlier run that did not publish it
        new_file.rename(new_file.with_name(f"{new_file.name}.descartado-{datetime.now():%Y%m%d-%H%M%S}"))
    releases = getattr(args, "release", None) or []
    say(f"2/5 Firmando la secuencia {sequence + 1} con vencimiento de {args.expires_days} días"
        f"{' y ' + str(len(releases)) + ' versión(es) nueva(s)' if releases else ', mismas versiones'}…")
    if releases:
        document = tooling.manifest(old["channel"], sequence + 1, args.private, releases, new_file,
                                    args.expires_days)
    else:
        document = tooling.renew(published, args.key, args.private, new_file, args.expires_days)

    say("3/5 Comprobación obligatoria (check), con cada paquete descargado sin autenticación…")
    report = tooling.check(new_file, args.channel_url, args.key, [], previous=published, remote=True)
    (work / f"{new_file.stem}.check.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), "utf-8")
    for item in report["checks"]:
        say(f"    {'ok   ' if item['ok'] else 'FALLO'} {item['check']}: {item['detail']}")
    if not report["ok"]:
        raise RenewalError("La comprobación falló; no se publicó nada.")
    if args.dry_run:
        say(f"Prueba en seco: {new_file.name} firmado y comprobado; no se publicó nada.")
        return {"published": False, "sequence": document["sequence"], "file": str(new_file)}

    say("4/5 Publicando: subida con otro nombre, lectura sin autenticación e intercambio de nombres…")
    gh = gh or gh_runner(Path(args.gh))
    archive = publish_safely(gh, fetch, args.repo, args.tag, current, new_file, sequence)
    say(f"    el anterior quedó como {archive}")

    say("5/5 Verificando lo publicado sin autenticación…")
    if not wait_until_served(fetch, args.channel_url, new_file.read_bytes()):
        raise RenewalError("GitHub todavía no sirve el manifiesto nuevo; vuelve a comprobarlo en unos minutos.")
    say(f"Listo: secuencia {document['sequence']}, vence {document['expires_at']}.")
    return {"published": True, "sequence": document["sequence"], "expires_at": document["expires_at"],
            "archive": archive, "file": str(new_file)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel-url", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--key", type=Path, action="append", required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--gh", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--expires-days", type=int, default=90)
    parser.add_argument("--release", action="append", default=[],
                        help="<package.zip>=<public URL> of an already uploaded package (new versions)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        renew_channel(args)
    except RenewalError as exc:
        say(f"ERROR: {exc.code}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
