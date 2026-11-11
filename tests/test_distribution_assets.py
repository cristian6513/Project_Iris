from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.distribution_assets import (
    DistributionError,
    load_contract,
    prepare_assets,
    read_regular,
    release_manifest,
    verify_assets,
)
from scripts.verify_distribution import verify_archive

ROOT = Path(__file__).resolve().parents[1]


def fixture_contract():
    # Explicit, positive requirements for small archive fixtures, never a bypass.
    payload = b"fixture-only-animation"
    contract = {
        "schema": 1,
        "rest_pose": "idle",
        "clips": {
            "idle": {"filename": "iris_idle.vrma", "sha256": hashlib.sha256(payload).hexdigest()}
        },
    }
    manifest = release_manifest(
        {
            "clips": {"idle": "iris_idle.vrma"},
            "idle_pool": ["idle"],
            "return_idle_pool": ["idle"],
            "activity_clips": {
                state: {"clip": "idle"} for state in ("listening", "thinking", "speaking")
            },
        },
        contract,
    )
    files = {
        "iris_idle.vrma": payload,
        "manifest.json": json.dumps(manifest).encode(),
        "contexts.json": b'{"version":1,"contexts":{}}',
        "emotions.json": b'{"emotions":{}}',
        "README.md": b"fixture",
    }
    return contract, files


def wheel(path, files, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files.items():
            archive.writestr(f"face/animations/{name}", payload)
        for name, payload in (extra or {}).items():
            item = zipfile.ZipInfo()
            item.filename = name  # Preserve hostile separators on Windows, too.
            archive.writestr(item, payload)
    return path


def test_production_rejects_manifest_only_and_minimal_contract(tmp_path):
    contract, files = fixture_contract()
    path = wheel(tmp_path / "fixture.whl", files)
    assert verify_archive(path, contract=contract, web_contract=None) == path
    with pytest.raises(DistributionError, match="Inventario"):
        verify_archive(path)
    path = wheel(tmp_path / "missing.whl", {"manifest.json": files["manifest.json"]})
    with pytest.raises(DistributionError, match="Inventario"):
        verify_archive(path, contract=contract, web_contract=None)


@pytest.mark.parametrize(
    "name",
    [
        "/absolute.vrma",
        "C:/absolute.vrma",
        "../outside.vrma",
        "nested/../../outside.vrma",
        r"..\outside.vrma",
        r"C:\outside.vrma",
        r"\\server\share\outside.vrma",
        "sub/file.vrma",
        "iris_idle.vrma:secret",
        "iris_idle.vrma.",
        "NUL.vrma",
    ],
)
def test_release_rejects_unsafe_manifest_filenames(name):
    contract, files = fixture_contract()
    manifest = json.loads(files["manifest.json"])
    manifest["clips"]["optional"] = name
    with pytest.raises(DistributionError):
        release_manifest(manifest, contract)


@pytest.mark.parametrize(
    "name",
    [
        "/absolute.txt",
        "C:/absolute.txt",
        "../outside.txt",
        "face/../outside.txt",
        r"face\animations\iris_idle.vrma",
        r"..\outside.txt",
        r"\\server\share\outside.txt",
        "face//file.txt",
        "face/./file.txt",
        "face/NUL.txt",
        "file.txt:secret",
    ],
)
@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_archives_reject_unsafe_members(tmp_path, name, kind):
    contract, files = fixture_contract()
    if kind == "zip":
        path = wheel(tmp_path / "bad.whl", files, {name: b"untrusted"})
    else:
        path = tmp_path / "bad.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo(name)
            member.size = 9
            archive.addfile(member, io.BytesIO(b"untrusted"))
    with pytest.raises(DistributionError, match="Ruta"):
        verify_archive(path, contract=contract, web_contract=None)


@pytest.mark.parametrize("kind", ["zip-link", "tar-link", "tar-hardlink", "tar-fifo"])
def test_archives_reject_links_and_special_members(tmp_path, kind):
    contract, files = fixture_contract()
    if kind == "zip-link":
        path = wheel(tmp_path / "link.whl", files)
        with zipfile.ZipFile(path, "a") as archive:
            item = zipfile.ZipInfo("linked.txt")
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, "../private.txt")
    else:
        path = tmp_path / "link.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            item = tarfile.TarInfo("iris/linked.txt")
            item.type = {
                "tar-link": tarfile.SYMTYPE,
                "tar-hardlink": tarfile.LNKTYPE,
                "tar-fifo": tarfile.FIFOTYPE,
            }[kind]
            item.linkname = "../private.txt"
            archive.addfile(item)
    with pytest.raises(DistributionError, match="Enlace"):
        verify_archive(path, contract=contract, web_contract=None)


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_archives_reject_case_aliases(tmp_path, kind):
    contract, files = fixture_contract()
    if kind == "zip":
        path = wheel(tmp_path / "dup.whl", files, {"FACE/animations/iris_idle.vrma": b"other"})
    else:
        path = tmp_path / "dup.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name in ("iris/file", "iris/FILE"):
                item = tarfile.TarInfo(name)
                archive.addfile(item, io.BytesIO())
    with pytest.raises(DistributionError, match="duplicado"):
        verify_archive(path, contract=contract, web_contract=None)


def test_unknown_compression_and_distribution_format_are_rejected(tmp_path):
    contract, files = fixture_contract()
    path = wheel(tmp_path / "method.whl", files)
    payload = bytearray(path.read_bytes())
    local = payload.index(b"PK\x03\x04")
    central = payload.index(b"PK\x01\x02")
    struct.pack_into("<H", payload, local + 8, 99)
    struct.pack_into("<H", payload, central + 10, 99)
    path.write_bytes(payload)
    with pytest.raises(DistributionError, match="Método"):
        verify_archive(path, contract=contract, web_contract=None)
    with pytest.raises(DistributionError, match="Formato"):
        verify_archive(tmp_path / "unknown.7z", contract=contract, web_contract=None)


@pytest.mark.parametrize(
    "change", ["missing", "hash", "draft", "pose", "pool", "state", "face", "context"]
)
def test_archive_requires_hashes_and_coherent_references(tmp_path, change):
    contract, files = fixture_contract()
    if change == "missing":
        del files["iris_idle.vrma"]
    elif change == "hash":
        files["iris_idle.vrma"] = b"modified"
    elif change == "draft":
        files["_draft.glb"] = b"private"
    else:
        name = "contexts.json" if change == "context" else "manifest.json"
        document = json.loads(files[name])
        if change == "pose":
            document["pose_map"]["sit_chair"] = "sit"
        elif change == "pool":
            document["idle_pool"] = ["missing"]
        elif change == "state":
            document["activity_clips"]["listening"]["clip"] = "missing"
        elif change == "face":
            document["facial_profiles"]["draft"] = {}
        else:
            document["contexts"] = {"desktop_companion": {"walk": {"animation": "missing"}}}
        files[name] = json.dumps(document).encode()
    with pytest.raises(DistributionError):
        verify_archive(wheel(tmp_path / "invalid.whl", files), contract=contract, web_contract=None)


@pytest.mark.parametrize("nested", [False, True])
def test_source_cannot_follow_real_symlink_or_windows_junction(tmp_path, nested):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"private")
    link = tmp_path / "alias"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    if nested:
        (outside / "nested").mkdir()
        (outside / "nested" / "private.txt").write_bytes(b"private")
    try:
        with pytest.raises(DistributionError, match="Enlace"):
            read_regular(link / "nested" if nested else link, "private.txt")
        assert (outside / "private.txt").read_bytes() == b"private"
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_factory_assets_preserve_states_face_profiles_and_local_originals():
    source = ROOT / "face" / "animations"
    before = (source / "manifest.json").read_bytes()
    original = json.loads(before)
    assets = prepare_assets(source)
    manifest = json.loads(assets["manifest.json"])
    contract = load_contract()
    verify_assets(assets, contract)
    # 59 until 2026-11-09, when 33 gestures, variants, a walk and a seat from Tripo motions were added;
    # 92 until 2026-11-10 (face down on the bed: its loop and the two rolls).
    assert len(manifest["clips"]) == 95
    assert manifest["variants"] == original["variants"]
    assert manifest["variant_weights"] == original["variant_weights"]
    assert all(clip in manifest["clips"] for clips in manifest["variants"].values() for clip in clips)
    # Every packaged clip keeps its face; a clip left out takes its profile with it.
    assert manifest["facial_profiles"] == {
        name: profile for name, profile in original["facial_profiles"].items() if name in manifest["clips"]
    }
    assert manifest["activity_clips"] == original["activity_clips"]
    assert manifest["idle_pool"] == manifest["return_idle_pool"] == ["idle"]
    assert manifest["menu_poses"] == original["menu_poses"]
    assert not {"sit", "walk_in_place", "pc_sit_down_v2", "pc_seated_idle_v2"} & manifest["clips"].keys()
    room_clips = set(manifest["clips"]) - set(original["_source_mapping"])
    assert len(room_clips) == 43  # 41 before the two rolls onto her front and back
    assert room_clips <= set(manifest["runtime_only"])
    assert (source / "manifest.json").read_bytes() == before
    contexts = json.loads(assets["contexts.json"])["contexts"]["desktop_companion"]
    assert contexts["walk_tour"]["animation"] == "idle_casual"
    assert contexts["walk_tour"]["movement"] == "window-walk"


def test_iris_avatar_ships_pinned_with_its_own_licence():
    """IRIS' default VRM (2026-10-28) travels like the clips, never under the MIT licence."""
    contract = load_contract()
    avatar = contract["avatar"]
    assert avatar == {"filename": "iris.vrm", "license": "LICENSE-IRIS-VRM.txt",
                      "sha256": "c1dc7d03e96b4e166f13bced882ae20c09e13bb3129dd2ea42b56aae0ccd3531"}
    assets = prepare_assets(ROOT / "face" / "animations", contract)
    assert hashlib.sha256(assets["iris.vrm"]).hexdigest() == avatar["sha256"]
    assert "NO forma parte de la licencia MIT" in assets["LICENSE-IRIS-VRM.txt"].decode("utf-8")
    verify_assets(assets, contract)
    tampered = dict(assets, **{"iris.vrm": b"another model"})
    with pytest.raises(DistributionError, match="avatar"):
        verify_assets(tampered, contract)
    missing = {name: data for name, data in assets.items() if name != "LICENSE-IRIS-VRM.txt"}
    with pytest.raises(DistributionError, match="faltan=\\['LICENSE-IRIS-VRM.txt'\\]"):
        verify_assets(missing, contract)
    for change in ({"filename": "iris.glb"}, {"filename": "../iris.vrm"}, {"license": "iris_idle.vrma"},
                   {"license": "iris.vrm"}, {"sha256": "x"}):
        broken = copy.deepcopy(contract)
        broken["avatar"].update(change)
        with pytest.raises(DistributionError):
            prepare_assets(ROOT / "face" / "animations", broken)


@pytest.mark.parametrize("name,allowed", [
    ("face/animations/iris.vrm", True),
    ("iris_os-1.0.0/face/animations/iris.vrm", True),
    ("face/animations/private.vrm", False),
    ("face/animations/IRIS.vrm", False),
    ("face/avatars/iris.vrm", False),
    ("face/animations/sub/iris.vrm", False),
    ("profiles/face/animations/iris.blend", False),
])
def test_archives_ship_only_iris_factory_vrm(name, allowed):
    """The person's avatars stay private; only the contract's own VRM, at its exact path."""
    from scripts.verify_distribution import forbidden_reason

    assert (forbidden_reason(name) is None) is allowed, name
    assert forbidden_reason(name, contract=fixture_contract()[0]) == "modelo o workspace local", (
        "a contract without an avatar ships no model")


def test_wrong_source_hash_and_missing_idle_fail_before_build(tmp_path):
    contract, files = fixture_contract()
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    prepare_assets(tmp_path, contract)
    (tmp_path / "iris_idle.vrma").write_bytes(b"wrong")
    with pytest.raises(DistributionError, match="Hash"):
        prepare_assets(tmp_path, contract)
    missing = copy.deepcopy(contract)
    missing["rest_pose"] = "missing"
    with pytest.raises(DistributionError, match="reposo"):
        release_manifest(json.loads(files["manifest.json"]), missing)


@pytest.mark.parametrize("prefix", ["other/face/animations", "other/FACE/Animations"])
def test_approved_filename_cannot_hide_an_extra_resource_outside_installed_path(tmp_path, prefix):
    contract, files = fixture_contract()
    path = wheel(tmp_path / "outside.whl", files, {
        f"{prefix}/iris_idle.vrma": b"PRIVATE MUST NOT SHIP",
    })
    with pytest.raises(DistributionError, match="fuera de la ruta instalada"):
        verify_archive(path, contract=contract, web_contract=None)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_release_publisher_checks_mandatory_assets_before_installer(tmp_path, kind):
    from scripts.build_release import _verify_sdist, _verify_wheel

    if kind == "wheel":
        path = wheel(tmp_path / "missing.whl", {})
        check = _verify_wheel
    else:
        path = tmp_path / "missing.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            archive.addfile(tarfile.TarInfo("iris/README.md"), io.BytesIO())
        check = _verify_sdist
    with pytest.raises(DistributionError, match="Inventario"):
        check({"filename": "companion.exe"}, path)


def web_fixture():
    from scripts.distribution_web_assets import WEB_MANIFEST

    files = {
        "face/index.html": b'<html><script type="module" src="assets/face.js"></script></html>',
        "face/companion/index.html": b"<html>Companion</html>",
        "face/assets/face.js": b"export const ready = true;",
        "face/vendor/licenses/LICENSE.fixture": b"Fixture license for the offline test.",
        "wizard/ui/index.html": b"<html>Wizard</html>",
    }
    contract = {
        "schema": 1,
        "component": "iris-local-web",
        "versions": {"fixture": "1.0.0"},
        "entrypoints": {
            "face": "face/index.html",
            "companion": "face/companion/index.html",
            "wizard": "wizard/ui/index.html",
        },
        "licenses": [{
            "package": "fixture", "version": "1.0.0", "license": "MIT",
            "files": ["face/vendor/licenses/LICENSE.fixture"],
        }],
        "files": [
            {"path": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in sorted(files.items())
        ],
    }
    files[WEB_MANIFEST] = (json.dumps(contract, indent=2) + "\n").encode()
    return contract, files


def write_web_fixture(root, files):
    for name, payload in files.items():
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def test_default_archive_requires_trusted_web_contract_and_explicit_fixture_api(tmp_path, monkeypatch):
    import scripts.verify_distribution as verifier

    animation_contract, animations = fixture_contract()
    web_contract, web = web_fixture()
    path = wheel(tmp_path / "full.whl", animations, web)
    monkeypatch.setattr(verifier, "load_web_contract", lambda: web_contract)
    assert verify_archive(path, contract=animation_contract) == path
    missing = wheel(tmp_path / "missing-web.whl", animations)
    with pytest.raises(DistributionError, match="web inventory"):
        verify_archive(missing, contract=animation_contract)
    assert verify_archive(missing, contract=animation_contract, web_contract=None) == missing


@pytest.mark.parametrize("change", ["missing", "extra", "hash", "size", "manifest"])
def test_web_inventory_rejects_missing_extra_changed_or_untrusted_content(change):
    from scripts.distribution_web_assets import verify_web_assets

    contract, files = web_fixture()
    if change == "missing":
        del files["face/assets/face.js"]
    elif change == "extra":
        files["face/vendor/private.json"] = b"private"
    elif change == "hash":
        files["face/assets/face.js"] = b"modified"
    elif change == "size":
        contract["files"][0]["size"] += 1
        files["face/web-assets.json"] = json.dumps(contract).encode()
    else:
        other = copy.deepcopy(contract)
        other["versions"]["fixture"] = "2.0.0"
        files["face/web-assets.json"] = json.dumps(other).encode()
    with pytest.raises(DistributionError):
        verify_web_assets(files, contract)


@pytest.mark.parametrize(
    "name", [
        "/face/private.js", "C:/face/private.js", "face/../private.js",
        r"face\private.js", "face/vendor/file.js:secret", "face/vendor/NUL.js",
        "face/animations/private.vrma", "face/avatars/private.vrm",
        "wizard/credentials.json", "wizard/ui/.env", "face/vendor/private.map",
    ],
)
def test_web_contract_rejects_unsafe_and_private_paths(name):
    from scripts.distribution_web_assets import validate_web_contract

    contract, _ = web_fixture()
    contract["files"][0]["path"] = name
    with pytest.raises(DistributionError):
        validate_web_contract(contract)


def test_web_contract_rejects_case_aliases_and_unpinned_dependencies():
    from scripts.distribution_web_assets import validate_web_contract

    contract, _ = web_fixture()
    contract["files"].append(dict(contract["files"][0], path=contract["files"][0]["path"].upper()))
    with pytest.raises(DistributionError):
        validate_web_contract(contract)
    contract, _ = web_fixture()
    contract["versions"]["fixture"] = "^1.0.0"
    with pytest.raises(DistributionError, match="pinned"):
        validate_web_contract(contract)


@pytest.mark.parametrize(
    "name,payload", [
        ("face/index.html", b'<script src="https://cdn.example.test/boot.js"></script>'),
        ("face/index.html", b'<script type="importmap">{"imports":{"three":"https://cdn.test/three.js"}}</script>'),
        ("face/vendor/fonts.css", b'@font-face {src: url("https://cdn.test/font.woff2")}'),
        ("face/assets/face.js", b'import("https://cdn.test/module.js")'),
        ("face/assets/face.js", b'const source = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/a.js";'),
    ],
)
def test_web_verifier_rejects_remote_bootstrap_even_with_matching_hashes(name, payload):
    from scripts.distribution_web_assets import verify_web_assets

    contract, files = web_fixture()
    files[name] = payload
    contract["files"] = [
        {"path": key, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        for key, value in sorted(files.items()) if key != "face/web-assets.json"
    ]
    files["face/web-assets.json"] = json.dumps(contract).encode()
    with pytest.raises(DistributionError, match="Remote"):
        verify_web_assets(files, contract)


def test_web_verifier_keeps_remote_documentation_links():
    from scripts.distribution_web_assets import verify_local_bootstrap

    verify_local_bootstrap(
        "wizard/ui/index.html",
        b'<a href="https://provider.example.test/docs" target="_blank">Provider documentation</a>',
    )


def test_web_preparation_rejects_extra_inputs_without_copying_them(tmp_path):
    from scripts.distribution_web_assets import prepare_web_assets

    contract, files = web_fixture()
    write_web_fixture(tmp_path, files)
    assert prepare_web_assets(tmp_path, contract) == files
    private = tmp_path / "face" / "vendor" / "credentials-local.json"
    private.write_bytes(b"PRIVATE MUST NOT SHIP")
    with pytest.raises(DistributionError, match="inventory"):
        prepare_web_assets(tmp_path, contract)
    assert private.read_bytes() == b"PRIVATE MUST NOT SHIP"


@pytest.mark.parametrize("kind", ["hardlink", "junction"])
def test_web_preparation_rejects_links_and_preserves_source(tmp_path, kind):
    from scripts.distribution_web_assets import prepare_web_assets

    contract, files = web_fixture()
    write_web_fixture(tmp_path, files)
    target = tmp_path / "face/assets/face.js"
    if kind == "hardlink":
        outside = tmp_path / "outside.js"
        target.rename(outside)
        os.link(outside, target)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        target.rename(outside / "face.js")
        target.parent.rmdir()
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(target.parent))
        else:
            target.parent.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(DistributionError, match="(?:Linked|hardlinked)"):
            prepare_web_assets(tmp_path, contract)
        original = outside if kind == "hardlink" else outside / "face.js"
        assert original.read_bytes() == files["face/assets/face.js"]
    finally:
        if kind == "junction":
            target.parent.rmdir() if os.name == "nt" else target.parent.unlink()


def test_web_sdist_staging_replaces_hardlinks_without_editing_originals(tmp_path, monkeypatch):
    from scripts import distribution_build as hooks

    contract, files = web_fixture()
    original = tmp_path / "source.html"
    original.write_bytes(b"Source preview preserved")
    output = tmp_path / "staging"
    (output / "face").mkdir(parents=True)
    os.link(original, output / "face/index.html")
    monkeypatch.setattr(hooks._web, "load_web_contract", lambda: contract)
    hooks._stage_web(output, files)
    assert original.read_bytes() == b"Source preview preserved"
    assert (output / "face/index.html").read_bytes() == files["face/index.html"]


def test_pinned_ktx2_room_texture_ships_and_still_rejects_tampering():
    from scripts.distribution_web_assets import verify_web_assets

    contract, files = web_fixture()
    name = "face/public/assets/room/city.ktx2"
    payload = b"\xabKTX 20\xbb\r\n\x1a\ncompressed fixture"
    files[name] = payload
    contract["files"].append({"path": name, "size": len(payload),
                              "sha256": hashlib.sha256(payload).hexdigest()})
    contract["files"].sort(key=lambda item: item["path"])
    files["face/web-assets.json"] = json.dumps(contract).encode()
    verify_web_assets(files, contract)
    files[name] = payload + b"changed"
    with pytest.raises(DistributionError):
        verify_web_assets(files, contract)
