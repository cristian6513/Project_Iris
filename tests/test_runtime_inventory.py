"""A08.4 clean-runtime integrity and privacy tests; no executable is launched."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import struct

import pytest

from scripts.distribution_assets import DistributionError
from scripts.runtime_inventory import (
    INVENTORY_NAME,
    PIN_NAME,
    _link_check,
    build_inventory,
    read_json,
    validate_inventory,
    verify_runtime,
)
from scripts.verify_runtime import main


def pe(machine=0x8664):
    payload = bytearray(128)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 60, 64)
    payload[64:68] = b"PE\0\0"
    struct.pack_into("<H", payload, 68, machine)
    return bytes(payload)


def source_files():
    return {
        "locks/iris-pyproject.toml": b"# synthetic Iris metadata\n",
        "locks/iris-uv.lock": b"# synthetic Iris source lock\n",
        "hermes/pyproject.toml": b"# synthetic Hermes metadata\n",
        "hermes/uv.lock": b"# synthetic Hermes source lock\n",
        "locks/iris.txt": b"fixture==1.0.0 --hash=sha256:" + b"1" * 64,
        "locks/hermes.txt": b"fixture==1.0.0 --hash=sha256:" + b"2" * 64,
    }


def fixture_pin():
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in source_files().items()}
    return {
        "schema": 1,
        "platform": "windows",
        "arch": "x86_64",
        "python": {
            "version": "3.11.15",
            "url": "https://example.test/python.tar.gz",
            "sha256": "a" * 64,
        },
        "iris": {
            "version": "1.0.0",
            "pyproject_sha256": hashes["locks/iris-pyproject.toml"],
            "uv_lock_sha256": hashes["locks/iris-uv.lock"],
        },
        "hermes": {
            "version": "0.21.4",
            "revision": "b" * 40,
            "pyproject_sha256": hashes["hermes/pyproject.toml"],
            "lock_sha256": hashes["hermes/uv.lock"],
        },
        "audio": {"version": "7.1.1", "url": "https://example.test/audio.zip", "sha256": "e" * 64},
        "locks": {
            name: {"path": f"locks/{name}.txt", "sha256": hashes[f"locks/{name}.txt"]}
            for name in ("iris", "hermes")
        },
    }


@pytest.fixture
def stage(tmp_path):
    root = tmp_path / "Instalación Iris ñ con espacios"
    files = {
        "runtimes/iris/python.exe": pe(),
        "runtimes/hermes/python.exe": pe(),
        "runtimes/iris/Lib/site-packages/fake/__init__.py": b"# no real runtime\n",
        "runtimes/iris/Lib/site-packages/fake/native.pyd": pe(),
        "runtimes/hermes/Lib/site-packages/fake/native.dll": pe(),
        "hermes/hermes_cli/__init__.py": b"# no real Hermes\n",
        "tools/audio/bin/ffmpeg.exe": pe(),
        "tools/audio/bin/ffprobe.exe": pe(),
        "tools/audio/bin/avcodec.dll": pe(),
        "licenses/FIXTURE.txt": b"Synthetic fixture notice, not a runtime license.\n",
        **source_files(),
        PIN_NAME: json.dumps(fixture_pin()).encode(),
    }
    for name, payload in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    allowed = sorted(name for name in files if name.endswith(".exe"))
    licenses = [
        {
            "component": "fixture",
            "package": "fixture",
            "version": "1.0.0",
            "license": "MIT",
            "files": ["licenses/FIXTURE.txt"],
            "source": "https://example.test/license",
        }
    ]
    document = build_inventory(root, fixture_pin(), licenses, allowed)
    (root / INVENTORY_NAME).write_text(json.dumps(document), "utf-8")
    return root, document, files, licenses, allowed


def save(root, document):
    (root / INVENTORY_NAME).write_text(json.dumps(document), "utf-8")


def test_runtime_inventory_verifies_portable_unicode_stage_and_does_not_modify_it(
    stage, monkeypatch, tmp_path
):
    root, document, files, _, _ = stage
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile"))
    before = {name: (root / name).read_bytes() for name in files}
    assert verify_runtime(root, expected_pin=fixture_pin()) == document
    assert {name: (root / name).read_bytes() for name in files} == before
    assert INVENTORY_NAME not in {item["path"] for item in document["files"]}


@pytest.mark.parametrize(
    "name",
    [
        "/runtimes/iris/python.exe",
        "C:/runtimes/iris/python.exe",
        "C:python.exe",
        "../python.exe",
        "runtimes/../python.exe",
        "runtimes/./python.exe",
        r"runtimes\iris\python.exe",
        r"\\host\share\python.exe",
        "//host/share/python.exe",
        "runtimes/iris/python.exe:secret",
        "runtimes//iris/python.exe",
        "runtimes/iris/",
        "runtimes/iris/trailing.",
        "runtimes/iris/trailing ",
        "runtimes/iris/CON .py",
        "runtimes/iris/NUL.py",
        "runtimes/iris/LPT².py",
        "runtimes/iris/CLOCK$.py",
        "runtimes/iris/%TEMP%/test.py",
        "runtimes/iris/?.py",
        "runtimes/iris/\x00test.py",
        "runtimes/iris/\x85test.py",
        "runtimes/iris/\ud800.py",
        "",
    ],
)
def test_inventory_rejects_absolute_traversal_windows_and_ambiguous_paths(stage, name):
    _, document, _, _, _ = stage
    document = copy.deepcopy(document)
    document["files"][0]["path"] = name
    with pytest.raises(DistributionError):
        validate_inventory(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("component", "other"),
        ("platform", "linux"),
        ("architecture", "x86"),
        ("hash_method", "md5"),
        ("hash_method", "unknown"),
        ("files", []),
        ("executables", []),
        ("licenses", []),
    ],
)
def test_unknown_schema_platform_hash_methods_and_missing_inventories_are_rejected(
    stage, field, value
):
    _, document, _, _, _ = stage
    document = copy.deepcopy(document)
    document[field] = value
    with pytest.raises(DistributionError):
        validate_inventory(document)


@pytest.mark.parametrize("change", ["missing", "extra", "hash", "size", "pin", "revision"])
def test_verifier_rejects_missing_extra_corruption_and_wrong_revision(stage, change):
    root, document, _, _, _ = stage
    target = root / "hermes/hermes_cli/__init__.py"
    if change == "missing":
        target.unlink()
    elif change == "extra":
        (root / "hermes/extra.py").write_bytes(b"unexpected")
    elif change == "hash":
        target.write_bytes(b"# modified Hermes!\n")
    elif change == "size":
        document["files"][0]["size"] += 1
        save(root, document)
    else:
        pin = fixture_pin()
        pin["hermes"]["revision"] = "0" * 40
        if change == "pin":
            (root / PIN_NAME).write_text(json.dumps(pin), "utf-8")
        else:
            document["pin"] = pin
            save(root, document)
    with pytest.raises(DistributionError):
        verify_runtime(root, expected_pin=fixture_pin())


@pytest.mark.parametrize(
    "name",
    [
        "runtimes/iris/pyvenv.cfg",
        "runtimes/hermes/Lib/__pycache__/module.pyc",
        "hermes/.git/config",
        "hermes/.env",
        "hermes/.env.example",
        "hermes/auth.json",
        "hermes/tokens.json",
        "hermes/credentials-google.json",
        "hermes/client_secret_fixture.json",
        "hermes/.hermes/settings.yaml",
        "hermes/profiles/default/config.yaml",
        "hermes/profile/settings.json",
        "hermes/state.db",
        "hermes/debug.log",
        "hermes/.cache/model.json",
        "runtimes/iris/Lib/site-packages/private.pem",
        "hermes/server.key",
        "hermes/avatars/iris.vrm",
    ],
)
def test_private_content_cannot_be_blessed_by_generating_a_new_inventory(stage, name):
    root, _, _, licenses, allowed = stage
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SYNTHETIC PRIVATE FIXTURE MUST NOT SHIP")
    with pytest.raises(DistributionError, match="(?:Private|private)"):
        build_inventory(root, fixture_pin(), licenses, allowed)


@pytest.mark.parametrize(
    "name",
    [
        "runtimes/iris/Lib/site-packages/botocore/data/logs/2014-03-28/service-2.json.gz",
        "runtimes/hermes/Lib/site-packages/other/data/logs/service-2.json",
        "runtimes/hermes/Lib/site-packages/botocore/data/logs/2014-03-28/server.key",
        "runtimes/hermes/Lib/site-packages/openai/types/realtime/client_secret_other.py",
        "runtimes/iris/Lib/site-packages/openai/types/realtime/client_secret_create_params.py",
        "runtimes/iris/Lib/site-packages/botocore/cacert.pem",
        "runtimes/hermes/Lib/site-packages/other/cacert.pem",
        "runtimes/iris/Lib/site-packages/face/animations/private.vrm",
        "runtimes/iris/Lib/site-packages/face/avatars/iris.vrm",
        "runtimes/iris/Lib/site-packages/face/animations/sub/iris.vrm",
        "runtimes/hermes/Lib/site-packages/face/animations/iris.vrm",
    ],
)
def test_public_data_exemptions_do_not_spread_to_other_paths(stage, name):
    root, _, _, licenses, allowed = stage
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SYNTHETIC PRIVATE FIXTURE MUST NOT SHIP")
    with pytest.raises(DistributionError, match="(?:Private|private)"):
        build_inventory(root, fixture_pin(), licenses, allowed)


def test_exact_public_wheel_data_resembling_private_names_is_allowed(stage):
    root, _, _, licenses, allowed = stage
    site = "runtimes/hermes/Lib/site-packages/"
    for name in (
        "botocore/cacert.pem",
        "botocore/data/logs/2014-03-28/service-2.json.gz",
        "botocore/data/logs/2014-03-28/paginators-1.json",
        "openai/types/realtime/client_secret_create_params.py",
        "openai/types/realtime/client_secret_create_response.py",
    ):
        path = root / (site + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PUBLIC SYNTHETIC FIXTURE")
    document = build_inventory(root, fixture_pin(), licenses, allowed)
    names = {item["path"] for item in document["files"]}
    assert site + "botocore/cacert.pem" in names
    assert site + "openai/types/realtime/client_secret_create_params.py" in names


def test_iris_factory_avatar_is_the_only_model_the_iris_wheel_may_carry(stage):
    """2026-10-28: IRIS' VRM ships at its exact installed path, named by the asset contract."""
    from scripts.distribution_assets import load_contract
    from scripts.runtime_inventory import _PUBLIC_FACTORY_AVATAR

    assert _PUBLIC_FACTORY_AVATAR.endswith("/face/animations/" + load_contract()["avatar"]["filename"])
    root, _, _, licenses, allowed = stage
    name = "runtimes/iris/Lib/site-packages/face/animations/iris.vrm"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PUBLIC SYNTHETIC FIXTURE")
    document = build_inventory(root, fixture_pin(), licenses, allowed)
    assert name in {item["path"] for item in document["files"]}


def test_public_cacert_and_source_credentials_module_are_allowed(stage):
    root, _, _, licenses, allowed = stage
    for name in (
        "runtimes/iris/Lib/site-packages/certifi/cacert.pem",
        "hermes/hermes_cli/credentials.py",
        "runtimes/iris/python311.zip",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"PUBLIC SYNTHETIC FIXTURE")
    document = build_inventory(root, fixture_pin(), licenses, allowed)
    save(root, document)
    assert verify_runtime(root, expected_pin=fixture_pin()) == document


@pytest.mark.parametrize("change", ["unapproved", "duplicate", "missing-python", "wrong-case"])
def test_executables_require_explicit_exact_authorization(stage, change):
    root, _, _, licenses, allowed = stage
    if change == "unapproved":
        (root / "hermes/unapproved.exe").write_bytes(pe())
    elif change == "duplicate":
        allowed += [allowed[0]]
    elif change == "wrong-case":
        allowed[0] = allowed[0].upper()
    else:
        allowed.remove("runtimes/hermes/python.exe")
    with pytest.raises(DistributionError, match="(?:executables|interpreter)"):
        build_inventory(root, fixture_pin(), licenses, allowed)


@pytest.mark.parametrize("suffix", ["exe", "dll", "pyd"])
@pytest.mark.parametrize("payload", [pe(0x014C), pe(0xAA64), b"MZnot-a-real-PE", b"not-PE"])
def test_every_runtime_binary_must_be_windows_x64(stage, suffix, payload):
    root, _, _, licenses, allowed = stage
    name = f"runtimes/iris/wrong.{suffix}"
    (root / name).write_bytes(payload)
    if suffix == "exe":
        allowed += [name]
    with pytest.raises(DistributionError, match="(?:PE|architecture)"):
        build_inventory(root, fixture_pin(), licenses, allowed)


def test_inventory_rejects_case_aliases_and_wrong_component(stage):
    _, document, _, _, _ = stage
    alias = copy.deepcopy(document)
    alias["files"].append(dict(alias["files"][0], path=alias["files"][0]["path"].upper()))
    with pytest.raises(DistributionError):
        validate_inventory(alias)
    wrong = copy.deepcopy(document)
    wrong["files"][0]["component"] = "audio"
    with pytest.raises(DistributionError, match="component"):
        validate_inventory(wrong)


@pytest.mark.parametrize("kind", ["hardlink", "junction", "root-junction"])
def test_verifier_rejects_links_and_keeps_external_source(stage, tmp_path, kind):
    root, _, _, _, _ = stage
    target = root / "hermes/hermes_cli/__init__.py"
    original = target.read_bytes()
    junction = None
    if kind == "hardlink":
        outside = tmp_path / "outside.py"
        target.rename(outside)
        os.link(outside, target)
    else:
        outside = tmp_path / "outside-directory"
        if kind == "root-junction":
            root.rename(outside)
            junction = root
        else:
            target.parent.rename(outside)
            junction = target.parent
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside), str(junction))
        else:
            junction.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(DistributionError, match="(?:Linked|Hardlinked)"):
            verify_runtime(root, expected_pin=fixture_pin())
        actual = (
            outside
            if kind == "hardlink"
            else outside
            / ("hermes/hermes_cli/__init__.py" if kind == "root-junction" else "__init__.py")
        )
        assert actual.read_bytes() == original
    finally:
        if junction is not None:
            junction.rmdir() if os.name == "nt" else junction.unlink()


def test_symlink_type_is_always_rejected_before_any_read(tmp_path):
    fake = os.stat_result((stat.S_IFLNK | 0o777, 1, 1, 1, 0, 0, 0, 0, 0, 0))
    with pytest.raises(DistributionError, match="Linked"):
        _link_check(tmp_path / "link", fake)


@pytest.mark.parametrize("kind", ["inventory", "pin"])
def test_inventory_and_pin_cannot_be_hardlinked(stage, tmp_path, kind):
    root, _, _, _, _ = stage
    target = root / (INVENTORY_NAME if kind == "inventory" else PIN_NAME)
    outside = tmp_path / f"external-{kind}.json"
    target.rename(outside)
    os.link(outside, target)
    with pytest.raises(DistributionError, match="Hardlinked"):
        verify_runtime(root, expected_pin=fixture_pin())


@pytest.mark.parametrize(
    "document",
    [
        '{"schema":1,"schema":1}',
        '{"value":NaN}',
        '{"value":Infinity}',
        "[]",
        "null",
        "\ufeff{}",
        "not JSON",
    ],
)
def test_json_reader_rejects_duplicates_nonfinite_values_and_nonobjects(tmp_path, document):
    path = tmp_path / "inventory.json"
    path.write_text(document, "utf-8")
    with pytest.raises(DistributionError):
        read_json(path)


def test_json_reader_is_bounded(tmp_path):
    path = tmp_path / "large.json"
    path.write_text('{"value":"' + "x" * 100 + '"}', "utf-8")
    with pytest.raises(DistributionError, match="size limit"):
        read_json(path, maximum=32)


def test_cli_uses_independent_pin_and_unknown_operations_are_rejected(stage, tmp_path, capsys):
    root, _, _, _, _ = stage
    approved = tmp_path / "approved-pin.json"
    approved.write_text(json.dumps(fixture_pin()), "utf-8")
    assert main([str(root), "--pin", str(approved)]) == 0
    assert "private runtime verified" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([str(root), "--method", "unknown"])


def test_inventory_license_notice_must_exist_and_be_hashed(stage):
    root, document, _, _, _ = stage
    document["licenses"][0]["files"] = ["licenses/MISSING.txt"]
    with pytest.raises(DistributionError, match="license"):
        validate_inventory(document)
    document["licenses"][0]["files"] = ["licenses/FIXTURE.txt"]
    (root / "licenses/FIXTURE.txt").write_bytes(b"tampered notice")
    with pytest.raises(DistributionError, match="hash"):
        verify_runtime(root, expected_pin=fixture_pin())


@pytest.mark.parametrize("name", list(source_files()))
def test_regenerating_inventory_cannot_approve_modified_source_metadata_or_locks(stage, name):
    root, _, _, licenses, allowed = stage
    (root / name).write_bytes(b"corrupted source metadata or lock")
    with pytest.raises(DistributionError, match="approved pin"):
        build_inventory(root, fixture_pin(), licenses, allowed)
