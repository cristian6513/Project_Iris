"""A02 follow-up: bounded Windows read retries and real reader integration."""

from __future__ import annotations

import asyncio
import errno
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from test_animation_store import glb

from core import animation_library as library_module
from core import animation_store as store
from core import animations
from core import config as config_module
from core import websocket_server as core
from core.config import IRISConfig
from wizard import app as wizard


@pytest.fixture
def manifest(tmp_path):
    root = tmp_path / "face" / "animations"
    root.mkdir(parents=True)
    (root / "authored.vrma").write_bytes(glb("old"))
    path = root / "manifest.json"
    path.write_bytes(b'{"clips":{"wave":"authored.vrma"},"revision":4}')
    return path


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds

    # Replace the store's binding, never the global time module used by pytest/ASGI.
    monkeypatch.setattr(
        store,
        "time",
        SimpleNamespace(
            monotonic=lambda: state.now,
            sleep=sleep,
            time=time.time,
        ),
    )
    monkeypatch.setattr(store, "_WINDOWS", True)
    return state


def denied(path, winerror=None):
    error = PermissionError(errno.EACCES, "manifest busy", str(path))
    if winerror is not None:
        error.winerror = winerror
    return error


def fail_opens(monkeypatch, path, error, *, failures=2):
    original = Path.open
    state = SimpleNamespace(calls=0, failures=failures)

    def opened(candidate, mode="r", *args, **kwargs):
        if candidate == path and mode == "rb":
            state.calls += 1
            if state.calls <= state.failures:
                raise error
        return original(candidate, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    return state


@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_transient_windows_open_failure_reopens_and_returns_complete_bytes(
    manifest,
    monkeypatch,
    clock,
    winerror,
):
    expected = manifest.read_bytes()
    opened = fail_opens(monkeypatch, manifest, denied(manifest, winerror))
    assert store.read_animation_manifest(manifest) == expected
    assert opened.calls == 3 and clock.sleeps == [0.01, 0.01]


def test_permanent_denial_has_deadline_and_keeps_original_error(manifest, monkeypatch, clock):
    error = denied(manifest)
    opened = fail_opens(monkeypatch, manifest, error, failures=1000)
    with pytest.raises(PermissionError) as caught:
        store.read_animation_manifest(manifest, timeout=0.025)
    assert caught.value is error
    assert opened.calls == 4
    assert sum(clock.sleeps) == pytest.approx(0.025)


@pytest.mark.parametrize("failure", ["posix", "other_winerror", "io_error", "missing", "zero_wait"])
def test_non_transient_or_non_waiting_reads_are_not_retried(
    manifest,
    monkeypatch,
    clock,
    failure,
):
    error = denied(manifest, 87 if failure == "other_winerror" else None)
    if failure == "posix":
        monkeypatch.setattr(store, "_WINDOWS", False)
    elif failure == "io_error":
        error = OSError(errno.EIO, "read failed")
    elif failure == "missing":
        error = FileNotFoundError(errno.ENOENT, "missing")
    opened = fail_opens(monkeypatch, manifest, error)
    with pytest.raises(type(error)) as caught:
        store.read_animation_manifest(manifest, timeout=0 if failure == "zero_wait" else 1)
    assert caught.value is error
    assert opened.calls == 1 and not clock.sleeps


def test_size_limit_does_not_retry_or_return_truncated_json(manifest, monkeypatch, clock):
    monkeypatch.setattr(store, "MAX_MANIFEST_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        store.read_animation_manifest(manifest)
    assert not clock.sleeps


def test_failed_read_closes_handle_and_retries_the_entire_snapshot(manifest, monkeypatch, clock):
    expected = manifest.read_bytes()
    original = Path.open
    handles = []

    class InterruptedRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.handle.close()

        def read(self, size):
            assert self.handle.read(4) == expected[:4]
            raise denied(manifest, 32)

    def opened(path, mode="r", *args, **kwargs):
        handle = original(path, mode, *args, **kwargs)
        if path == manifest and mode == "rb":
            handles.append(handle)
            if len(handles) == 1:
                return InterruptedRead(handle)
        return handle

    monkeypatch.setattr(Path, "open", opened)
    assert store.read_animation_manifest(manifest) == expected
    assert len(handles) == 2 and all(handle.closed for handle in handles)


@pytest.fixture
def catalog(manifest, monkeypatch):
    monkeypatch.setattr(animations, "_ANIM_DIR", manifest.parent)
    monkeypatch.setattr(animations, "_MANIFEST_PATH", manifest)
    monkeypatch.setattr(animations, "_cache_token", None)
    monkeypatch.setattr(animations, "_cache_clips", [])
    return manifest


@pytest.mark.parametrize("invalidate", [False, True])
def test_catalog_retains_confirmed_names_on_outage_then_refreshes(
    catalog,
    monkeypatch,
    clock,
    invalidate,
):
    expected = animations.get_available_animations()
    assert [clip["name"] for clip in expected] == ["wave"]
    if invalidate:
        animations._invalidate_animation_cache()
    old_token = animations._cache_token
    opened = fail_opens(monkeypatch, catalog, denied(catalog), failures=10000)
    assert animations.get_available_animations() == expected
    assert animations._cache_token == old_token
    assert clock.now == pytest.approx(1.0)
    opened.failures = 0
    catalog.write_bytes(b'{"clips":{"celebrate":"authored.vrma"},"revision":5}')
    assert [clip["name"] for clip in animations.get_available_animations()] == ["celebrate"]


def test_initial_outage_does_not_advertise_legacy_aliases(catalog, monkeypatch, clock):
    fail_opens(monkeypatch, catalog, denied(catalog), failures=10000)
    assert animations.get_available_animations() == []
    assert animations._cache_token is None


@pytest.mark.asyncio
async def test_sync_catalog_on_event_loop_never_sleeps_on_a_busy_manifest(catalog, monkeypatch):
    expected = await asyncio.to_thread(animations.get_available_animations)
    monkeypatch.setattr(store, "_WINDOWS", True)
    sleep = Mock(side_effect=AssertionError("must not block the event loop"))
    monkeypatch.setattr(store, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
    opened = fail_opens(monkeypatch, catalog, denied(catalog), failures=1)
    assert animations.get_available_animations() == expected
    assert opened.calls == 1
    assert animations.get_available_animations() == expected
    assert opened.calls == 2
    sleep.assert_not_called()


def test_real_missing_manifest_preserves_legacy_fallback(catalog):
    catalog.unlink()
    assert [clip["name"] for clip in animations.get_available_animations()] == ["authored"]


@pytest.fixture
def surfaces(catalog, monkeypatch, tmp_path):
    home = tmp_path / "profile"
    monkeypatch.setattr(wizard, "_animations_dir", lambda: home / "animations")
    monkeypatch.setattr(
        wizard,
        "_animation_library",
        lambda: library_module.AnimationLibrary(catalog.parent, home / "animations"),
    )
    monkeypatch.setattr(config_module, "get_iris_home", lambda: home)
    cfg = IRISConfig.model_construct(lan_access=True, lan_pairing_token="TEST-PAIR")
    monkeypatch.setattr(IRISConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(core.app.router, "routes", list(core.app.router.routes))
    monkeypatch.setattr(core.app.state, "face_dir", None, raising=False)
    core.mount_static_files(catalog.parent.parent)
    return SimpleNamespace(manifest=catalog, core=core.app, wizard=wizard.app)


@contextmanager
def client_for(surfaces, surface):
    client = TestClient(
        getattr(surfaces, surface),
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    )
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize(
    "surface,path,method",
    [
        ("wizard", "/api/animation-slots", "GET"),
        ("core", "/api/animation-catalog", "GET"),
        ("core", "/face/animations/manifest.json", "GET"),
        ("core", "/face/animations/manifest.json", "HEAD"),
    ],
)
def test_http_readers_recover_transient_denial(surfaces, monkeypatch, clock, surface, path, method):
    expected = b'{"clips":{"wave":"authored.vrma"},"revision":0,"schema_version":1}'
    opened = fail_opens(monkeypatch, surfaces.manifest, denied(surfaces.manifest))
    with client_for(surfaces, surface) as client:
        response = client.request(method, path)
    assert response.status_code == 200, response.text
    assert opened.calls >= 3
    if path.endswith("manifest.json"):
        assert response.content == (expected if method == "GET" else b"")
        assert response.headers["content-length"] == str(len(expected))
        assert "no-store" in response.headers["cache-control"]
    elif surface == "wizard":
        assert response.json()["revision"] == 0  # No personal commits yet.
        assert next(x for x in response.json()["slots"] if x["slot"] == "wave")["present"]
    else:
        assert [clip["name"] for clip in response.json()["clips"]] == ["wave"]


@pytest.mark.parametrize(
    "surface,path",
    [
        ("wizard", "/api/animation-slots"),
        ("core", "/face/animations/manifest.json"),
    ],
)
def test_persistent_http_denial_returns_unavailable(surfaces, monkeypatch, clock, surface, path):
    fail_opens(monkeypatch, surfaces.manifest, denied(surfaces.manifest), failures=10000)
    with client_for(surfaces, surface) as client:
        response = client.get(path)
    assert response.status_code == 503
    assert "error" in response.json() or "detail" in response.json()
    assert clock.now == pytest.approx(1.0)


def test_manifest_headers_and_body_use_one_snapshot_even_if_replaced_before_response(
    surfaces,
    monkeypatch,
):
    old = {"clips": {"wave": "authored.vrma"}, "revision": 0, "schema_version": 1}
    new = b'{"clips":{"celebrate":"authored.vrma"},"revision":12345,"new":"metadata"}'
    original = store.read_animation_manifest

    def snapshot(path, **kwargs):
        raw = original(path, **kwargs)
        replacement = path.with_suffix(".next")
        replacement.write_bytes(new)
        replacement.replace(path)
        return raw

    monkeypatch.setattr(library_module, "read_animation_manifest", snapshot)
    with client_for(surfaces, "core") as client:
        first = client.get("/face/animations/manifest.json")
        second = client.get("/face/animations/manifest.json")
    assert first.json() == old and first.headers["content-length"] == str(len(first.content))
    assert second.json() == {
        "clips": {"celebrate": "authored.vrma"},
        "revision": 0,
        "new": "metadata",
        "schema_version": 1,
    }
    assert second.headers["content-length"] == str(len(second.content))


def test_manifest_still_requires_lan_pairing_and_immutable_clip_serving_is_unchanged(surfaces):
    client = TestClient(
        surfaces.core,
        base_url="http://192.168.1.10:8765",
        client=("192.168.1.20", 50000),
    )
    try:
        assert client.get("/face/animations/manifest.json").status_code == 403
        assert client.head("/face/animations/manifest.json").status_code == 403
        response = client.get(
            "/face/animations/manifest.json",
            headers={"x-iris-lan-token": "TEST-PAIR"},
        )
        assert response.json()["clips"] == json.loads(surfaces.manifest.read_bytes())["clips"]
    finally:
        client.close()
    with client_for(surfaces, "core") as client:
        assert client.get("/face/animations/authored.vrma").content == glb("old")
        assert client.post("/face/animations/manifest.json").status_code == 405


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "surface,path",
    [
        ("wizard", "/api/animation-slots"),
        ("core", "/api/animation-catalog"),
        ("core", "/face/animations/manifest.json"),
    ],
)
async def test_http_manifest_wait_runs_outside_event_loop(surfaces, monkeypatch, surface, path):
    entered, release = threading.Event(), threading.Event()
    original = store.read_animation_manifest
    loop_thread = threading.get_ident()

    def slow(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(4)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "read_animation_manifest", slow)
    monkeypatch.setattr(library_module, "read_animation_manifest", slow)
    monkeypatch.setattr(animations, "read_animation_manifest", slow)
    transport = httpx.ASGITransport(app=getattr(surfaces, surface), client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        task = asyncio.create_task(client.get(path))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        assert (await task).status_code == 200


def test_failed_writer_read_does_not_replace_existing_manifest(manifest, monkeypatch, clock):
    import io

    previous = manifest.read_bytes()
    opened = fail_opens(monkeypatch, manifest, denied(manifest), failures=10000)
    with pytest.raises(store.AnimationStoreError) as caught:
        store.AnimationStore(manifest.parent).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert caught.value.status_code == 503
    assert clock.now == pytest.approx(1.0)
    opened.failures = 0
    assert manifest.read_bytes() == previous
    assert sorted(p.name for p in manifest.parent.iterdir()) == [
        ".animation-store.lock",
        "authored.vrma",
        "manifest.json",
    ]


def test_permission_failure_during_path_validation_is_also_retried(manifest, monkeypatch, clock):
    original = store._plain_file
    attempts = 0

    def checked(*args):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise denied(manifest)
        return original(*args)

    monkeypatch.setattr(store, "_plain_file", checked)
    assert json.loads(store.read_animation_manifest(manifest))["revision"] == 4
    assert attempts == 3 and clock.sleeps == [0.01, 0.01]


def test_missing_factory_manifest_is_an_empty_effective_library(surfaces):
    surfaces.manifest.unlink()
    with client_for(surfaces, "core") as client:
        response = client.get("/face/animations/manifest.json")
        assert response.status_code == 200 and response.json()["clips"] == {}
    with client_for(surfaces, "wizard") as client:
        response = client.get("/api/animation-slots")
    assert response.status_code == 200
    assert response.json()["revision"] == 0
    assert not any(slot["present"] for slot in response.json()["slots"])


def test_manifest_snapshot_cannot_follow_a_real_outside_directory_alias(surfaces, tmp_path):
    import os

    root = surfaces.manifest.parent
    saved = root.with_name("original-animations")
    root.rename(saved)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = b'{"private": "MUST NOT BE SERVED"}'
    (outside / "manifest.json").write_bytes(secret)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(root))
    else:
        root.symlink_to(outside, target_is_directory=True)
    try:
        with client_for(surfaces, "core") as client:
            response = client.get("/face/animations/manifest.json")
        assert response.status_code == 404
        assert b"MUST NOT BE SERVED" not in response.content
        assert (outside / "manifest.json").read_bytes() == secret
    finally:
        if os.name == "nt":
            root.rmdir()  # Remove only the junction, never its target.
        else:
            root.unlink()
