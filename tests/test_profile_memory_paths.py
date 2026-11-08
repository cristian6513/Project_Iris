"""Custom SQLite/WAL destinations must not write the packaged installation."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import IRISConfig
from core.profile_paths import ProfilePathError


@pytest.fixture
def packaged(monkeypatch, tmp_path):
    from core import runtime_descriptor

    base = tmp_path / "installation"
    base.mkdir()
    monkeypatch.setattr(
        runtime_descriptor,
        "get_runtime",
        lambda: SimpleNamespace(
            packaged=True,
            base=base,
        ),
    )
    return base


@pytest.mark.parametrize("inside", ["memory.db", "data/memory.db"])
def test_custom_memory_inside_installation_is_rejected_before_sqlite(packaged, inside):
    path = packaged / inside
    config = IRISConfig.model_construct(memory_db_path=str(path))
    with pytest.raises(ProfilePathError, match="fuera"):
        config.get_memory_db_path()
    assert not path.exists()


def test_packaged_relative_memory_is_rejected_but_development_preserves_it(packaged, monkeypatch):
    from core import runtime_descriptor

    config = IRISConfig.model_construct(memory_db_path="../memory.db")
    with pytest.raises(ProfilePathError, match="absoluta"):
        config.get_memory_db_path()
    monkeypatch.setattr(runtime_descriptor, "get_runtime", lambda: SimpleNamespace(packaged=False))
    assert config.get_memory_db_path() == Path("../memory.db")


def test_absolute_custom_memory_outside_installation_is_preserved(packaged, tmp_path):
    path = tmp_path / "profile" / "memory.db"
    assert IRISConfig.model_construct(memory_db_path=str(path)).get_memory_db_path() == path
    assert not path.exists()


def test_custom_memory_hardlink_cannot_write_an_installation_file(packaged, tmp_path):
    original = packaged / "memory.db"
    original.write_bytes(b"EXISTING-DATABASE")
    alias = tmp_path / "alias.db"
    os.link(original, alias)
    with pytest.raises(ProfilePathError, match="enlazado"):
        IRISConfig.model_construct(memory_db_path=str(alias)).get_memory_db_path()
    assert original.read_bytes() == b"EXISTING-DATABASE"


def test_custom_memory_directory_alias_into_installation_is_rejected(packaged, tmp_path):
    original = packaged / "memory.db"
    original.write_bytes(b"EXISTING-DATABASE")
    alias = tmp_path / "profile-alias"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(packaged), str(alias))
    else:
        alias.symlink_to(packaged, target_is_directory=True)
    try:
        with pytest.raises(ProfilePathError, match="fuera"):
            IRISConfig.model_construct(memory_db_path=str(alias / "memory.db")).get_memory_db_path()
        assert original.read_bytes() == b"EXISTING-DATABASE"
    finally:
        if os.name == "nt":
            alias.rmdir()  # Remove only the junction, never the installation.
        else:
            alias.unlink()
