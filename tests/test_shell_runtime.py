"""Private-shell extraction never widens the verified member list or overwrites files."""
import hashlib
import json
import zipfile

import pytest
from scripts import prepare_private_runtime as builder


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def shell_input(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    manifest = {"files": {"usr/bin/bash.exe": "unused", "etc/fstab": "unused"},
                "sources": [{"notices": {"COPYING.txt": "unused"}}]}
    raw = json.dumps(manifest).encode()
    (root / "scripts/shell_runtime_sources.json").write_bytes(raw)
    monkeypatch.setattr(builder, "ROOT", root)
    entries = {"tools/shell/usr/bin/bash.exe": b"synthetic", "tools/shell/etc/fstab": b"mounts",
               "licenses/shell/COPYING.txt": b"license", "licenses/shell/README.txt": b"notice",
               "licenses/shell/sources.json": raw, "licenses/shell/components.json": b'{"licenses": []}'}
    return tmp_path, entries, {"build_sources_sha256": digest(raw)}


def pack(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return digest(path.read_bytes())


def test_private_shell_extracts_exact_members_and_lists_executables(shell_input):
    root, entries, spec = shell_input
    spec["sha256"] = pack(root / "shell.zip", entries)
    stage = root / "stage"
    licenses, executables = builder.extract_shell(root / "shell.zip", stage, spec)
    assert licenses == [] and executables == ["tools/shell/usr/bin/bash.exe"]
    assert (stage / executables[0]).read_bytes() == b"synthetic"


@pytest.mark.parametrize("name", ["../outside.exe", "tools/shell/usr/bin/other.exe", "licenses/shell/secret.txt", "tools/shell/usr/bin/alias/../bash.exe"])
def test_unlisted_or_escaping_shell_members_rejected_before_writes(shell_input, name):
    root, entries, spec = shell_input
    entries[name] = b"bad"
    spec["sha256"] = pack(root / "shell.zip", entries)
    with pytest.raises(ValueError):
        builder.extract_shell(root / "shell.zip", root / "stage", spec)
    assert not (root / "stage").exists()


def test_missing_member_hash_change_and_existing_file_are_rejected(shell_input):
    root, entries, spec = shell_input
    payload = entries.pop("tools/shell/etc/fstab")
    spec["sha256"] = pack(root / "missing.zip", entries)
    with pytest.raises(ValueError, match="incomplete"):
        builder.extract_shell(root / "missing.zip", root / "stage", spec)
    entries["tools/shell/etc/fstab"] = payload
    spec["sha256"] = pack(root / "shell.zip", entries)
    with pytest.raises(ValueError, match="hash mismatch"):
        builder.extract_shell(root / "shell.zip", root / "stage", {**spec, "sha256": "0" * 64})
    (root / "stage/tools/shell/etc").mkdir(parents=True)
    (root / "stage/tools/shell/etc/fstab").write_bytes(b"keep")
    with pytest.raises(ValueError, match="replace existing"):
        builder.extract_shell(root / "shell.zip", root / "stage", spec)
    assert (root / "stage/tools/shell/etc/fstab").read_bytes() == b"keep"
