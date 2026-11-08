"""Exact reviewed dependency substitution; upstream Hermes source remains unchanged.

This is not a general compatibility allowlist. Every unrelated dependency is
still checked against its installed metadata and the complete hashed lock.
"""
from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

REVIEWED_EDGE_OVERRIDE = {
    "runtime": "hermes",
    "package": "edge-tts",
    "replaces": "7.2.7",
    "version": "7.3.1",
    "hermes_version": "0.21.6",
    "hermes_revision": "818c13be1dc4fd28987e1e881a9408224afd4535",
    "upstream_lock_sha256": "551c49cdcf829421870a4c9886211ad966923e8e08b14d85710180c50778abea",
    "lock_sha256": "9f8509f79ec66f2a879142d114110a7a197d35829ce3bf7cb97bccabf08234ba",
    "wheel_filename": "edge_tts-7.3.1-py3-none-any.whl",
    "wheel_sha256": "2a3c79d6235a7a736d681233823ce5492e829b43eb47beae49aa181237a8f9a4",
    "reason": "Microsoft rejects the pinned Edge-TTS client with HTTP 403; 7.3.1 is reviewed for Hermes voice generation on Windows CPython 3.14.8."
}
_OLD_BLOCK = "edge-tts==7.2.7 \\\n    --hash=sha256:0127fba57a742bc48ff0a2a3b24b8324f7859260185274c335b4e54735aff325 \\\n    --hash=sha256:ac11d9e834347e5ee62cbe72e8a56ffd65d3c4e795be14b1e593b72cf6480dd9\n"
_NEW_BLOCK = "edge-tts==7.3.1 \\\n    --hash=sha256:2a3c79d6235a7a736d681233823ce5492e829b43eb47beae49aa181237a8f9a4 \\\n    --hash=sha256:ee1fabf911b9ea83b38ae93aee5619ee41eaf0b109fef6e65d797dc4ebf1f20f\n"
_UPSTREAM = {
    "version": "0.21.6",
    "source_archive_sha256": "9deddf1d5325361b18f2281f21c4229fe699a62eec6fb809b3a5a83e0a1e88da",
    "revision": "818c13be1dc4fd28987e1e881a9408224afd4535",
    "pyproject_sha256": "cadde2f6a92574d292103f43abf66408b1d841cd9ea332ca7f3e3dfc5dcc87fd",
    "lock_sha256": "2bc4db483e9cba927cd7147f9ccbb09635f85bb3f1f36d6e9d51d06b65d1c67e",
}


def reviewed_dependency_overrides(pin: dict) -> list[dict]:
    items = pin.get("dependency_overrides", [])
    if not isinstance(items, list):
        raise ValueError("Invalid runtime dependency overrides")
    hermes = pin.get("hermes") or {}
    digest = ((pin.get("locks") or {}).get("hermes") or {}).get("sha256")
    known_revision = all(hermes.get(key) == value for key, value in _UPSTREAM.items())
    if known_revision and digest not in {
        REVIEWED_EDGE_OVERRIDE["upstream_lock_sha256"], REVIEWED_EDGE_OVERRIDE["lock_sha256"],
    }:
        raise ValueError("Unreviewed Hermes Windows dependency lock")
    if not items:
        if digest == REVIEWED_EDGE_OVERRIDE["lock_sha256"]:
            raise ValueError("Hermes Edge-TTS replacement requires its reviewed override")
        return []
    if items != [REVIEWED_EDGE_OVERRIDE]:
        raise ValueError("Not an exact reviewed runtime dependency override")
    if any(hermes.get(key) != value for key, value in _UPSTREAM.items()):
        raise ValueError("Dependency override belongs to a different Hermes source")
    if (pin.get("platform"), pin.get("arch")) != ("windows", "x86_64"):
        raise ValueError("Dependency override requires the reviewed Windows runtime")
    if (pin.get("hermes_python") or {}).get("version") != "3.14.8":
        raise ValueError("Dependency override requires the reviewed Hermes Python")
    if digest != REVIEWED_EDGE_OVERRIDE["lock_sha256"]:
        raise ValueError("Dependency override does not match the complete locked payload")
    return [dict(REVIEWED_EDGE_OVERRIDE)]


def verify_dependency_overrides(pin: dict, lock_text: str, source: Path | None = None) -> list[dict]:
    reviewed = reviewed_dependency_overrides(pin)
    if not reviewed:
        return []
    digest = hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
    if digest != reviewed[0]["lock_sha256"] or lock_text.count(_NEW_BLOCK) != 1:
        raise ValueError("Dependency override lock bytes differ from the reviewed replacement")
    upstream = lock_text.replace(_NEW_BLOCK, _OLD_BLOCK, 1)
    if hashlib.sha256(upstream.encode("utf-8")).hexdigest() != reviewed[0]["upstream_lock_sha256"]:
        raise ValueError("Dependency override changed more than the reviewed Edge-TTS block")
    if source is not None:
        payload = (source / "pyproject.toml").read_bytes()
        if hashlib.sha256(payload).hexdigest() != _UPSTREAM["pyproject_sha256"]:
            raise ValueError("Upstream Hermes pyproject changed under a dependency override")
        data = tomllib.loads(payload.decode("utf-8"))
        dependencies = data.get("project", {}).get("optional-dependencies", {}).get("edge-tts")
        if dependencies != ["edge-tts==7.2.7"]:
            raise ValueError("Upstream Hermes Edge-TTS requirement was not preserved")
    return reviewed
