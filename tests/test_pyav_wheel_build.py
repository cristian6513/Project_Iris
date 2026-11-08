"""PyAV build boundaries; no compiler, native module or model is executed."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import os
import stat
import tarfile
import zipfile

import pytest

from scripts.build_pyav_wheel import (
    PIN_PATH,
    build_environment,
    check_new_output,
    extract_tar,
    install_tool,
    normalized_def,
    validate_pin,
    verify_input,
    verify_native_members,
    wheel_members,
)
from scripts.distribution_assets import DistributionError
from scripts.runtime_inventory import read_json


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("arch", "arm64"),
        ("platform", "linux"),
        ("ffmpeg_libraries", ["avcodec"]),
        ("source_date_epoch", 0),
        ("repair", {"mangle": False}),
        ("msvc", {"compile_flags": []}),
    ],
)
def test_unapproved_build_contracts_are_rejected(field, value):
    pin = copy.deepcopy(read_json(PIN_PATH))
    pin[field] = value
    with pytest.raises(DistributionError):
        validate_pin(pin)


def test_tool_versions_and_nonprimary_sources_are_rejected():
    pin = copy.deepcopy(read_json(PIN_PATH))
    pin["tools"][0]["version"] = "3.0.0"
    with pytest.raises(DistributionError):
        validate_pin(pin)
    pin = copy.deepcopy(read_json(PIN_PATH))
    pin["source"]["url"] = "https://untrusted.invalid/av.tar.gz"
    with pytest.raises(DistributionError):
        validate_pin(pin)


def test_source_hash_check_precedes_any_output(tmp_path):
    source = tmp_path / "source.tar.gz"
    source.write_bytes(b"changed source")
    with pytest.raises(DistributionError, match="hash"):
        verify_input(source, "0" * 64)
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize(
    "member",
    [
        "../outside",
        "/absolute",
        "C:/outside",
        "root/../outside",
        "root/file:stream",
        r"root\outside",
    ],
)
def test_tar_rejects_unsafe_names_before_creating_output(tmp_path, member):
    archive, output = tmp_path / "source.tar.gz", tmp_path / "new"
    with tarfile.open(archive, "w:gz") as tar:
        item = tarfile.TarInfo(member)
        item.size = 1
        tar.addfile(item, io.BytesIO(b"x"))
    with pytest.raises(DistributionError):
        extract_tar(archive, output, "root")
    assert not output.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_tar_rejects_links_and_special_files(tmp_path, kind):
    archive, output = tmp_path / "source.tar.gz", tmp_path / "new"
    with tarfile.open(archive, "w:gz") as tar:
        item = tarfile.TarInfo("root/linked")
        item.type, item.linkname = kind, "../outside"
        tar.addfile(item)
    with pytest.raises(DistributionError, match="Linked"):
        extract_tar(archive, output, "root")
    assert not output.exists()


def test_fresh_python_excludes_unpinned_archive_packages_but_preserves_sdk(tmp_path):
    archive, output = tmp_path / "python.tar.gz", tmp_path / "python"
    with tarfile.open(archive, "w:gz") as tar:
        for name in (
            "python/Lib/site-packages/setuptools/__init__.py",
            "python/include/Python.h",
            "python/libs/python3.lib",
        ):
            item = tarfile.TarInfo(name)
            item.size = 7
            tar.addfile(item, io.BytesIO(b"fixture"))
    extract_tar(archive, output, "python")
    assert not (output / "Lib/site-packages").exists()
    assert (output / "include/Python.h").read_bytes() == b"fixture"
    assert (output / "libs/python3.lib").read_bytes() == b"fixture"


def test_output_is_new_and_cannot_overlap_sources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(DistributionError, match="new"):
        check_new_output(source, [])
    with pytest.raises(DistributionError, match="separate"):
        check_new_output(source / "output", [source])
    assert list(source.iterdir()) == []


def test_build_environment_ignores_user_tools_headers_and_secrets(tmp_path, monkeypatch):
    for name in (
        "PATH",
        "INCLUDE",
        "LIB",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "OPENAI_API_KEY",
        "CPATH",
        "APPDATA",
        "USERPROFILE",
        "CL",
        "LINK",
    ):
        monkeypatch.setenv(name, "hostile-private-value")
    env = build_environment(
        tmp_path / "msvc", tmp_path / "sdk", tmp_path / "python", tmp_path / "output"
    )
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "msvc/bin/Hostx64/x64")
    assert env["PATH"].split(os.pathsep)[1].replace("\\", "/").endswith("bin/10.0.26100.0/x64")
    assert len(env["PATH"].split(os.pathsep)) == 2
    assert env["CL"] == "/Brepro"
    assert env["LINK"] == "/Brepro /DEBUG:NONE"
    assert env["SOURCE_DATE_EPOCH"] == "1790713770"
    assert "hostile-private-value" not in env.values()
    assert not {"PYTHONPATH", "VIRTUAL_ENV", "OPENAI_API_KEY", "CPATH"} & env.keys()


def test_def_copy_targets_actual_dll_and_preserves_data_exports():
    original = "LIBRARY unversioned.dll\r\nEXPORTS\r\nav_function\r\nav_data DATA\r\n"
    result = normalized_def(original, "avcodec-62.dll")
    assert result == "LIBRARY avcodec-62.dll\nEXPORTS\nav_function\nav_data DATA\n"
    assert original.startswith("LIBRARY unversioned.dll")
    with pytest.raises(DistributionError):
        normalized_def(original, "libx264.dll")


def make_wheel(path, members, *, wrong_hash=False):
    rows = []
    with zipfile.ZipFile(path, "w") as wheel:
        for name, data in members.items():
            wheel.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append([name, "sha256=" + ("wrong" if wrong_hash else digest), str(len(data))])
        record = "tool-1.0.dist-info/RECORD"
        rows.append([record, "", ""])
        content = io.StringIO(newline="")
        csv.writer(content, lineterminator="\n").writerows(rows)
        wheel.writestr(record, content.getvalue())


def test_invalid_tool_record_creates_no_site_packages(tmp_path):
    wheel, site = tmp_path / "tool.whl", tmp_path / "site"
    make_wheel(wheel, {"tool/__init__.py": b"x=1"}, wrong_hash=True)
    with pytest.raises(DistributionError, match="RECORD hash"):
        install_tool(wheel, site)
    assert not site.exists()


def test_linked_wheel_member_is_rejected(tmp_path):
    wheel = tmp_path / "link.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        item = zipfile.ZipInfo("tool/link")
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(item, "../outside")
    with zipfile.ZipFile(wheel) as archive:
        with pytest.raises(DistributionError, match="Linked"):
            wheel_members(archive)


def test_vendor_external_dlls_fail_before_native_loading(tmp_path):
    wheel = tmp_path / "wrong.whl"
    make_wheel(wheel, {"av.libs/libx264.dll": b"never executed", "av/_core.pyd": b"never executed"})
    with pytest.raises(DistributionError, match="seven"):
        verify_native_members(wheel, tmp_path)


def test_nested_vendor_records_remain_hashed_members_of_root_record(tmp_path):
    wheel, site = tmp_path / "tool.whl", tmp_path / "site"
    make_wheel(wheel, {"tool/__init__.py": b"x=1",
                       "tool/_vendor/lib-1.0.dist-info/RECORD": b"vendor-record"})
    install_tool(wheel, site)
    assert (site / "tool/_vendor/lib-1.0.dist-info/RECORD").read_bytes() == b"vendor-record"
