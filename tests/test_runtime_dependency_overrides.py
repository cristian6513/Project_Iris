"""Reviewed Edge-TTS substitution stays confined to one Hermes revision and lock."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import prepare_private_runtime as builder
from scripts.runtime_dependency_overrides import (
    REVIEWED_EDGE_OVERRIDE, reviewed_dependency_overrides, verify_dependency_overrides,
)
from scripts.runtime_inventory import validate_pin

ROOT = Path(__file__).resolve().parents[1]


def real_pin():
    return json.loads((ROOT / "scripts/runtime_sources.json").read_text("utf-8"))


def test_the_real_reviewed_lock_changes_only_edge_and_keeps_upstream_source_pins():
    pin = real_pin()
    lock = (ROOT / "scripts/locks/hermes-windows-cp314.txt").read_text("utf-8")
    assert verify_dependency_overrides(pin, lock) == [REVIEWED_EDGE_OVERRIDE]
    assert pin["hermes"]["lock_sha256"] == "2bc4db483e9cba927cd7147f9ccbb09635f85bb3f1f36d6e9d51d06b65d1c67e"
    assert pin["hermes"]["pyproject_sha256"] == "cadde2f6a92574d292103f43abf66408b1d841cd9ea332ca7f3e3dfc5dcc87fd"


@pytest.mark.parametrize("field,value", [
    ("version", "7.3.2"), ("replaces", "7.2.8"), ("runtime", "iris"),
    ("hermes_revision", "a" * 40), ("wheel_sha256", "0" * 64),
    ("wheel_filename", "some-other-wheel.whl"),
])
def test_a_different_dependency_or_artifact_is_not_implicitly_approved(field, value):
    pin = real_pin()
    pin["dependency_overrides"][0][field] = value
    with pytest.raises(ValueError, match="exact reviewed"):
        validate_pin(pin)


@pytest.mark.parametrize("field,value", [
    ("revision", "a" * 40), ("version", "0.21.7"),
    ("pyproject_sha256", "0" * 64), ("lock_sha256", "0" * 64),
])
def test_a_new_hermes_source_needs_its_own_review(field, value):
    pin = real_pin()
    pin["hermes"][field] = value
    with pytest.raises(ValueError, match="different Hermes source"):
        reviewed_dependency_overrides(pin)


def test_an_upgrade_cannot_omit_or_duplicate_its_reviewed_declaration():
    pin = real_pin()
    pin.pop("dependency_overrides")
    with pytest.raises(ValueError, match="requires its reviewed"):
        reviewed_dependency_overrides(pin)
    pin = real_pin()
    pin["dependency_overrides"] *= 2
    with pytest.raises(ValueError, match="exact reviewed"):
        reviewed_dependency_overrides(pin)


def test_the_preceding_candidate_remains_verifiable_without_an_override():
    pin = real_pin()
    pin.pop("dependency_overrides")
    pin["locks"]["hermes"]["sha256"] = REVIEWED_EDGE_OVERRIDE["upstream_lock_sha256"]
    assert reviewed_dependency_overrides(pin) == []


def test_unrelated_lock_edits_and_changed_upstream_requirements_are_rejected(tmp_path):
    pin = real_pin()
    lock = (ROOT / "scripts/locks/hermes-windows-cp314.txt").read_text("utf-8")
    with pytest.raises(ValueError, match="lock bytes"):
        verify_dependency_overrides(pin, lock.replace("aiohttp==", "aiohttp==0.", 1))
    (tmp_path / "pyproject.toml").write_text('[project.optional-dependencies]\nedge-tts=["edge-tts>=7"]\n')
    with pytest.raises(ValueError, match="pyproject changed"):
        verify_dependency_overrides(pin, lock, tmp_path)


def test_override_does_not_approve_another_interpreter_or_runtime():
    pin = real_pin()
    pin["hermes_python"]["version"] = "3.14.9"
    with pytest.raises(ValueError, match="reviewed Hermes Python"):
        reviewed_dependency_overrides(pin)
    pin = real_pin()
    pin["platform"] = "linux"
    with pytest.raises(ValueError, match="Windows runtime"):
        reviewed_dependency_overrides(pin)


def test_builder_refuses_an_unreviewed_override_before_creating_output(tmp_path):
    pin = real_pin()
    pin["dependency_overrides"][0]["version"] = "7.4.0"
    output = tmp_path / "candidate"
    with pytest.raises(ValueError, match="exact reviewed"):
        builder.prepare_runtime(output, tmp_path / "cache", tmp_path / "iris.whl", tmp_path / "uv.exe", pin, offline=True)
    assert not output.exists()
