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
