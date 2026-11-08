"""Core must claim/migrate its profile before constructing mutable services."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_core_profile_claim_precedes_services_and_releases_on_failure(
    monkeypatch, tmp_path, fails
):
    from core import agent, profile_migration, profile_paths

    events = []
    paths = SimpleNamespace(animations=tmp_path / "profile" / "animations")
    factory = tmp_path / "factory" / "animations"
    monkeypatch.setattr(profile_paths, "get_profile_paths", lambda: paths)
    monkeypatch.setattr(agent, "get_runtime", lambda: SimpleNamespace(assets_root=factory.parent))

    def migrate(source, target):
        assert (source, target) == (factory, paths.animations)
        events.append("migrate")

    @asynccontextmanager
    async def claim(role, *, paths, prepare):
        assert role == "core"
        events.append("claim")
        prepare()
        try:
            yield
        finally:
            events.append("release")

    async def services():
        events.append("services")
        if fails:
            raise RuntimeError("service startup failed")

    monkeypatch.setattr(profile_paths, "async_profile_writer", claim)
    monkeypatch.setattr(profile_migration, "migrate_animation_profile", migrate)
    monkeypatch.setattr(agent, "_run_agent_services", services)
    if fails:
        with pytest.raises(RuntimeError, match="startup failed"):
            await agent._run_agent()
    else:
        await agent._run_agent()
    assert events == ["claim", "migrate", "services", "release"]


@pytest.mark.asyncio
async def test_incompatible_profile_prevents_core_service_creation(monkeypatch, tmp_path):
    from core import agent, profile_paths

    @asynccontextmanager
    async def reject(*args, **kwargs):
        raise RuntimeError("incompatible writer")
        yield  # pragma: no cover

    async def unexpected():
        pytest.fail("No Core services may start on an incompatible profile")

    monkeypatch.setattr(profile_paths, "async_profile_writer", reject)
    monkeypatch.setattr(
        profile_paths,
        "get_profile_paths",
        lambda: SimpleNamespace(animations=tmp_path / "profile" / "animations"),
    )
    monkeypatch.setattr(agent, "_run_agent_services", unexpected)
    with pytest.raises(RuntimeError, match="incompatible writer"):
        await agent._run_agent()
