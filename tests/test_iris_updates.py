"""A08.8: joint Iris + Hermes channel, versioned installation, activation and rollback."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from core import iris_install as inst
from core import iris_updates as upd
from core import update_signing as sig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_update_package as tooling  # noqa: E402

IRIS_REV, HERMES_REV = "a" * 40, "b" * 40


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_version(folder: Path, *, iris: str = "1.0.0", revision: str = IRIS_REV, hermes: str = "0.21.4",
                 profile: int = 1, channel: dict | None = None, marker: str = "") -> Path:
    """A small but complete version folder with the A08.7 manifest chain."""
    runtime_files = {
        "runtimes/iris/python.exe": b"iris python" + marker.encode(),
        "runtimes/iris/Lib/site-packages/core/version.py": f'__version__ = "{iris}"\n'.encode(),
        "runtimes/hermes/python.exe": b"hermes python",
        "hermes/iris_cli.py": b"cli",
        "runtime-pin.json": b'{"pin": 1}',
    }
    for name, data in runtime_files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    inventory = {"schema": 1, "files": [{"path": name, "size": len(data), "sha256": _sha(data), "component": "x"}
                                        for name, data in sorted(runtime_files.items())]}
    inventory_raw = json.dumps(inventory).encode()
    (folder / inst.INVENTORY).write_bytes(inventory_raw)
    descriptor = {"schema_version": 2, "mode": "packaged",
                  "iris": {"root": "runtimes/iris/Lib/site-packages", "python": "runtimes/iris/python.exe",
                           "assets": "runtimes/iris/Lib/site-packages", "revision": revision},
                  "hermes": {"root": "hermes", "python": "runtimes/hermes/python.exe", "cli": "hermes/iris_cli.py",
                             "version": hermes, "revision": HERMES_REV, "metadata": "runtime-pin.json"}}
    staged = {"Iris.exe": b"MZ launcher " + iris.encode(), "runtime.json": json.dumps(descriptor).encode(),
              "LEEME.txt": b"readme"}
    if channel is not None:
        staged["channel.json"] = json.dumps(channel).encode()
    for name, data in staged.items():
        (folder / name).write_bytes(data)
    manifest = {
        "schema": 1, "component": "iris-windows-staging", "platform": "windows", "architecture": "x86_64",
        "iris": {"version": iris, "revision": revision, "wheel_sha256": revision + "0" * 24},
        "hermes": {"version": hermes, "revision": HERMES_REV},
        "runtime": {"inventory_sha256": _sha(inventory_raw), "pin_sha256": _sha(runtime_files["runtime-pin.json"])},
        "install": {"max_relative_path": 60, "max_root_length": 198},
        "profile": {"format_version": profile},
        "files": [{"path": name, "size": len(data), "sha256": _sha(data)} for name, data in sorted(staged.items())],
    }
    (folder / inst.STAGING_MANIFEST).write_text(json.dumps(manifest), "utf-8")
    return folder


@pytest.fixture
def keypair():
    pem, entry = sig.generate_private_key()
    return sig.load_private_key(pem), entry


def channel_doc(entry, url="http://127.0.0.1:9999/stable.json", test=True):
    return {"schema": 1, "channel": "stable", "url": url, "keys": [entry], "test": test}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    iris_home, hermes_home = tmp_path / "perfil iris", tmp_path / "perfil hermes"
    iris_home.mkdir()
    hermes_home.mkdir()
    monkeypatch.setenv("IRIS_HOME", str(iris_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return SimpleNamespace(iris=iris_home, hermes=hermes_home)


@pytest.fixture
def installed(tmp_path, keypair):
    """A versioned installation of 1.0.0 created exactly as the installer will."""
    key, entry = keypair
    staging = make_version(tmp_path / "staging-1.0.0", channel=channel_doc(entry))
    root = tmp_path / "Iris OS ñ"
    active = inst.create_install(staging, root)
    return SimpleNamespace(install=inst.Install(root), active=active, key=key, entry=entry, tmp=tmp_path)


# ── Signatures ──────────────────────────────────────────────────────────────

def test_envelope_verifies_with_trusted_key_and_rejects_everything_else(keypair):
    key, entry = keypair
    trusted = sig.parse_trusted_keys([entry])
    envelope = sig.sign_payload(b'{"x": 1}', [key])
    assert sig.verify_envelope(envelope, trusted) == b'{"x": 1}'
    other, other_entry = sig.generate_private_key()
    with pytest.raises(sig.SignatureError):
        sig.verify_envelope(sig.sign_payload(b'{"x": 1}', [sig.load_private_key(other)]), trusted)
    tampered = json.loads(envelope)
    tampered["payload"] = base64.b64encode(b'{"x": 2}').decode()
    with pytest.raises(sig.SignatureError):
        sig.verify_envelope(json.dumps(tampered).encode(), trusted)
    for broken in (b"", b"[]", b'{"schema": 1}', b"\xef\xbb\xbf{}", b'{"schema": 1, "schema": 1}',
                   json.dumps({"schema": 2, "payload": "", "signatures": []}).encode()):
        with pytest.raises(sig.SignatureError):
            sig.verify_envelope(broken, trusted)


def test_trusted_key_entries_must_match_their_identifier(keypair):
    _, entry = keypair
    with pytest.raises(sig.SignatureError):
        sig.parse_trusted_keys([{**entry, "keyid": "0" * 16}])
    with pytest.raises(sig.SignatureError):
        sig.parse_trusted_keys([entry, entry])
    with pytest.raises(sig.SignatureError):
        sig.parse_trusted_keys([])


# ── Channel and manifest ────────────────────────────────────────────────────

def test_channel_requires_https_except_loopback_test_channels(tmp_path, keypair):
    _, entry = keypair
    for url, test, ok in [("https://updates.example/stable.json", False, True),
                          ("http://127.0.0.1:8080/stable.json", True, True),
                          ("http://127.0.0.1:8080/stable.json", False, False),
                          ("http://updates.example/stable.json", True, False),
                          ("https://user:pw@updates.example/x.json", False, False),
                          ("https://127.0.0.1/stable.json", False, False),
                          ("https://localhost./stable.json", False, False),
                          ("https://iris.localhost/stable.json", False, False),
                          ("https://10.0.0.5/stable.json", False, False),
                          ("https://[::1]/stable.json", False, False),
                          ("https://updates/stable.json", False, False),
                          ("https://127.0.0.1/stable.json", True, True),
                          ("file:///C:/stable.json", True, False)]:
        (tmp_path / "channel.json").write_text(json.dumps(channel_doc(entry, url, test)))
        if ok:
            assert upd.load_channel(tmp_path).url == url
        else:
            with pytest.raises(upd.UpdateError):
                upd.load_channel(tmp_path)
    (tmp_path / "channel.json").unlink()
    assert upd.load_channel(tmp_path) is None


def _release_entry(version="1.0.1", **overrides):
    staging_sha = _sha(version.encode())
    entry = {"id": staging_sha[:8], "platform": "windows", "architecture": "x86_64",
             "iris": {"version": version, "revision": "c" * 40}, "hermes": {"version": "0.21.4", "revision": HERMES_REV},
             "profile_format": 1, "minimum_from": "1.0.0", "max_relative_path": 60, "staging_sha256": staging_sha,
             "package": {"url": "http://127.0.0.1:9999/p.zip", "size": 10, "sha256": "d" * 64,
                         "unpacked_size": 20, "files": 3}, "notes": ""}
    entry.update(overrides)
    return entry


def _payload(releases, *, sequence=1, channel="stable", product="iris-os", expires_in=30):
    now = datetime.now(timezone.utc)
    return json.dumps({"schema": 1, "product": product, "channel": channel, "sequence": sequence,
                       "published_at": now.isoformat(), "expires_at": (now + timedelta(days=expires_in)).isoformat(),
                       "releases": releases}).encode()


def _channel(entry, test=True):
    return upd.Channel("stable", "http://127.0.0.1:9999/stable.json", sig.parse_trusted_keys([entry]), test)


def test_manifest_validation_rejects_wrong_product_channel_expiry_and_fields(keypair):
    _, entry = keypair
    channel = _channel(entry)
    assert [r.iris for r in upd.parse_manifest(_payload([_release_entry()]), channel)["releases"]] == ["1.0.1"]
    other_platform = _release_entry(platform="macos")
    assert upd.parse_manifest(_payload([other_platform]), channel)["releases"] == []
    for payload in (_payload([], product="other"), _payload([], channel="beta"), _payload([], expires_in=-1),
                    _payload([], sequence=0), _payload([_release_entry(id="00000000")]),
                    _payload([_release_entry(staging_sha256="z" * 64)]),
                    _payload([_release_entry(package={"url": "http://evil.example/p.zip", "size": 1, "sha256": "d" * 64,
                                                      "unpacked_size": 1, "files": 1})]),
                    _payload([_release_entry(package={"url": "http://127.0.0.1/p.zip", "size": 0, "sha256": "d" * 64,
                                                      "unpacked_size": 1, "files": 1})]),
                    _payload([_release_entry(iris={"version": "not a version", "revision": "c" * 40})]),
                    _payload([{"id": "x"}])):
        with pytest.raises(upd.UpdateError):
            upd.parse_manifest(payload, channel)


def test_fetch_manifest_checks_signature_and_size(keypair):
    key, entry = keypair
    channel = _channel(entry)
    good = sig.sign_payload(_payload([_release_entry()]), [key])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=good)))
    assert upd.fetch_manifest(channel, client)["sequence"] == 1
    other = sig.sign_payload(_payload([_release_entry()]), [sig.load_private_key(sig.generate_private_key()[0])])
    for response in (httpx.Response(200, content=other), httpx.Response(404),
                     httpx.Response(200, content=b"x" * (upd.MAX_MANIFEST + 1))):
        client = httpx.Client(transport=httpx.MockTransport(lambda request, r=response: r))
        with pytest.raises(upd.UpdateError):
            upd.fetch_manifest(channel, client)

    def offline(request):
        raise httpx.ConnectError("offline")

    with pytest.raises(upd.UpdateError, match="conexión"):
        upd.fetch_manifest(channel, httpx.Client(transport=httpx.MockTransport(offline)))


GITHUB_CHANNEL = "https://github.com/o/iris-os-releases/releases/download/channel-beta/beta.json"
GITHUB_ASSET = "https://release-assets.githubusercontent.com/github-production-release-asset/1/beta.json?sig=x"


def _hops(*steps, body=b""):
    """(url, Location) pairs answer 302; any other URL answers 200 with ``body``."""
    routes, seen = dict(steps), []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) not in routes:
            return httpx.Response(200, content=body)
        location = routes[str(request.url)]
        return httpx.Response(302, headers={} if location is None else {"location": location})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_redirects_follow_only_https_github_hosts_a_few_hops(keypair):
    key, entry = keypair
    channel = upd.Channel("stable", GITHUB_CHANNEL, sig.parse_trusted_keys([entry]), False)
    package = {"url": "https://github.com/o/iris-os-releases/releases/download/v0.9.1/iris.zip", "size": 10,
               "sha256": "d" * 64, "unpacked_size": 20, "files": 3}
    signed = sig.sign_payload(_payload([_release_entry(package=package)]), [key])
    client, seen = _hops((GITHUB_CHANNEL, GITHUB_ASSET), body=signed)
    assert [r.url for r in upd.fetch_manifest(channel, client)["releases"]] == [package["url"]]
    assert seen == [GITHUB_CHANNEL, GITHUB_ASSET]
    client, seen = _hops((GITHUB_CHANNEL, "/o/iris-os-releases/releases/download/channel-beta/b2.json"),
                         ("https://github.com/o/iris-os-releases/releases/download/channel-beta/b2.json",
                          "https://objects.githubusercontent.com/x"), body=signed)
    assert upd.fetch_manifest(channel, client)["sequence"] == 1 and seen[-1] == "https://objects.githubusercontent.com/x"
    for location in ("https://evil.example/beta.json", "http://release-assets.githubusercontent.com/x",
                     "https://127.0.0.1/x", "https://10.1.2.3/x", "https://[::1]/x", "https://localhost/x",
                     "https://user:pw@github.com/x", "file:///C:/beta.json", "https://github.com.evil.example/x", None):
        client, seen = _hops((GITHUB_CHANNEL, location), body=signed)
        with pytest.raises(upd.UpdateError):
            upd.fetch_manifest(channel, client)
        assert seen == [GITHUB_CHANNEL], location
    loop = [(f"https://github.com/hop/{n}", f"https://github.com/hop/{n + 1}") for n in range(upd.MAX_REDIRECTS + 1)]
    client, seen = _hops((GITHUB_CHANNEL, "https://github.com/hop/0"), *loop, body=signed)
    with pytest.raises(upd.UpdateError, match="redirecciones"):
        upd.fetch_manifest(channel, client)
    assert len(seen) == upd.MAX_REDIRECTS + 1
    test_channel = _channel(entry)
    client, _ = _hops((test_channel.url, "http://127.0.0.1:9999/moved.json"),
                      body=sig.sign_payload(_payload([_release_entry()]), [key]))
    assert upd.fetch_manifest(test_channel, client)["sequence"] == 1


def test_download_follows_github_redirects_and_still_checks_the_hash(installed, tmp_path):
    import dataclasses

    release, archive = _publish(installed)
    real = dataclasses.replace(release, url="https://github.com/o/iris-os-releases/releases/download/v1/p.zip",
                               test=False)
    asset = "https://release-assets.githubusercontent.com/github-production-release-asset/2/p.zip?sig=y"
    client, seen = _hops((real.url, asset), body=archive.read_bytes())
    upd.download(real, tmp_path / "ok.zip", client=client)
    assert (tmp_path / "ok.zip").read_bytes() == archive.read_bytes() and seen == [real.url, asset]
    for location, body in (("https://evil.example/p.zip", archive.read_bytes()), (asset, b"tampered")):
        client, _ = _hops((real.url, location), body=body)
        with pytest.raises(upd.UpdateError):
            upd.download(real, tmp_path / "refused.zip", client=client)
        assert not (tmp_path / "refused.zip").exists() and not (tmp_path / "refused.zip.part").exists()


def test_choose_release_offers_only_newer_compatible_versions(tmp_path, keypair):
    _, entry = keypair
    manifest = upd.parse_manifest(_payload([
        _release_entry("0.9.0"), _release_entry("1.0.0"), _release_entry("1.0.1"),
        _release_entry("1.2.0", minimum_from="1.1.0"), _release_entry("1.0.2", profile_format=1),
    ]), _channel(entry))
    release, _ = upd.choose_release(manifest, "1.0.0", 1, tmp_path)
    assert release.iris == "1.0.2"
    release, reason = upd.choose_release(manifest, "1.0.0", 2, tmp_path)
    assert release is None and "formato" in reason
    deep = tmp_path / ("x" * 200)
    release, reason = upd.choose_release(manifest, "1.0.0", 1, deep)
    assert release is None and "ruta" in reason
    assert upd.choose_release(manifest, "1.2.0", 1, tmp_path)[0] is None


# ── Package extraction ─────────────────────────────────────────────────────

def _release_for(files: dict[str, bytes]) -> upd.Release:
    return upd.Release("00000000", "1.0.1", "c" * 40, "0.21.4", HERMES_REV, 1, "1.0.0", 60, "0" * 64,
                       "https://x/p.zip", 1, "d" * 64, sum(map(len, files.values())), len(files), "")


def _raw_archive(path: Path, stored: str, data: bytes = b"data", *, flags: int = 0) -> Path:
    """An archive whose stored name/flags bypass zipfile's own normalisation."""
    placeholder = "p" * len(stored.encode("utf-8"))
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(placeholder, data)
    raw = bytearray(path.read_bytes().replace(placeholder.encode(), stored.encode("utf-8")))
    if flags:
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = raw.find(signature)
            raw[start + offset] |= flags
    path.write_bytes(bytes(raw))
    return path


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "a\\b.txt", "C:/x.txt", "a/../../x", "a/./b",
                                  "CON", "dir/NUL.txt", "trailing. ", "dir./x", "a//b", "bad|name", "x:stream"])
def test_extraction_rejects_unsafe_names(tmp_path, name):
    archive = _raw_archive(tmp_path / "p.zip", name)
    with pytest.raises(upd.UpdateError):
        upd.safe_extract(archive, tmp_path / "out", _release_for({"x": b"data"}))
    assert not (tmp_path / "out").exists()


def test_extraction_rejects_links_duplicates_encryption_and_size_mismatch(tmp_path):
    def build(entries):
        archive = tmp_path / f"p{len(list(tmp_path.glob('*.zip')))}.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for info, data in entries:
                package.writestr(info, data)
        return archive

    link = zipfile.ZipInfo("link")
    link.external_attr = 0o120777 << 16
    cases = [(build([(link, b"target")]), {"link": b"target"}),
             (build([(zipfile.ZipInfo("A.txt"), b"1"), (zipfile.ZipInfo("a.txt"), b"2")]), {"A.txt": b"1", "a.txt": b"2"}),
             (_raw_archive(tmp_path / "encrypted.zip", "secret", b"x", flags=0x1), {"secret": b"x"}),
             (build([(zipfile.ZipInfo("x"), b"data")]), {"x": b"dat"}),
             (build([(zipfile.ZipInfo("x"), b"data"), (zipfile.ZipInfo("y"), b"")]), {"x": b"data"})]
    for archive, declared in cases:
        with pytest.raises(upd.UpdateError):
            upd.safe_extract(archive, tmp_path / "out", _release_for(declared))
        assert not (tmp_path / "out").exists()


def test_extraction_writes_plain_files(tmp_path):
    archive = tmp_path / "p.zip"
    files = {"a/b/c.txt": b"hello", "top.bin": b"\0\1"}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("a/", b"")
        for name, data in files.items():
            package.writestr(name, data)
    upd.safe_extract(archive, tmp_path / "out", _release_for(files))
    assert (tmp_path / "out/a/b/c.txt").read_bytes() == b"hello"


# ── Version folders and installation layout ────────────────────────────────

def test_version_folder_verification_detects_any_change(tmp_path):
    folder = make_version(tmp_path / "v")
    info = inst.verify_version(folder)
    assert info["iris"] == "1.0.0" and info["profile_format"] == 1 and len(info["id"]) == 8
    with pytest.raises(inst.InstallError):
        inst.verify_version(folder, "0" * 64)
    (folder / "extra.txt").write_text("x")
    with pytest.raises(inst.InstallError, match="sobran"):
        inst.verify_version(folder)
    (folder / "extra.txt").unlink()
    (folder / "hermes/iris_cli.py").write_bytes(b"changed")
    with pytest.raises(inst.InstallError, match="alterado"):
        inst.verify_version(folder)


def test_quick_verification_keeps_structure_and_size_checks(tmp_path):
    folder = make_version(tmp_path / "v")
    (folder / "hermes/iris_cli.py").write_bytes(b"CLI")  # same size, other bytes
    assert inst.verify_version(folder, hashes=False)["iris"] == "1.0.0"
    with pytest.raises(inst.InstallError, match="alterado"):
        inst.verify_version(folder)
    (folder / "hermes/iris_cli.py").write_bytes(b"longer")
    with pytest.raises(inst.InstallError, match="alterado"):
        inst.verify_version(folder, hashes=False)
    (folder / "hermes/iris_cli.py").unlink()
    with pytest.raises(inst.InstallError, match="faltan"):
        inst.verify_version(folder, hashes=False)


def test_rollback_check_still_hashes_the_programs(tmp_path):
    folder = make_version(tmp_path / "v")
    launcher = folder / "Iris.exe"
    launcher.write_bytes(launcher.read_bytes().upper())  # same size, other program
    assert inst.verify_version(folder, hashes=False)["id"]
    with pytest.raises(inst.InstallError, match="Iris.exe"):
        inst.verify_version(folder, hashes=False, executables=True)


def test_version_folder_rejects_descriptor_mismatch(tmp_path):
    folder = make_version(tmp_path / "v")
    manifest = json.loads((folder / inst.STAGING_MANIFEST).read_text())
    descriptor = json.loads((folder / "runtime.json").read_text())
    descriptor["hermes"]["version"] = "0.22.0"
    raw = json.dumps(descriptor).encode()
    (folder / "runtime.json").write_bytes(raw)
    for item in manifest["files"]:
        if item["path"] == "runtime.json":
            item.update(size=len(raw), sha256=_sha(raw))
    (folder / inst.STAGING_MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(inst.InstallError, match="descriptor"):
        inst.verify_version(folder)


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_version_folder_rejects_junctions(tmp_path):
    import subprocess

    folder = make_version(tmp_path / "v")
    outside = tmp_path / "outside"
    outside.mkdir()
    made = subprocess.run(["cmd", "/C", "mklink", "/J", str(folder / "linked"), str(outside)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot create a junction here")
    with pytest.raises(inst.InstallError, match="enlace"):
        inst.verify_version(folder)


def test_create_install_lays_out_a_versioned_installation(installed):
    install = installed.install
    state = inst.read_state(install)
    assert state["active"] == installed.active and state["confirmed"] is True and state["previous"] is None
    assert (install.root / "Iris.exe").read_bytes() == (install.folder(installed.active) / "Iris.exe").read_bytes()
    assert inst.verify_version(install.folder(installed.active))["id"] == installed.active
    with pytest.raises(inst.InstallError, match="no está vacía"):
        inst.create_install(installed.tmp / "staging-1.0.0", install.root)


def test_state_keeps_unknown_fields_and_rejects_invalid_documents(installed):
    install = installed.install
    state = inst.read_state(install)
    state["future"] = {"from": "a newer version"}
    inst.write_state(install, state)
    assert inst.read_state(install)["future"] == {"from": "a newer version"}
    for broken in ({**state, "active": "deadbeef"}, {**state, "schema": 2}, {**state, "confirmed": "yes"},
                   {**state, "pending": {"operation": "upgrade", "from": "x", "to": "y", "phase": "switched"}}):
        with pytest.raises(inst.InstallError):
            inst.write_state(install, broken)
    assert inst.read_state(install)["future"]


def test_find_install_only_for_a_packaged_version_folder(installed, tmp_path):
    folder = installed.install.folder(installed.active)
    assert inst.find_install(SimpleNamespace(packaged=True, base=folder)) == installed.install
    assert inst.find_install(SimpleNamespace(packaged=False, base=folder)) is None
    assert inst.find_install(SimpleNamespace(packaged=True, base=tmp_path / "staging-1.0.0")) is None


# ── Download and staging of a release ──────────────────────────────────────

def _publish(installed, version="1.0.1", **kwargs):
    staging = make_version(installed.tmp / f"staging-{version}", iris=version, revision="c" * 40,
                           channel=channel_doc(installed.entry), **kwargs)
    archive = installed.tmp / f"iris-{version}.zip"
    tooling.package(staging, archive)
    manifest = installed.tmp / "stable.json"
    pem = installed.tmp / "key.pem"
    if not pem.exists():
        from cryptography.hazmat.primitives import serialization

        pem.write_bytes(installed.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
    tooling.manifest("stable", 1, pem, [f"{archive}=http://127.0.0.1:9999/{archive.name}"], manifest, test=True)
    channel = upd.load_channel(installed.install.folder(installed.active))
    parsed = upd.parse_manifest(sig.verify_envelope(manifest.read_bytes(), channel.keys), channel)
    return parsed["releases"][0], archive


def _serving(archive: Path, *, cut: int | None = None):
    data = archive.read_bytes()

    def handler(request):
        if cut is not None:
            return httpx.Response(200, content=data[:cut])
        return httpx.Response(200, content=data)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_prepare_release_downloads_verifies_and_places_a_new_folder(installed, monkeypatch):
    monkeypatch.setattr(upd, "_self_check", Mock())
    release, archive = _publish(installed)
    phases = []
    folder = upd.prepare_release(installed.install, release, lambda phase, *_: phases.append(phase), _serving(archive))
    assert folder == installed.install.folder(release.id)
    assert inst.verify_version(folder)["iris"] == "1.0.1"
    assert {"downloading", "extracting", "verifying"} <= set(phases)
    assert not list(installed.install.updates.glob("*.zip")) and not list(installed.install.versions.glob("_*"))
    assert inst.read_state(installed.install)["active"] == installed.active  # nothing activated


def test_interrupted_or_altered_download_changes_nothing(installed, monkeypatch):
    monkeypatch.setattr(upd, "_self_check", Mock())
    release, archive = _publish(installed)
    with pytest.raises(upd.UpdateError):
        upd.prepare_release(installed.install, release, client=_serving(archive, cut=100))
    tampered = installed.tmp / "tampered.zip"
    data = bytearray(archive.read_bytes())
    data[-200] ^= 0xFF
    tampered.write_bytes(bytes(data))
    with pytest.raises(upd.UpdateError, match="no coincide"):
        upd.prepare_release(installed.install, release, client=_serving(tampered))
    assert not installed.install.folder(release.id).exists() and not list(installed.install.versions.glob("_*"))
    assert not list(installed.install.updates.glob("*.part")) and not list(installed.install.updates.glob("*.zip"))


def test_signed_package_with_mismatched_content_is_rejected(installed, monkeypatch):
    monkeypatch.setattr(upd, "_self_check", Mock())
    release, archive = _publish(installed)
    forged = upd.Release(**{**release.__dict__, "staging_sha256": "f" * 64, "id": "ffffffff"})
    with pytest.raises(upd.UpdateError, match="canal"):
        upd.prepare_release(installed.install, forged, client=_serving(archive))
    assert not installed.install.folder("ffffffff").exists()


def test_extraction_folder_has_the_length_of_the_final_version_folder(installed, monkeypatch):
    # The new version self-checks its descriptor while extracted; its A08.7
    # MAX_PATH budget must hold there exactly as in v/<id>.
    seen = []
    monkeypatch.setattr(upd, "_self_check", lambda folder, release: seen.append(folder))
    release, archive = _publish(installed)
    upd.prepare_release(installed.install, release, client=_serving(archive))
    assert len(seen[0].name) == len(release.id) and seen[0].parent == installed.install.versions
    assert not seen[0].exists()


def test_disk_space_is_checked_before_downloading(installed, monkeypatch):
    release, archive = _publish(installed)
    monkeypatch.setattr(upd.shutil, "disk_usage", lambda path: SimpleNamespace(free=10))
    client = Mock()
    with pytest.raises(upd.UpdateError, match="espacio"):
        upd.prepare_release(installed.install, release, client=client)
    client.stream.assert_not_called()


# ── Startup reconciliation and confirmation ────────────────────────────────

def _add_version(installed, version="1.0.1"):
    staging = make_version(installed.tmp / f"add-{version}", iris=version, revision="c" * 40,
                           channel=channel_doc(installed.entry))
    info = inst.verify_version(staging)
    target = installed.install.folder(info["id"])
    inst._copy_listed(staging, target)
    with inst.state_lock(installed.install):
        state = inst.read_state(installed.install)
        state["versions"][info["id"]] = inst.version_record(info, "update")
        inst.write_state(installed.install, state)
    return info["id"]


def test_interrupted_activation_before_switch_keeps_the_active_version(installed):
    install, own = installed.install, installed.active
    target = _add_version(installed)
    state = inst.read_state(install)
    state["pending"] = {"operation": "upgrade", "from": own, "to": target, "phase": "stopping",
                        "helper": {"pid": 999999, "created": 1.0}, "backup": None, "previous_before": None}
    inst.write_state(install, state)
    assert inst.startup_check(install, own) is None
    state = inst.read_state(install)
    assert state["pending"] is None and state["active"] == own
    assert state["last_activation"]["outcome"] == "interrupted"


def test_only_the_active_version_may_start(installed):
    target = _add_version(installed)
    message = inst.startup_check(installed.install, target)
    assert message and "no es la versión activa" in message


def test_unconfirmed_version_is_confirmed_once_ready(installed):
    install, own = installed.install, installed.active
    target = _add_version(installed)
    state = inst.read_state(install)
    state.update(active=target, previous=own, confirmed=False,
                 pending={"operation": "upgrade", "from": own, "to": target, "phase": "switched",
                          "helper": None, "backup": None, "previous_before": None})
    inst.write_state(install, state)
    install.trial_path.write_text(json.dumps({"target": target, "launches": 1}))
    assert inst.startup_check(install, target) is None
    assert inst.confirm_active(install, target) is True
    state = inst.read_state(install)
    assert state["confirmed"] and state["pending"] is None and not install.trial_path.exists()
    assert state["last_activation"]["outcome"] == "completed" and "1.0.1" in state["last_activation"]["message"]
    assert inst.confirm_active(install, target) is False


def test_launcher_fallback_after_failed_trials_takes_the_activation_back(installed):
    install, own = installed.install, installed.active
    target = _add_version(installed)
    state = inst.read_state(install)
    state.update(active=target, previous=own, confirmed=False,
                 pending={"operation": "upgrade", "from": own, "to": target, "phase": "switched",
                          "helper": None, "backup": None, "previous_before": None})
    inst.write_state(install, state)
    install.trial_path.write_text(json.dumps({"target": target, "launches": inst.TRIAL_LIMIT}))
    assert "no es la versión activa" in inst.startup_check(install, own)  # trials not exhausted
    install.trial_path.write_text(json.dumps({"target": target, "launches": inst.TRIAL_LIMIT + 1}))
    assert inst.startup_check(install, own) is None
    state = inst.read_state(install)
    assert state["active"] == own and state["confirmed"] and state["pending"] is None
    assert state["last_activation"]["outcome"] == "reverted"
    assert (state["last_activation"]["operation"], state["last_activation"]["to"]) == ("upgrade", target)


# ── Profile backup ─────────────────────────────────────────────────────────

def test_profile_backup_and_restore_move_newer_files_aside(profile):
    (profile.iris / "config.json").write_text('{"v": 1}')
    (profile.iris / ".profile").mkdir()
    (profile.iris / ".profile/format.json").write_text('{"schema_version": 1, "format_version": 1}')
    (profile.iris / "extras/kokoro").mkdir(parents=True)
    (profile.iris / "extras/kokoro/big.bin").write_bytes(b"x" * 10)
    (profile.hermes / "config.yaml").write_text("model: a\n")
    (profile.hermes / "logs").mkdir()
    (profile.hermes / "logs/gateway.log").write_text("log")
    backup = inst.backup_profile(profile.iris, profile.hermes, "aaaa-to-bbbb")
    record = json.loads((backup / "backup.json").read_text())
    assert set(record["files"]["iris"]) == {"config.json", ".profile/format.json"}
    assert set(record["files"]["hermes"]) == {"config.yaml"}
    (profile.iris / "config.json").write_text('{"v": 2}')
    (profile.iris / ".profile/format.json").write_text('{"schema_version": 1, "format_version": 2}')
    (profile.hermes / "new.db").write_text("migrated")
    replaced = inst.restore_profile(profile.iris, profile.hermes, backup)
    assert json.loads((profile.iris / "config.json").read_text()) == {"v": 1}
    assert "format_version\": 1" in (profile.iris / ".profile/format.json").read_text()
    assert not (profile.hermes / "new.db").exists() and (replaced / "hermes/new.db").read_text() == "migrated"
    assert (profile.iris / "extras/kokoro/big.bin").exists() and (profile.hermes / "logs/gateway.log").exists()


def test_corrupt_backup_is_not_restored(profile):
    (profile.iris / "config.json").write_text("1")
    backup = inst.backup_profile(profile.iris, profile.hermes, "x")
    (backup / "iris/config.json").write_text("tampered")
    (profile.iris / "config.json").write_text("2")
    with pytest.raises(inst.InstallError, match="dañada"):
        inst.restore_profile(profile.iris, profile.hermes, backup)
    assert (profile.iris / "config.json").read_text() == "2"


# ── Activation helper ──────────────────────────────────────────────────────

@pytest.fixture
def helper(installed, profile, monkeypatch):
    """Activation with the process and port side effects replaced by recorders."""
    target = _add_version(installed)
    install, own = installed.install, installed.active
    calls = []
    monkeypatch.setattr(inst, "own_version_id", lambda runtime=None: own)
    monkeypatch.setattr(upd, "_close_desktop", lambda install, version: calls.append(("close", version)))
    monkeypatch.setattr(upd, "_stop_own_services", lambda: calls.append(("stop", own)) or {})
    monkeypatch.setattr(upd, "_drain_version", lambda install, version: calls.append(("drain", version)))
    monkeypatch.setattr(upd, "_ports_free", lambda: calls.append(("ports", None)))
    monkeypatch.setattr(upd, "_stop_version_services", lambda install, version: calls.append(("stop", version)))
    monkeypatch.setattr(upd, "processes_of", lambda *a, **k: [])
    monkeypatch.setattr(upd.time, "sleep", lambda seconds: None)
    state = inst.read_state(install)
    state["pending"] = {"operation": "upgrade", "from": own, "to": target, "phase": "starting",
                        "started_at": inst.now(), "helper": None, "backup": None, "previous_before": None}
    inst.write_state(install, state)
    return SimpleNamespace(install=install, own=own, target=target, calls=calls, profile=profile)


def test_activation_switches_and_waits_for_the_new_version_to_confirm(helper, monkeypatch):
    def launch(install):
        helper.calls.append(("launch", inst.read_state(install)["active"]))
        inst.confirm_active(install, inst.read_state(install)["active"])

    monkeypatch.setattr(upd, "_launch", launch)
    assert upd.activate(helper.install, helper.target, "upgrade", confirm_timeout=5) == 0
    state = inst.read_state(helper.install)
    assert state["active"] == helper.target and state["previous"] == helper.own and state["confirmed"]
    assert state["pending"] is None and state["last_activation"]["outcome"] == "completed"
    assert [c[0] for c in helper.calls] == ["close", "stop", "drain", "ports", "launch"]
    # Same profile format and Hermes: no backup was needed.
    assert not (helper.profile.iris / ".profile" / "backups").exists()
    assert helper.install.folder(helper.own).is_dir()  # kept for rollback


def test_refusal_before_switching_restores_the_running_version(helper, monkeypatch):
    launches = []
    monkeypatch.setattr(upd, "_launch", lambda install: launches.append(inst.read_state(install)["active"]))
    monkeypatch.setattr(upd, "_ports_free", Mock(side_effect=upd._Refused("El puerto 18765 sigue ocupado")))
    assert upd.activate(helper.install, helper.target, "upgrade", confirm_timeout=5) == 1
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and state["pending"] is None and state["confirmed"]
    assert state["last_activation"]["outcome"] == "failed" and "18765" in state["last_activation"]["message"]
    assert launches == [helper.own]


def test_a_gateway_that_cannot_be_stopped_refuses_before_switching(helper, monkeypatch):
    # qa9: the new version refuses a gateway it cannot accredit, so switching
    # anyway only failed the trial minutes later. Keep the running version now.
    launches = []
    monkeypatch.setattr(upd, "_launch", lambda install: launches.append(inst.read_state(install)["active"]))
    monkeypatch.setattr(upd, "_stop_own_services", lambda: {"core": True, "gateway": False, "blocked": True})
    assert upd.activate(helper.install, helper.target, "upgrade", confirm_timeout=5) == 1
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and state["pending"] is None and state["confirmed"]
    assert "Gateway" in state["last_activation"]["message"]
    assert launches == [helper.own]


def test_incompatible_profile_writer_blocks_the_activation(helper, monkeypatch):
    from core import profile_paths

    monkeypatch.setattr(upd, "_launch", lambda install: None)
    paths = profile_paths.ProfilePaths(helper.profile.iris)
    with profile_paths.profile_writer("other-installation", paths=paths, format_version=1):
        (paths.state / "format.json").write_text('{"schema_version": 1, "format_version": 2}')
        assert upd.activate(helper.install, helper.target, "upgrade", confirm_timeout=5) == 1
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and state["last_activation"]["outcome"] == "failed"


def test_new_version_that_never_confirms_is_reverted_with_profile_backup(helper, monkeypatch):
    launches = []
    monkeypatch.setattr(upd, "_launch", lambda install: launches.append(inst.read_state(install)["active"]))
    state = inst.read_state(helper.install)
    state["versions"][helper.own]["hermes"] = "0.20.0"  # a Hermes change takes a backup
    inst.write_state(helper.install, state)
    (helper.profile.hermes / "config.yaml").write_text("before")

    real_launch_count = len(launches)
    assert upd.activate(helper.install, helper.target, "upgrade", confirm_timeout=0.01) == 1
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and state["confirmed"] and state["pending"] is None
    assert state["last_activation"]["outcome"] == "reverted" and "perfil" in state["last_activation"]["message"]
    assert launches[real_launch_count:] == [helper.target, helper.own]
    assert (helper.profile.hermes / "config.yaml").read_text() == "before"


def _switched(fixture, **pending):
    state = inst.read_state(fixture.install)
    state.update(active=fixture.target, previous=fixture.own, confirmed=False)
    state["pending"].update(phase="switched", **pending)
    inst.write_state(fixture.install, state)


def test_a_late_confirmation_is_kept_instead_of_reverted(helper, monkeypatch):
    _switched(helper)
    inst.confirm_active(helper.install, helper.target)
    closed = Mock()
    monkeypatch.setattr(upd, "_close_desktop", closed)
    assert upd._revert(helper.install, helper.own, helper.target, "timeout") is False
    closed.assert_not_called()
    assert inst.read_state(helper.install)["active"] == helper.target


def test_a_version_being_reverted_cannot_confirm(helper):
    _switched(helper)
    state = inst.read_state(helper.install)
    state["pending"]["phase"] = "reverting"
    inst.write_state(helper.install, state)
    assert inst.confirm_active(helper.install, helper.target) is False
    assert inst.read_state(helper.install)["confirmed"] is False


def test_interrupted_revert_is_not_mistaken_for_an_interruption_before_switching(helper):
    _switched(helper, helper={"pid": 999999, "created": 1.0})
    state = inst.read_state(helper.install)
    state["pending"]["phase"] = "reverting"
    inst.write_state(helper.install, state)
    assert inst.startup_check(helper.install, helper.target) is None
    state = inst.read_state(helper.install)
    assert state["pending"]["phase"] == "reverting" and state["last_activation"] is None


def test_revert_records_a_backup_it_could_not_restore(helper, monkeypatch):
    monkeypatch.setattr(upd, "_launch", lambda install: None)
    _switched(helper, backup=str(helper.profile.iris / ".profile" / "backups" / "missing"))
    assert upd._revert(helper.install, helper.own, helper.target, "timeout") is True
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and state["confirmed"]
    assert "No se pudo restaurar" in state["last_activation"]["message"]


def test_unexpected_error_before_switching_still_restarts_the_running_version(helper, monkeypatch):
    launches = []
    monkeypatch.setattr(upd, "_launch", lambda install: launches.append(inst.read_state(install)["active"]))
    monkeypatch.setattr(upd, "_ports_free", Mock(side_effect=KeyError("unexpected")))
    assert upd.activate(helper.install, helper.target, "upgrade", confirm_timeout=5) == 1
    assert launches == [helper.own] and inst.read_state(helper.install)["pending"] is None


def test_launcher_fallback_restores_the_backup_of_the_failed_version(helper, monkeypatch):
    (helper.profile.iris / "config.json").write_text("before")
    backup = inst.backup_profile(helper.profile.iris, helper.profile.hermes, "upgrade")
    (helper.profile.iris / "config.json").write_text("migrated by the failed version")
    _switched(helper, backup=str(backup))
    helper.install.trial_path.write_text(json.dumps({"target": helper.target, "launches": inst.TRIAL_LIMIT + 1}))
    assert inst.startup_check(helper.install, helper.own) is None
    assert ("stop", helper.target) in helper.calls  # its services first, with its own rules
    assert (helper.profile.iris / "config.json").read_text() == "before"
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and "restauró" in state["last_activation"]["message"]


def _newer_profile(helper, monkeypatch):
    """The running version writes format 2; the rollback target only reads format 1."""
    from core import profile_paths

    monkeypatch.setattr(profile_paths, "PROFILE_FORMAT_VERSION", 2)
    paths = profile_paths.ProfilePaths(helper.profile.iris)
    with profile_paths.profile_coordination(paths=paths):
        pass
    (paths.state / "format.json").write_text('{"schema_version": 1, "format_version": 2}')
    state = inst.read_state(helper.install)
    state["pending"]["operation"] = "rollback"
    inst.write_state(helper.install, state)
    return paths


def test_rollback_to_an_older_profile_format_requires_its_backup(helper, monkeypatch):
    launches = []
    monkeypatch.setattr(upd, "_launch", lambda install: launches.append(inst.read_state(install)["active"]))
    _newer_profile(helper, monkeypatch)
    assert upd.activate(helper.install, helper.target, "rollback", confirm_timeout=5) == 1
    state = inst.read_state(helper.install)
    assert state["active"] == helper.own and "copia de seguridad" in state["last_activation"]["message"]
    assert launches == [helper.own]


def test_rollback_restores_the_backup_taken_by_the_upgrade(helper, monkeypatch):
    paths = _newer_profile(helper, monkeypatch)
    (paths.state / "format.json").write_text('{"schema_version": 1, "format_version": 1}')
    (helper.profile.iris / "config.json").write_text("format 1 data")
    backup = inst.backup_profile(helper.profile.iris, helper.profile.hermes, "upgrade")
    (paths.state / "format.json").write_text('{"schema_version": 1, "format_version": 2}')
    (helper.profile.iris / "config.json").write_text("migrated to format 2")
    state = inst.read_state(helper.install)
    state["versions"][helper.own]["backup_from_previous"] = str(backup)
    inst.write_state(helper.install, state)

    def launch(install):
        inst.confirm_active(install, inst.read_state(install)["active"])

    monkeypatch.setattr(upd, "_launch", launch)
    assert upd.activate(helper.install, helper.target, "rollback", confirm_timeout=5) == 0
    assert (helper.profile.iris / "config.json").read_text() == "format 1 data"
    assert '"format_version": 1' in (paths.state / "format.json").read_text()
    state = inst.read_state(helper.install)
    assert state["active"] == helper.target and state["last_activation"]["message"].startswith("Se volvió a")


def test_start_activation_records_pending_and_spawns_one_helper(installed, monkeypatch):
    target = _add_version(installed)
    monkeypatch.setattr(inst, "own_version_id", lambda runtime=None: installed.active)
    import core.runtime_descriptor as rd

    monkeypatch.setattr(rd, "get_runtime", lambda: SimpleNamespace(iris_python=Path(sys.executable), packaged=True))
    monkeypatch.setattr(rd, "runtime_subprocess_env", lambda source=None: {"SYSTEMROOT": "x"})
    spawned = []
    monkeypatch.setattr(upd, "_spawn_detached", lambda command, cwd, env, log=None:
                        spawned.append(command) or SimpleNamespace(pid=os.getpid()))
    upd.start_activation(installed.install, target, "upgrade")
    state = inst.read_state(installed.install)
    assert state["pending"]["to"] == target and state["pending"]["helper"]["pid"] == os.getpid()
    assert spawned[0][-6:] == ["--root", str(installed.install.root), "--to", target, "--operation", "upgrade"]
    with pytest.raises(upd.UpdateError, match="en curso"):
        upd.start_activation(installed.install, target, "upgrade")


# ── Snapshot (Core API) ─────────────────────────────────────────────────────

def _packaged(monkeypatch, folder):
    import core.runtime_descriptor as rd

    runtime = SimpleNamespace(packaged=True, base=folder, iris_revision=IRIS_REV,
                              hermes=SimpleNamespace(version="0.21.4"))
    monkeypatch.setattr(rd, "get_runtime", lambda: runtime)
    monkeypatch.setattr(rd, "runtime_public_info", lambda: {"mode": "packaged"})


def test_snapshot_offers_the_signed_newer_release_and_rollback(installed, profile, monkeypatch):
    release, _ = _publish(installed)
    _packaged(monkeypatch, installed.install.folder(installed.active))
    monkeypatch.setattr(upd, "fetch_manifest", lambda channel, client=None: {"sequence": 4, "releases": [release]})
    snapshot = upd.update_snapshot()
    assert snapshot["summary"] == "updates_available" and snapshot["can_update"] is True
    assert snapshot["available"]["version"] == "1.0.1" and snapshot["channel"] == {"name": "stable", "test": True}
    channel_url = channel_doc(installed.entry)["url"]
    assert inst.read_state(installed.install)["channel_sequences"] == {channel_url: 4}
    monkeypatch.setattr(upd, "fetch_manifest", lambda channel, client=None: {"sequence": 3, "releases": [release]})
    replay = upd.update_snapshot()
    assert replay["summary"] == "error" and "anterior" in replay["message"]


def test_sequence_is_tracked_per_channel_address(installed, profile, monkeypatch):
    """A version that moves to another channel or host is not stuck behind the old count."""
    release, _ = _publish(installed)
    _packaged(monkeypatch, installed.install.folder(installed.active))
    state = inst.read_state(installed.install)
    old = {f"https://old-{n}.example/beta.json": 40 + n for n in range(inst.MAX_CHANNELS)}
    inst.write_state(installed.install, {**state, "channel_sequences": old})
    monkeypatch.setattr(upd, "fetch_manifest", lambda channel, client=None: {"sequence": 1, "releases": [release]})
    assert upd.update_snapshot()["summary"] == "updates_available"
    sequences = inst.read_state(installed.install)["channel_sequences"]
    assert len(sequences) == inst.MAX_CHANNELS and "https://old-0.example/beta.json" not in sequences
    assert list(sequences.items())[-1] == (channel_doc(installed.entry)["url"], 1)
    for broken in ({"x": 0}, {"": 1}, [1], {f"u{n}": 1 for n in range(inst.MAX_CHANNELS + 1)}):
        with pytest.raises(inst.InstallError):
            inst.write_state(installed.install, {**state, "channel_sequences": broken})


def test_snapshot_without_installer_layout_or_channel_offers_nothing(installed, tmp_path, monkeypatch):
    _packaged(monkeypatch, tmp_path / "staging-1.0.0")
    assert upd.update_snapshot()["summary"] == "unavailable"
    folder = installed.install.folder(installed.active)
    _packaged(monkeypatch, folder)
    (folder / "channel.json").unlink()
    snapshot = upd.update_snapshot()
    assert snapshot["summary"] == "unavailable" and "canal" in snapshot["message"]


def test_package_tool_round_trip_is_verifiable_by_the_client(installed):
    release, archive = _publish(installed)
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
    assert names == sorted(names) and "iris-staging.json" in names
    assert release.files == len(names) and release.sha256 == _sha(archive.read_bytes())


# ── Release gate before a real channel is published ────────────────────────

REAL_CHANNEL = "https://updates.iris.example/stable.json"
REAL_PACKAGE = "https://downloads.iris.example/iris-1.0.1.zip"


def _pem(path: Path, key) -> Path:
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return path


def _real_package(folder: Path, keys: list[dict], *, channel: dict | None = None, marker: str = "") -> Path:
    document = channel or {"schema": 1, "channel": "stable", "url": REAL_CHANNEL, "keys": keys, "test": False}
    staging = make_version(folder / "staging", iris="1.0.1", revision="c" * 40, channel=document, marker=marker)
    archive = folder / "iris-1.0.1.zip"
    tooling.package(staging, archive)
    return archive


def _failed(report: dict) -> list[str]:
    return [item["check"] for item in report["checks"] if not item["ok"]]


@pytest.fixture
def production(tmp_path, keypair):
    key, entry = keypair
    public = tmp_path / "production.json"
    public.write_text(json.dumps(entry))
    return SimpleNamespace(key=key, entry=entry, pem=_pem(tmp_path / "production.pem", key), public=public)


def test_release_check_passes_a_real_channel_and_requires_a_higher_sequence(tmp_path, production):
    archive = _real_package(tmp_path, [production.entry])
    first = tmp_path / "stable-1.json"
    tooling.manifest("stable", 1, production.pem, [f"{archive}={REAL_PACKAGE}"], first)
    report = tooling.check(first, REAL_CHANNEL, [production.public], [archive], first_publication=True)
    assert report["ok"], report
    assert [item["check"] for item in report["checks"]] == ["firma", "canal real", "vigencia", "secuencia",
                                                            "paquete Iris 1.0.1 (" + json.loads(
                                                                Path(f"{archive}.release.json").read_text())["id"] + ")"]
    same, higher = tmp_path / "stable-same.json", tmp_path / "stable-2.json"
    tooling.manifest("stable", 1, production.pem, [f"{archive}={REAL_PACKAGE}"], same)
    tooling.manifest("stable", 2, production.pem, [f"{archive}={REAL_PACKAGE}"], higher)
    assert _failed(tooling.check(same, REAL_CHANNEL, [production.public], [archive], previous=first)) == ["secuencia"]
    assert tooling.check(higher, REAL_CHANNEL, [production.public], [archive], previous=first)["ok"]
    for previous, first_publication in ((None, False), (first, True)):
        with pytest.raises(SystemExit):
            tooling.check(higher, REAL_CHANNEL, [production.public], [archive], previous, first_publication)
    missing = tooling.check(first, REAL_CHANNEL, [production.public], [], first_publication=True)
    assert _failed(missing) and _failed(missing)[0].startswith("paquete")
    soon = tmp_path / "stable-soon.json"
    tooling.manifest("stable", 3, production.pem, [f"{archive}={REAL_PACKAGE}"], soon, expires_days=3)
    assert _failed(tooling.check(soon, REAL_CHANNEL, [production.public], [archive], previous=first)) == ["vigencia"]
    assert tooling.main(["check", "--manifest", str(first), "--channel-url", REAL_CHANNEL, "--key",
                         str(production.public), "--package", str(archive), "--first-publication"]) == 0
    assert tooling.main(["check", "--manifest", str(same), "--channel-url", REAL_CHANNEL, "--key",
                         str(production.public), "--package", str(archive), "--previous", str(first)]) == 1


def test_release_check_rejects_test_channels_keys_and_addresses(tmp_path, production):
    test_pem, test_entry = sig.generate_private_key()
    test_key = sig.load_private_key(test_pem)

    def gate(archive, *, signers=(production.key,), channel_url=REAL_CHANNEL):
        signed = tmp_path / f"m-{len(list(tmp_path.glob('m-*')))}.json"
        tooling.manifest("stable", 1, production.pem, [f"{archive}={REAL_PACKAGE}"], signed)
        payload = sig.verify_envelope(signed.read_bytes(), sig.parse_trusted_keys([production.entry]))
        signed.write_bytes(sig.sign_payload(payload, list(signers)))
        return tooling.check(signed, channel_url, [production.public], [archive], first_publication=True)

    good = _real_package(tmp_path / "good", [production.entry])
    assert gate(good)["ok"]
    assert _failed(gate(good, signers=(test_key,))) == ["firma"]
    assert _failed(gate(good, signers=(production.key, test_key))) == ["firma"]
    assert _failed(gate(good, channel_url="https://localhost/stable.json")) == ["canal real"]
    assert _failed(gate(good, channel_url="https://updates.iris.example/beta.json"))[0].startswith("paquete")

    def problems(archive):
        report = gate(archive)
        assert len(_failed(report)) == 1 and _failed(report)[0].startswith("paquete")
        return report["checks"][-1]["detail"]

    assert "test: true" in problems(_real_package(tmp_path / "test", [], channel=channel_doc(production.entry)))
    assert "confía en" in problems(_real_package(tmp_path / "mixed", [production.entry, test_entry]))
    assert "claves privadas" in problems(_real_package(tmp_path / "leak", [production.entry],
                                                       marker=test_pem.decode()))
    for url in ("https://192.168.0.5/iris.zip", "http://127.0.0.1:9999/iris.zip", "http://downloads.example/x.zip"):
        with pytest.raises(SystemExit):
            tooling.manifest("stable", 1, production.pem, [f"{good}={url}"], tmp_path / "refused.json")
    tooling.manifest("stable", 1, production.pem, [f"{good}=http://127.0.0.1:9999/iris.zip"], tmp_path / "t.json",
                     test=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_protected_production_key_is_never_stored_in_clear_and_signs(tmp_path, monkeypatch):
    import getpass

    private, public = tmp_path / "keys" / "production.key", tmp_path / "keys" / "production.json"
    entry = tooling.keygen(private, public, protect=True)
    raw = private.read_bytes()
    assert raw.startswith(b"IRIS-OS-DPAPI-1\n") and b"PRIVATE KEY" not in raw
    assert json.loads(public.read_text()) == entry
    assert sig.public_entry(tooling.read_private_key(private).public_key()) == entry
    tampered = tmp_path / "tampered.key"
    tampered.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(SystemExit):
        tooling.read_private_key(tampered)
    with pytest.raises(SystemExit):
        tooling.keygen(private, tmp_path / "other.json", protect=True)
    archive = _real_package(tmp_path, [entry])
    signed = tmp_path / "beta.json"
    tooling.manifest("stable", 1, private, [f"{archive}={REAL_PACKAGE}"], signed)
    assert tooling.check(signed, REAL_CHANNEL, [public], [archive], first_publication=True)["ok"]

    phrases = iter(["corta", "corta"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(phrases))
    with pytest.raises(SystemExit):
        tooling.backup_key(private, tmp_path / "short.pem")
    assert not (tmp_path / "short.pem").exists()
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "frase de respaldo bastante larga")
    backup = tmp_path / "backup.pem"
    assert tooling.backup_key(private, backup) == entry
    assert b"ENCRYPTED PRIVATE KEY" in backup.read_bytes()
    assert sig.public_entry(tooling.read_private_key(backup).public_key()) == entry
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "otra frase equivocada")
    with pytest.raises(ValueError):
        tooling.read_private_key(backup)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "frase de respaldo bastante larga")
    assert tooling.verify_key(backup, [public]) == {"file": str(backup), "format": "backup with passphrase",
                                                    "keyid": entry["keyid"], "matches": True}
    assert tooling.verify_key(private, [public])["format"] == "dpapi"


def test_private_key_scan_finds_keys_but_not_certificates_or_code(tmp_path):
    pem, _ = sig.generate_private_key()
    certificate = b"-----BEGIN CERTIFICATE-----\n" + b"A" * 64 + b"\n-----END CERTIFICATE-----\n"
    archive = tmp_path / "scan.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("certifi/cacert.pem", certificate * 3)
        package.writestr("cryptography/ssh.py", b'_SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"\n')
        package.writestr("data/leak.json", json.dumps({"key": pem.decode()}))
        package.writestr("conf/signing.pem", b"x" * 100 + pem)
        package.writestr("conf/store.pfx", b"\x30\x82")
    with zipfile.ZipFile(archive) as package:
        assert tooling.private_keys_in(package) == ["data/leak.json", "conf/signing.pem", "conf/store.pfx"]
        assert tooling.private_keys_in(package, chunk=16) == ["data/leak.json", "conf/signing.pem", "conf/store.pfx"]


def test_packaged_update_never_runs_git_or_hermes_update(installed, profile, monkeypatch):
    from core import hermes_updates

    effect = Mock(side_effect=AssertionError("external updater"))
    monkeypatch.setattr(hermes_updates, "run_hermes_cli", effect)
    monkeypatch.setattr(upd.subprocess, "run", effect)
    _packaged(monkeypatch, installed.install.folder(installed.active))
    monkeypatch.setattr(upd, "fetch_manifest", lambda channel, client=None: {"sequence": 1, "releases": []})
    assert upd.update_snapshot()["summary"] == "current"
    effect.assert_not_called()


def test_helper_command_line_is_a_stable_contract():
    parser_help = upd.main.__code__.co_consts
    for command in ("activate", "stop-services", "install", "verify"):
        assert command in parser_help


# ── Renewal of a published channel ─────────────────────────────────────────

import publish_update_channel as renewal  # noqa: E402

GITHUB_REPO = "o/iris-os-releases"
GITHUB_PACKAGE = "https://github.com/o/iris-os-releases/releases/download/v0.9.1-beta/iris-os-0.9.1.zip"


def _beta_package(folder: Path, entry: dict) -> Path:
    return _real_package(folder, [entry], channel={"schema": 1, "channel": "beta", "url": GITHUB_CHANNEL,
                                                   "keys": [entry], "test": False})


def _serving_packages(monkeypatch, archive: Path, *, body: bytes | None = None, status: int = 200):
    """Anonymous client of the gate: GitHub redirect, then the package; records the headers."""
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        if str(request.url) == GITHUB_PACKAGE:
            return httpx.Response(302, headers={"location": GITHUB_ASSET})
        return httpx.Response(status, content=archive.read_bytes() if body is None else body)

    monkeypatch.setattr(tooling, "anonymous_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))
    return seen


def test_renew_signs_the_same_releases_with_the_next_sequence(tmp_path, production):
    archive = _real_package(tmp_path, [production.entry])
    expired = tmp_path / "stable-1.json"
    tooling.manifest("stable", 1, production.pem, [f"{archive}={REAL_PACKAGE}"], expired, expires_days=-1)
    renewed = tmp_path / "stable-2.json"
    document = tooling.renew(expired, [production.public], production.pem, renewed)
    old = json.loads(sig.verify_envelope(expired.read_bytes(), sig.parse_trusted_keys([production.entry])))
    assert document["sequence"] == 2 and document["releases"] == old["releases"]
    days = (datetime.fromisoformat(document["expires_at"]) - datetime.now(timezone.utc)).days
    assert 89 <= days <= 90
    assert tooling.check(renewed, REAL_CHANNEL, [production.public], [archive], previous=expired)["ok"]
    with pytest.raises(SystemExit):
        tooling.renew(expired, [production.public], production.pem, renewed)  # never overwrites
    other_pem, _ = sig.generate_private_key()
    other = _pem(tmp_path / "other.pem", sig.load_private_key(other_pem))
    with pytest.raises(SystemExit, match="production"):
        tooling.renew(expired, [production.public], other, tmp_path / "x.json")  # foreign signing key
    forged = tmp_path / "forged.json"
    forged.write_bytes(sig.sign_payload(json.dumps(old).encode(), [sig.load_private_key(other_pem)]))
    with pytest.raises(SystemExit, match="not signed"):
        tooling.renew(forged, [production.public], production.pem, tmp_path / "y.json")
    assert not (tmp_path / "x.json").exists() and not (tmp_path / "y.json").exists()


def _other_public(tmp_path: Path) -> Path:
    path = tmp_path / "other-public.json"
    path.write_text(json.dumps(sig.generate_private_key()[1]))
    return path


def test_check_remote_downloads_each_package_anonymously(tmp_path, production, monkeypatch):
    archive = _beta_package(tmp_path, production.entry)
    signed = tmp_path / "beta.json"
    tooling.manifest("beta", 1, production.pem, [f"{archive}={GITHUB_PACKAGE}"], signed)
    seen = _serving_packages(monkeypatch, archive)
    report = tooling.check(signed, GITHUB_CHANNEL, [production.public], [], first_publication=True, remote=True)
    assert report["ok"], report
    assert report["checks"][-1]["detail"].startswith("descargado sin autenticación")
    assert len(seen) == 2 and not any({"authorization", "cookie"} & set(headers) for headers in seen)
    assert not tooling.check(signed, GITHUB_CHANNEL, [production.public], [], first_publication=True)["ok"]
    for kwargs in ({"status": 404}, {"body": b"tampered"}):
        _serving_packages(monkeypatch, archive, **kwargs)
        failed = tooling.check(signed, GITHUB_CHANNEL, [production.public], [], first_publication=True, remote=True)
        assert _failed(failed) and _failed(failed)[0].startswith("paquete")
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(production.entry))
    assert tooling.verify_key(production.pem, [plain])["format"] == "plain"
    with pytest.raises(SystemExit):
        tooling.verify_key(production.pem, [_other_public(tmp_path)])


class FakeGitHub:
    """Release assets of one tag, driven through the gh command lines the renewal uses."""

    def __init__(self, tag: str, files: dict[str, bytes], fail_rename_to: str | None = None):
        self.tag, self.assets, self.next_id = tag, {}, 100
        self.calls: list[list[str]] = []
        self.fail_rename_to = fail_rename_to
        for name, data in files.items():
            self._add(name, data)

    def _add(self, name, data):
        self.next_id += 1
        self.assets[name] = (self.next_id, data)

    def __call__(self, arguments: list[str]) -> str:
        self.calls.append(arguments)
        if arguments[:2] == ["api", f"repos/{GITHUB_REPO}/releases/tags/{self.tag}"]:
            return json.dumps({"assets": [{"id": i, "name": n} for n, (i, _) in self.assets.items()]})
        if arguments[:3] == ["release", "upload", self.tag]:
            path = Path(arguments[3])
            assert path.name not in self.assets
            self._add(path.name, path.read_bytes())
            return ""
        if arguments[:3] == ["api", "-X", "PATCH"]:
            asset_id, new_name = int(arguments[3].rsplit("/", 1)[1]), arguments[5].split("=", 1)[1]
            if new_name == self.fail_rename_to and "beta-0001.json" in self.assets:
                self.fail_rename_to = None
                raise renewal.RenewalError("rename failed")
            old_name = next(n for n, (i, _) in self.assets.items() if i == asset_id)
            assert new_name not in self.assets
            self.assets[new_name] = self.assets.pop(old_name)
            return "{}"
        raise AssertionError(arguments)

    def fetch(self, url: str) -> bytes:
        prefix = f"https://github.com/{GITHUB_REPO}/releases/download/{self.tag}/"
        name = url[len(prefix):] if url.startswith(prefix) else None
        if name not in self.assets:
            raise renewal.RenewalError(f"{url} respondió HTTP 404.")
        return self.assets[name][1]


def _renewal_args(tmp_path, production, *, dry_run=False):
    return SimpleNamespace(channel_url=GITHUB_CHANNEL, repo=GITHUB_REPO, tag="channel-beta", key=[production.public],
                           private=production.pem, gh=None, work=tmp_path / "work", expires_days=90,
                           dry_run=dry_run)


@pytest.fixture
def published_beta(tmp_path, production, monkeypatch):
    archive = _beta_package(tmp_path, production.entry)
    first = tmp_path / "beta.json"
    tooling.manifest("beta", 1, production.pem, [f"{archive}={GITHUB_PACKAGE}"], first)
    _serving_packages(monkeypatch, archive)
    monkeypatch.setattr(renewal.time, "sleep", lambda seconds: None)
    return SimpleNamespace(archive=archive, first=first.read_bytes())


def test_renewal_publishes_safely_and_keeps_the_previous_manifest(tmp_path, production, published_beta):
    github = FakeGitHub("channel-beta", {"beta.json": published_beta.first})
    dry = renewal.renew_channel(_renewal_args(tmp_path, production, dry_run=True), gh=github, fetch=github.fetch)
    assert dry["published"] is False and github.calls == [] and Path(dry["file"]).name == "beta-0002.json"
    result = renewal.renew_channel(_renewal_args(tmp_path, production), gh=github, fetch=github.fetch)
    assert result["published"] and result["sequence"] == 2 and result["archive"] == "beta-0001.json"
    assert github.assets["beta-0001.json"][1] == published_beta.first
    served = json.loads(sig.verify_envelope(github.assets["beta.json"][1], sig.parse_trusted_keys([production.entry])))
    assert served["sequence"] == 2 and set(github.assets) == {"beta.json", "beta-0001.json"}
    kinds = [call[:3] for call in github.calls if call[0] != "api" or call[1] == "-X"]
    assert kinds == [["release", "upload", "channel-beta"], ["api", "-X", "PATCH"], ["api", "-X", "PATCH"]]
    work = tmp_path / "work"
    assert (work / "beta-0001.publicado.json").read_bytes() == published_beta.first
    assert any(work.glob("beta-0002.json.descartado-*"))  # the dry run's file was set aside, not reused
    assert json.loads((work / "beta-0002.check.json").read_text("utf-8"))["ok"]
    again = renewal.renew_channel(_renewal_args(tmp_path, production), gh=github, fetch=github.fetch)
    assert again["sequence"] == 3 and set(github.assets) == {"beta.json", "beta-0001.json", "beta-0002.json"}


def test_renewal_publishes_nothing_when_a_step_fails(tmp_path, production, published_beta, monkeypatch):
    other_pem, _ = sig.generate_private_key()
    payload = sig.verify_envelope(published_beta.first, sig.parse_trusted_keys([production.entry]))
    foreign = FakeGitHub("channel-beta", {"beta.json": sig.sign_payload(payload, [sig.load_private_key(other_pem)])})
    with pytest.raises(renewal.RenewalError, match="clave de producción"):
        renewal.renew_channel(_renewal_args(tmp_path, production), gh=foreign, fetch=foreign.fetch)
    assert foreign.calls == []

    _serving_packages(monkeypatch, published_beta.archive, body=b"tampered")
    github = FakeGitHub("channel-beta", {"beta.json": published_beta.first})
    with pytest.raises(renewal.RenewalError, match="comprobación"):
        renewal.renew_channel(_renewal_args(tmp_path / "a", production), gh=github, fetch=github.fetch)
    assert github.calls == [] and set(github.assets) == {"beta.json"}

    _serving_packages(monkeypatch, published_beta.archive)
    leftover = FakeGitHub("channel-beta", {"beta.json": published_beta.first, "beta-0002.json": b"half"})
    with pytest.raises(renewal.RenewalError, match="beta-0002.json"):
        renewal.renew_channel(_renewal_args(tmp_path / "b", production), gh=leftover, fetch=leftover.fetch)
    assert not any(call[:2] == ["release", "upload"] for call in leftover.calls)

    broken = FakeGitHub("channel-beta", {"beta.json": published_beta.first}, fail_rename_to="beta.json")
    with pytest.raises(renewal.RenewalError, match="rename failed"):
        renewal.renew_channel(_renewal_args(tmp_path / "c", production), gh=broken, fetch=broken.fetch)
    assert broken.assets["beta.json"][1] == published_beta.first  # the old name was restored


def test_publishing_new_versions_checks_the_uploaded_package(tmp_path, production, published_beta, monkeypatch):
    rebuilt = _real_package(tmp_path / "rebuilt", [production.entry], marker="rebuilt",
                            channel={"schema": 1, "channel": "beta", "url": GITHUB_CHANNEL,
                                     "keys": [production.entry], "test": False})
    assert rebuilt.read_bytes() != published_beta.archive.read_bytes()
    github = FakeGitHub("channel-beta", {"beta.json": published_beta.first})
    args = _renewal_args(tmp_path, production)
    args.release = [f"{rebuilt}={GITHUB_PACKAGE}"]
    # The new package is not uploaded yet (GitHub still serves the old one): nothing is published.
    with pytest.raises(renewal.RenewalError, match="comprobación"):
        renewal.renew_channel(args, gh=github, fetch=github.fetch)
    assert github.calls == [] and set(github.assets) == {"beta.json"}
    _serving_packages(monkeypatch, rebuilt)
    args.work = tmp_path / "work2"
    result = renewal.renew_channel(args, gh=github, fetch=github.fetch)
    served = json.loads(sig.verify_envelope(github.assets["beta.json"][1], sig.parse_trusted_keys([production.entry])))
    assert result["sequence"] == 2 and served["releases"][0]["package"]["sha256"] == _sha(rebuilt.read_bytes())
    assert github.assets["beta-0001.json"][1] == published_beta.first
