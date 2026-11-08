"""A02: failures, process interruption and actual competing writers."""

from __future__ import annotations

import errno
import hashlib
import io
import json
import multiprocessing
import os
import struct
import time
from pathlib import Path

import pytest

from core import animation_store as store
from asset_upload_fixtures import BVH, animation


def glb(tag="new"):
    return animation(tag)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "animations"
    root.mkdir()
    (root / "wave.vrma").write_bytes(glb("old"))
    metadata = {
        "clips": {"wave": "wave.vrma"},
        "pose_map": {"greet": "wave"},
        "idle_pool": ["wave"],
        "return_idle_pool": ["wave"],
        "body_only_clips": ["wave"],
        "activity_clips": {"listening": {"clip": "wave", "loop": False}},
        "_credits": ["Author"],
        "_source_mapping": {"wave": "source"},
        "future_metadata": {"keep": [1, "á", False]},
    }
    (root / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return root


def snapshot(root):
    return (root / "manifest.json").read_bytes(), (root / "wave.vrma").read_bytes()


@pytest.mark.parametrize("extension", ["vrma", "glb", "gltf", "bvh"])
def test_formats_commit_versions_preserve_previous_metadata_and_retry(library, extension):
    payloads = {
        "vrma": glb(),
        "glb": glb(),
        "gltf": animation(embedded=True),
        "bvh": BVH,
    }
    payload = payloads[extension]
    before, old_clip = snapshot(library)
    result = store.AnimationStore(library).save_upload(
        io.BytesIO(payload), f"new.{extension}", "wave"
    )
    manifest = json.loads((library / "manifest.json").read_bytes())
    assert result.changed and result.revision == 1
    assert result.filename == f"wave.{hashlib.sha256(payload).hexdigest()}.{extension}"
    assert manifest["clips"]["wave"] == result.filename
    assert result.path.read_bytes() == payload
    assert (library / "wave.vrma").read_bytes() == old_clip
    assert {k: manifest[k] for k in json.loads(before) if k != "clips"} == {
        k: v for k, v in json.loads(before).items() if k != "clips"
    }
    committed = (library / "manifest.json").read_bytes()
    retry = store.AnimationStore(library).save_upload(
        io.BytesIO(payload), f"again.{extension}", "wave"
    )
    assert not retry.changed and retry.revision == 1
    assert (library / "manifest.json").read_bytes() == committed
    assert len(list(library.glob(f"wave.*.{extension}"))) == 1


@pytest.mark.parametrize(
    "filename,content,status",
    [
        ("new.glb", b"", 400),
        ("new.vrma", b"", 400),
        ("new.exe", glb(), 400),
        ("new.glb", glb()[:-1], 400),
        ("new.gltf", b"[]", 400),
        ("new.gltf", b'{"asset":null}', 400),
        ("new.bvh", b"not bvh", 400),
        ("new.fbx", b"not fbx", 415),
        ("vrma", glb(), 400),
    ],
)
def test_invalid_input_preserves_exact_previous_version(library, filename, content, status):
    before = snapshot(library)
    with pytest.raises(store.AnimationStoreError) as error:
        store.AnimationStore(library).save_upload(io.BytesIO(content), filename, "wave")
    assert error.value.status_code == status
    assert snapshot(library) == before
    assert sorted(p.name for p in library.iterdir()) == ["manifest.json", "wave.vrma"]


def test_reads_are_bounded_and_oversize_never_modifies_the_store(library):
    sizes = []

    class EndlessInput:
        def read(self, size):
            assert size > 0
            sizes.append(size)
            return b"a" * size

    before = snapshot(library)
    with pytest.raises(store.AnimationStoreError) as error:
        store.AnimationStore(library, max_bytes=100).save_upload(EndlessInput(), "x.glb", "wave")
    assert error.value.status_code == 413
    assert sum(sizes) == 101
    assert snapshot(library) == before


@pytest.mark.parametrize(
    "value",
    [
        "/outside.glb",
        "../outside.glb",
        "a/../../outside.glb",
        r"..\outside.glb",
        r"C:\outside.glb",
        r"\\server\outside.glb",
        "C:outside.glb",
        "a.glb:stream",
    ],
)
@pytest.mark.parametrize("field", ["filename", "name"])
def test_absolute_traversal_and_windows_paths_cannot_choose_a_destination(library, value, field):
    before = snapshot(library)
    with pytest.raises(store.AnimationStoreError):
        store.AnimationStore(library).save_upload(
            io.BytesIO(glb()),
            value if field == "filename" else "new.glb",
            value if field == "name" else "wave",
        )
    assert snapshot(library) == before


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"[]",
        b"{}",
        b'{"clips":[]}',
        b'{"clips":{"wave":{}}}',
        b'{"clips":{},"schema_version":2}',
        b'{"clips":{},"revision":true}',
        b'{"clips":{},"revision":-1}',
        b'{"clips":{},"value":NaN}',
        b'{"clips":{"wave":"../outside.vrma"}}',
        b'{"clips":{"wave":"missing.vrma"}}',
    ],
)
def test_invalid_manifest_is_preserved_not_replaced_with_an_empty_catalog(library, raw):
    (library / "manifest.json").write_bytes(raw)
    before = snapshot(library)
    with pytest.raises(store.AnimationStoreError) as error:
        store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert error.value.status_code == 409
    assert snapshot(library) == before
    assert not list(library.glob("wave.*.glb"))


@pytest.mark.parametrize(
    "operation,number",
    [
        ("write", 1),
        ("write", 2),
        ("fsync", 1),
        ("fsync", 2),
        ("replace", 1),
        ("replace", 2),
        ("directory", 1),
    ],
)
def test_io_failures_keep_previous_manifest_and_bytes(library, monkeypatch, operation, number):
    before = snapshot(library)
    attribute = {
        "write": "_write_temp",
        "fsync": "_write_temp",
        "replace": "_replace",
        "directory": "_sync_directory",
    }[operation]
    original = getattr(store, attribute)
    calls = 0

    def fail(*args):
        nonlocal calls
        calls += 1
        if calls == number:
            if operation == "fsync":
                with monkeypatch.context() as local:

                    def full(_fd):
                        raise OSError(errno.ENOSPC, "simulated disk full")

                    local.setattr(store.os, "fsync", full)
                    return original(*args)
            raise OSError(errno.ENOSPC, "simulated disk full")
        return original(*args)

    monkeypatch.setattr(store, attribute, fail)
    with pytest.raises(store.AnimationStoreError) as error:
        store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert error.value.status_code == 503
    assert snapshot(library) == before
    assert not list(library.glob(".animation-upload-*.tmp"))


def test_partial_write_is_removed_and_old_version_is_untouched(library, monkeypatch):
    before = snapshot(library)
    original = store.os.fdopen

    class PartialFile:
        def __init__(self, fd, mode):
            self.handle = original(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.handle.close()

        def write(self, content):
            self.handle.write(content[:5])
            raise OSError(errno.ENOSPC, "simulated partial write")

    monkeypatch.setattr(store.os, "fdopen", PartialFile)
    with pytest.raises(store.AnimationStoreError):
        store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert snapshot(library) == before
    assert not list(library.glob(".animation-upload-*.tmp"))


def test_error_after_commit_is_reported_as_saved_with_warning(library, monkeypatch):
    calls = 0

    def sync(_root):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync failed after manifest replacement")

    monkeypatch.setattr(store, "_sync_directory", sync)
    result = store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert result.warning
    assert json.loads((library / "manifest.json").read_bytes())["clips"]["wave"] == result.filename
    assert result.path.read_bytes() == glb()
    assert (library / "wave.vrma").is_file()


def make_directory_alias(target, link):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_directory_alias(link):
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


@pytest.mark.parametrize("alias", ["root", "manifest", "version", "lock"])
def test_symlinks_or_windows_junctions_cannot_redirect_storage(library, tmp_path, alias):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"UNTOUCHED")
    root = library
    if alias == "root":
        link = tmp_path / "linked"
        root = link
    elif alias == "manifest":
        link = library / "manifest.json"
        link.unlink()
    elif alias == "lock":
        link = library / ".animation-store.lock"
    else:
        link = library / f"wave.{hashlib.sha256(glb()).hexdigest()}.glb"
    make_directory_alias(outside, link)
    try:
        with pytest.raises(store.AnimationStoreError) as error:
            store.AnimationStore(root).save_upload(io.BytesIO(glb()), "new.glb", "wave")
        assert error.value.status_code == 409
        assert sentinel.read_bytes() == b"UNTOUCHED"
        assert list(outside.iterdir()) == [sentinel]
    finally:
        remove_directory_alias(link)


def _crash_writer(root, phase):
    root = Path(root)
    original_write, original_replace = store._write_temp, store._replace
    writes = 0

    def write(*args):
        nonlocal writes
        result = original_write(*args)
        writes += 1
        if (phase == "asset_staged" and writes == 1) or (
            phase == "manifest_staged" and writes == 2
        ):
            os._exit(17)
        return result

    def replace(source, destination):
        original_replace(source, destination)
        is_manifest = destination.name == "manifest.json"
        if (phase == "asset_published" and not is_manifest) or (
            phase == "committed" and is_manifest
        ):
            os._exit(17)

    store._write_temp, store._replace = write, replace
    store.AnimationStore(root).save_upload(io.BytesIO(glb()), "new.glb", "wave")


@pytest.mark.parametrize(
    "phase", ["asset_staged", "asset_published", "manifest_staged", "committed"]
)
def test_process_interruption_at_each_publication_phase_is_recoverable(library, phase):
    before = snapshot(library)
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_writer, args=(str(library), phase)
    )
    process.start()
    process.join(20)
    try:
        assert not process.is_alive()
        assert process.exitcode == 17
        if phase != "committed":
            assert snapshot(library) == before
        else:
            manifest = json.loads((library / "manifest.json").read_bytes())
            assert (library / manifest["clips"]["wave"]).read_bytes() == glb()
        retry = store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
        assert retry.changed is (phase != "committed")
        assert retry.revision == 1 and retry.path.read_bytes() == glb()
        assert (library / "wave.vrma").read_bytes() == before[1]
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        process.close()


def _parallel_writer(root, slot, identity, barrier, results):
    barrier.wait(timeout=15)
    for index in range(4):
        result = store.AnimationStore(Path(root)).save_upload(
            io.BytesIO(glb(f"{identity}-{index}")),
            "new.glb",
            slot,
        )
        results.put((result.revision, result.filename))


@pytest.mark.parametrize("same_slot", [False, True])
def test_two_processes_merge_slots_and_readers_only_see_complete_versions(library, same_slot):
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(3), ctx.Queue()
    slots = ["wave", "wave" if same_slot else "think"]
    processes = [
        ctx.Process(target=_parallel_writer, args=(str(library), slot, str(i), barrier, results))
        for i, slot in enumerate(slots)
    ]
    for process in processes:
        process.start()
    try:
        barrier.wait(timeout=15)
        reads = 0
        deadline = time.monotonic() + 20
        while any(process.is_alive() for process in processes) and time.monotonic() < deadline:
            manifest = json.loads(store.read_animation_manifest(library / "manifest.json"))
            for filename in manifest["clips"].values():
                content = (library / filename).read_bytes()
                assert len(content) == struct.unpack_from("<I", content, 8)[0]
            reads += 1
            time.sleep(0.001)
        for process in processes:
            process.join(2)
            assert process.exitcode == 0
        commits = [results.get(timeout=3) for _ in range(8)]
        assert sorted(revision for revision, _ in commits) == list(range(1, 9))
        assert reads > 0
        for _, filename in commits:
            assert (library / filename).is_file()
        final = json.loads(store.read_animation_manifest(library / "manifest.json"))
        assert final["revision"] == 8 and set(final["clips"]) == set(slots)
        assert final["future_metadata"] == {"keep": [1, "á", False]}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join()
            process.close()
        results.close()
        results.join_thread()


def _hold_lock(root, ready, release):
    with store._store_lock(Path(root), 2):
        ready.set()
        release.wait(15)


def test_another_process_lock_has_a_bounded_timeout(library):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_lock, args=(str(library), ready, release))
    process.start()
    before = snapshot(library)
    try:
        assert ready.wait(10)
        with pytest.raises(store.AnimationStoreError) as error:
            store.AnimationStore(library, lock_timeout=0.05).save_upload(
                io.BytesIO(glb()), "new.glb", "wave"
            )
        assert error.value.status_code == 409
        assert snapshot(library) == before
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()
        process.close()


def test_deferred_cleanup_keeps_published_versions_and_factory_files(library):
    abandoned = library / ".animation-upload-abandoned.tmp"
    fresh = library / ".animation-upload-active.tmp"
    abandoned.write_bytes(b"partial")
    fresh.write_bytes(b"partial")
    orphan = library / f"wave.{hashlib.sha256(glb('orphan')).hexdigest()}.glb"
    orphan.write_bytes(glb("orphan"))
    for path in (abandoned, library / "wave.vrma", orphan):
        os.utime(path, (1, 1))
    result = store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert result.changed
    assert not abandoned.exists()
    assert fresh.is_file() and orphan.read_bytes() == glb("orphan")
    assert (library / "wave.vrma").read_bytes() == glb("old")


def test_existing_digest_file_with_wrong_bytes_is_never_overwritten(library):
    target = library / f"wave.{hashlib.sha256(glb()).hexdigest()}.glb"
    target.write_bytes(b"unexpected-existing-bytes")
    before = snapshot(library)
    with pytest.raises(store.AnimationStoreError) as error:
        store.AnimationStore(library).save_upload(io.BytesIO(glb()), "new.glb", "wave")
    assert error.value.status_code == 409
    assert target.read_bytes() == b"unexpected-existing-bytes"
    assert snapshot(library) == before
