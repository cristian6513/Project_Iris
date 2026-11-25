"""Reviewed Edge-TTS substitution stays confined to one Hermes revision and lock.

The substitution belongs to Hermes 0.21.6 (QA22–QA26), whose pinned Edge-TTS client Microsoft
refuses. Hermes 0.23.3 (QA27, 2026-11-20) ships Edge-TTS 7.3.1 upstream, so the current pin
declares no override; the policy stays so a QA26 runtime can still be verified. QA26's pin is
rebuilt here from the reviewed values, and its upstream lock is a byte-exact test fixture.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import prepare_private_runtime as builder
from scripts.runtime_dependency_overrides import (
    _NEW_BLOCK, _OLD_BLOCK, _UPSTREAM, REVIEWED_EDGE_OVERRIDE, reviewed_dependency_overrides,
    verify_dependency_overrides,
)
from scripts.runtime_inventory import validate_pin

ROOT = Path(__file__).resolve().parents[1]
QA26_UPSTREAM_LOCK = Path(__file__).with_name("fixtures") / "hermes-0.21.6-windows-cp314.txt"


def real_pin():
    return json.loads((ROOT / "scripts/runtime_sources.json").read_text("utf-8"))


def qa26_pin():
    """The current pin with QA26's Hermes 0.21.6 source, lock and reviewed override."""
    pin = real_pin()
    pin["hermes"].update(copy.deepcopy(_UPSTREAM))
    pin["hermes"].pop("source_patches", None)
    pin["locks"]["hermes"]["sha256"] = REVIEWED_EDGE_OVERRIDE["lock_sha256"]
    pin["dependency_overrides"] = [copy.deepcopy(REVIEWED_EDGE_OVERRIDE)]
    return pin


def qa26_lock():
    upstream = QA26_UPSTREAM_LOCK.read_bytes().decode("utf-8")
    assert hashlib.sha256(upstream.encode("utf-8")).hexdigest() == REVIEWED_EDGE_OVERRIDE["upstream_lock_sha256"]
    reviewed = upstream.replace(_OLD_BLOCK, _NEW_BLOCK, 1)
    assert hashlib.sha256(reviewed.encode("utf-8")).hexdigest() == REVIEWED_EDGE_OVERRIDE["lock_sha256"]
    return reviewed


def test_the_current_hermes_needs_no_override_and_refuses_the_old_one():
    pin = real_pin()
    lock = (ROOT / "scripts/locks/hermes-windows-cp314.txt").read_text("utf-8")
    assert pin["dependency_overrides"] == []
    assert verify_dependency_overrides(pin, lock) == []
    assert "edge-tts==7.3.1 \\" in lock  # upstream's own pin since Hermes 0.23
    pin["dependency_overrides"] = [copy.deepcopy(REVIEWED_EDGE_OVERRIDE)]
    with pytest.raises(ValueError, match="different Hermes source"):
        reviewed_dependency_overrides(pin)


def test_the_qa26_reviewed_lock_changes_only_edge_and_keeps_upstream_source_pins():
    pin = qa26_pin()
    assert verify_dependency_overrides(pin, qa26_lock()) == [REVIEWED_EDGE_OVERRIDE]
    assert pin["hermes"]["lock_sha256"] == "2bc4db483e9cba927cd7147f9ccbb09635f85bb3f1f36d6e9d51d06b65d1c67e"
    assert pin["hermes"]["pyproject_sha256"] == "cadde2f6a92574d292103f43abf66408b1d841cd9ea332ca7f3e3dfc5dcc87fd"


@pytest.mark.parametrize("field,value", [
    ("version", "7.3.2"), ("replaces", "7.2.8"), ("runtime", "iris"),
    ("hermes_revision", "a" * 40), ("wheel_sha256", "0" * 64),
    ("wheel_filename", "some-other-wheel.whl"),
])
def test_a_different_dependency_or_artifact_is_not_implicitly_approved(field, value):
    pin = qa26_pin()
    pin["dependency_overrides"][0][field] = value
    with pytest.raises(ValueError, match="exact reviewed"):
        validate_pin(pin)


@pytest.mark.parametrize("field,value", [
    ("revision", "a" * 40), ("version", "0.21.7"),
    ("pyproject_sha256", "0" * 64), ("lock_sha256", "0" * 64),
])
def test_a_new_hermes_source_needs_its_own_review(field, value):
    pin = qa26_pin()
    pin["hermes"][field] = value
    with pytest.raises(ValueError, match="different Hermes source"):
        reviewed_dependency_overrides(pin)


def test_an_upgrade_cannot_omit_or_duplicate_its_reviewed_declaration():
    pin = qa26_pin()
    pin.pop("dependency_overrides")
    with pytest.raises(ValueError, match="requires its reviewed"):
        reviewed_dependency_overrides(pin)
    pin = qa26_pin()
    pin["dependency_overrides"] *= 2
    with pytest.raises(ValueError, match="exact reviewed"):
        reviewed_dependency_overrides(pin)


def test_the_preceding_candidate_remains_verifiable_without_an_override():
    pin = qa26_pin()
    pin.pop("dependency_overrides")
    pin["locks"]["hermes"]["sha256"] = REVIEWED_EDGE_OVERRIDE["upstream_lock_sha256"]
    assert reviewed_dependency_overrides(pin) == []


def test_unrelated_lock_edits_and_changed_upstream_requirements_are_rejected(tmp_path):
    pin = qa26_pin()
    lock = qa26_lock()
    with pytest.raises(ValueError, match="lock bytes"):
        verify_dependency_overrides(pin, lock.replace("aiohttp==", "aiohttp==0.", 1))
    (tmp_path / "pyproject.toml").write_text('[project.optional-dependencies]\nedge-tts=["edge-tts>=7"]\n')
    with pytest.raises(ValueError, match="pyproject changed"):
        verify_dependency_overrides(pin, lock, tmp_path)


def test_override_does_not_approve_another_interpreter_or_runtime():
    pin = qa26_pin()
    pin["hermes_python"]["version"] = "3.14.9"
    with pytest.raises(ValueError, match="reviewed Hermes Python"):
        reviewed_dependency_overrides(pin)
    pin = qa26_pin()
    pin["platform"] = "linux"
    with pytest.raises(ValueError, match="Windows runtime"):
        reviewed_dependency_overrides(pin)


def test_builder_refuses_an_unreviewed_override_before_creating_output(tmp_path):
    pin = qa26_pin()
    pin["dependency_overrides"][0]["version"] = "7.4.0"
    output = tmp_path / "candidate"
    with pytest.raises(ValueError, match="exact reviewed"):
        builder.prepare_runtime(output, tmp_path / "cache", tmp_path / "iris.whl", tmp_path / "uv.exe", pin, offline=True)
    assert not output.exists()
