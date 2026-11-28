"""Shared test setup.

Core and the wizard refuse local voice engines a PC cannot run (core/voice_requirements.py).
Tests must not depend on the RAM, disk or GPU of the machine running them, so the
detected machine is a roomy one unless a test passes its own.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _roomy_machine(monkeypatch):
    from core import voice_requirements

    machine = voice_requirements.Machine(ram_gb=63.9, threads=16, gpu={"detected": False, "name": "",
                                         "acceleration": "cpu"}, free_disk_gb=500.0, available_ram_gb=48.0)
    monkeypatch.setattr(voice_requirements, "current_machine", lambda _home: machine)
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_nous_guest_default: run wizard.hermes_config.ensure_nous_guest_default for real")
    config.addinivalue_line(
        "markers", "real_stt_language_default: run wizard.hermes_config.ensure_stt_language_default for real")


@pytest.fixture(autouse=True)
def _no_real_hermes_profile_write(request, monkeypatch):
    """Starting the gateway or Wake writes ``nous.guest`` to the Hermes profile (Hermes 0.23),
    and the voice runtime writes ``stt.language``.

    Tests do not isolate HERMES_HOME by default, so a test that reaches those starts would edit
    the profile of the PC running them. Only tests marked ``real_nous_guest_default`` or
    ``real_stt_language_default`` (which set their own HERMES_HOME) run the real writer.
    """
    from wizard import hermes_config

    if request.node.get_closest_marker("real_nous_guest_default") is None:
        monkeypatch.setattr(hermes_config, "ensure_nous_guest_default", lambda: "absent")
    if request.node.get_closest_marker("real_stt_language_default") is None:
        monkeypatch.setattr(hermes_config, "ensure_stt_language_default", lambda language: "absent")
    yield
