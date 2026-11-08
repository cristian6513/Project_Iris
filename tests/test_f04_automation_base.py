"""F04: automation contracts, the durable store and presence signals.

Synthetic rules, fake clocks and throwaway profiles only: nothing here runs a
job, talks to Hermes or opens a sensor.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.automation_contracts import (
    AutomationDelivery,
    AutomationOccurrence,
    AutomationRule,
    can_transition,
    new_id,
)
from core.automation_store import (
    AutomationStore,
    ClaimLost,
    InvalidTransition,
    RevisionConflict,
    StaleRevision,
    StorePathError,
    StoreVersionError,
    profile_store_path,
)
from core.presence_state import PresenceTracker


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


def rule(**overrides) -> AutomationRule:
    data = {
        "id": new_id(), "title": "Investigar al irme", "enabled": True,
        "trigger": {"source": "iris_idle", "threshold_s": 300},
        "execution": {"prompt": "Resume las noticias que te pedí (sintético)."},
        "created_at": 1_800_000_000.0, "updated_at": 1_800_000_000.0,
    }
    data.update(overrides)
    return AutomationRule.model_validate(data)


# ── Contracts ─────────────────────────────────────────────────────────────


def test_a_rule_is_strict_and_starts_paused():
    paused = AutomationRule.model_validate({
        "id": new_id(), "title": "x", "trigger": {"source": "observed_absence", "threshold_s": 300},
        "execution": {"prompt": "p"}, "created_at": 1.0, "updated_at": 1.0})
    assert paused.enabled is False and paused.revision == 1
    assert paused.model_dump(by_alias=True)["schema"] == "iris.automation-rule/v1"
    for bad in (
        {"extra": 1},
        {"trigger": {"source": "cron", "threshold_s": 300}},  # not a presence source
        {"trigger": {"source": "iris_idle", "threshold_s": 30}},  # below a minute
        {"trigger": {"source": "iris_idle", "threshold_s": "300"}},  # strict types
        {"execution": {"prompt": "p", "provider": "x"}},  # provider without model
        {"execution": {"prompt": "p", "tool_allowlist": ["web", "web"]}},
        {"execution": {"prompt": "p", "concurrency": 2}},
        {"updated_at": 0.5},  # before created_at
        {"id": "NO-ES-UN-ID"},
    ):
        with pytest.raises(ValidationError):
            rule(**bad)


def test_occurrence_and_delivery_facts_are_consistent():
    base = {"id": new_id(), "rule_id": new_id(), "rule_revision": 1, "occurrence_key": "iris_idle:abcd:1",
            "correlation_id": new_id(), "created_at": 1.0, "updated_at": 1.0}
    AutomationOccurrence.model_validate(base)
    with pytest.raises(ValidationError):
        AutomationOccurrence.model_validate({**base, "state": "claimed"})  # a claim needs its lease
    with pytest.raises(ValidationError):
        AutomationOccurrence.model_validate({**base, "state": "completed"})  # and a result
    with pytest.raises(ValidationError):
        AutomationOccurrence.model_validate({**base, "occurrence_key": "con espacios"})
    with pytest.raises(ValidationError):
        AutomationDelivery.model_validate({"result_id": new_id(), "status": "read", "expires_at": 2.0})
    assert not can_transition("completed", "running") and not can_transition("ambiguous", "candidate")
    assert can_transition("submitted", "ambiguous") and can_transition("ambiguous", "running")


# ── Store ─────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    clock = Clock()
    return AutomationStore(tmp_path / "automations.sqlite3", clock=clock), clock


def test_saving_and_reopening_keeps_everything_and_runs_nothing(store, tmp_path):
    db, clock = store
    saved = db.create_rule(rule())
    reopened = AutomationStore(tmp_path / "automations.sqlite3", clock=clock)
    assert reopened.get_rule(saved.id) == saved
    assert reopened.occurrences() == []  # saving a rule created no occurrence and ran nothing
    assert [h["kind"] for h in reopened.history()] == ["rule_created"]


def test_one_episode_is_one_occurrence_whoever_records_it(store):
    db, clock = store
    saved = db.create_rule(rule())
    first, created = db.record_candidate(saved.id, 1, "iris_idle:run:1")
    again, created_again = db.record_candidate(saved.id, 1, "iris_idle:run:1")
    assert created and not created_again and again.id == first.id
    results = []

    def record():
        results.append(db.record_candidate(saved.id, 1, "iris_idle:run:2"))

    threads = [threading.Thread(target=record) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for _, created in results if created) == 1
    assert len({occurrence.id for occurrence, _ in results}) == 1
    assert len(db.occurrences()) == 2


def test_two_claimants_one_wins_and_an_expired_lease_can_be_claimed_again(store, tmp_path):
    db, clock = store
    saved = db.create_rule(rule())
    occurrence, _ = db.record_candidate(saved.id, 1, "k1")
    other = AutomationStore(tmp_path / "automations.sqlite3", clock=clock)  # another process' handle
    wins = []
    barrier = threading.Barrier(2)

    def contend(handle, owner):
        barrier.wait()
        wins.append(handle.claim(occurrence.id, owner, lease_s=30))

    threads = [threading.Thread(target=contend, args=(db, "core-a")), threading.Thread(target=contend, args=(other, "core-b"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    claimed = [w for w in wins if w is not None]
    assert len(claimed) == 1
    holder = claimed[0]
    with pytest.raises(ClaimLost):
        db.advance(occurrence.id, "submitted", token=new_id())  # not the holder
    clock.tick(31)
    with pytest.raises(ClaimLost):
        db.advance(occurrence.id, "submitted", token=holder.claim.token)  # its lease expired
    retaken = other.claim(occurrence.id, "core-b", lease_s=30)
    assert retaken is not None and retaken.claim.token != holder.claim.token
    submitted = other.advance(occurrence.id, "submitted", token=retaken.claim.token, run_id="run_1")
    assert submitted.state == "submitted" and submitted.claim is None
    assert db.claim(occurrence.id, "core-a") is None  # submitted work is never claimed again


def test_an_edit_needs_the_revision_it_read_and_cancels_unsent_occurrences(store):
    db, clock = store
    saved = db.create_rule(rule())
    old, _ = db.record_candidate(saved.id, 1, "k-old")
    sent, _ = db.record_candidate(saved.id, 1, "k-sent")
    token = db.claim(sent.id, "core").claim.token
    db.advance(sent.id, "submitted", token=token)
    clock.tick(5)
    edited = db.update_rule(saved.id, expected_revision=1, title="Otro título")
    assert edited.revision == 2
    with pytest.raises(RevisionConflict):
        db.update_rule(saved.id, expected_revision=1, enabled=False)
    states = {o.occurrence_key: o.state for o in db.occurrences()}
    assert states == {"k-old": "cancelled", "k-sent": "submitted"}  # sent work keeps going
    with pytest.raises(StaleRevision):
        db.record_candidate(saved.id, 1, "k-new")  # an episode seen by revision 1 does not fire again
    db.update_rule(saved.id, expected_revision=2, enabled=False)
    with pytest.raises(StaleRevision):
        db.record_candidate(saved.id, 3, "k-new")  # paused


def test_finishing_writes_result_delivery_and_state_together(store):
    db, clock = store
    saved = db.create_rule(rule())
    occurrence, _ = db.record_candidate(saved.id, 1, "k")
    token = db.claim(occurrence.id, "core").claim.token
    db.advance(occurrence.id, "submitted", token=token, run_id="run_9")
    with pytest.raises(InvalidTransition):
        db.advance(occurrence.id, "completed")
    done, result, delivery = db.finish(occurrence.id, status="completed", text="Resumen sintético.")
    assert done.state == "completed" and done.result_id == result.id and delivery.status == "pending"
    assert db.result(result.id).text == "Resumen sintético."
    db.mark_delivery(result.id, "presented")
    read = db.mark_delivery(result.id, "read")
    assert read.presented_at is not None and read.read_at is not None
    with pytest.raises(InvalidTransition):
        db.mark_delivery(result.id, "presented")  # never back
    with pytest.raises(InvalidTransition):
        db.finish(occurrence.id, status="completed")


def test_a_failed_write_changes_nothing(store, monkeypatch):
    db, clock = store
    saved = db.create_rule(rule())
    occurrence, _ = db.record_candidate(saved.id, 1, "k")
    before = (db.get_rule(saved.id), db.occurrences(), db.history())

    def broken(*args, **kwargs):
        raise OSError("disco lleno (simulado)")

    monkeypatch.setattr(AutomationStore, "_history", staticmethod(broken))
    with pytest.raises(OSError):
        db.update_rule(saved.id, expected_revision=1, title="Nuevo")
    with pytest.raises(OSError):
        db.claim(occurrence.id, "core")
    monkeypatch.undo()
    assert (db.get_rule(saved.id), db.occurrences(), db.history()) == before
    assert db.check() == []


def test_a_store_from_a_newer_iris_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "automations.sqlite3"
    AutomationStore(path).create_rule(rule())
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version = 7")
        conn.execute("CREATE TABLE future_rules (x TEXT)")
        conn.commit()
        # A future Iris may use another journal mode: opening must not switch it to WAL.
        assert conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    before = path.read_bytes()
    with pytest.raises(StoreVersionError):
        AutomationStore(path)
    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()


def test_each_profile_has_its_own_store_and_links_are_refused(tmp_path, monkeypatch):
    from core.profile_paths import ProfilePathError
    from tests.test_runtime_descriptor import make_directory_alias

    a, b = tmp_path / "perfil-a", tmp_path / "perfil-b"
    a.mkdir()
    b.mkdir()
    store_a, store_b = AutomationStore.for_profile(a), AutomationStore.for_profile(b)
    store_a.create_rule(rule())
    assert store_b.list_rules() == [] and len(store_a.list_rules()) == 1
    assert profile_store_path(a) == a / "automations" / "automations.sqlite3"
    c = tmp_path / "perfil-c"
    c.mkdir()
    outside = tmp_path / "fuera"
    outside.mkdir()
    make_directory_alias(c / "automations", outside)
    try:
        with pytest.raises(ProfilePathError):
            profile_store_path(c)
        assert list(outside.iterdir()) == []
    finally:
        (c / "automations").rmdir()


# ── Q02: the profile boundary holds on every real open ──────────────────


def outside_rules(db):
    """Rules of a store outside the profile, read with plain sqlite (no boundary checks)."""
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM rules")]


def make_outside(tmp_path):
    outside = tmp_path / "fuera"
    AutomationStore(outside / "automations.sqlite3").create_rule(rule())
    return outside, outside / "automations.sqlite3"


def test_a_folder_replaced_by_a_link_after_opening_is_never_written(tmp_path):
    from tests.test_runtime_descriptor import make_directory_alias

    home = tmp_path / "perfil"
    home.mkdir()
    outside, outside_db = make_outside(tmp_path)
    before = outside_rules(outside_db)
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    folder = home / "automations"
    folder.rename(home / "automations-movida")
    make_directory_alias(folder, outside)  # installed before the next write, no race needed
    try:
        with pytest.raises(StorePathError):
            store.create_rule(rule())
        with pytest.raises(StorePathError):
            store.list_rules()
        assert outside_rules(outside_db) == before  # the target is neither written nor touched
    finally:
        folder.rmdir()  # removes the link, keeps its target
    assert outside_rules(outside_db) == before


def test_the_runtime_refuses_a_folder_replaced_before_its_deferred_open(tmp_path):
    from core.automation_api import AutomationRuntime
    from tests.test_runtime_descriptor import make_directory_alias

    home = tmp_path / "perfil"
    home.mkdir()
    outside, outside_db = make_outside(tmp_path)
    before = outside_rules(outside_db)
    runtime = AutomationRuntime(home=home, transport=None, permission=lambda: "ask",
                                profile_problem=lambda profile: "", local_surface_open=lambda: True)
    make_directory_alias(home / "automations", outside)  # after the runtime, before its first open
    try:
        assert runtime.store(create=True) is None and "enlace" in runtime.problem
        assert outside_rules(outside_db) == before
    finally:
        (home / "automations").rmdir()
    assert runtime.store(create=True) is not None and runtime.problem == ""  # a plain folder again


def test_a_hardlinked_database_is_refused_before_and_after_opening(tmp_path):
    home = tmp_path / "perfil"
    (home / "automations").mkdir(parents=True)
    _, outside_db = make_outside(tmp_path)
    before = outside_rules(outside_db)
    db = home / "automations" / "automations.sqlite3"
    os.link(outside_db, db)
    with pytest.raises(StorePathError):
        AutomationStore.for_profile(home)
    db.unlink()
    store = AutomationStore.for_profile(home)  # control: a plain file opens
    store.create_rule(rule())
    db.unlink()
    os.link(outside_db, db)  # replaced after opening
    with pytest.raises(StorePathError):
        store.create_rule(rule())
    assert outside_rules(outside_db) == before


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_a_linked_sidecar_is_refused_and_its_target_left_alone(tmp_path, sidecar):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    target = tmp_path / "ajeno.bin"
    target.write_bytes(b"no es del perfil")
    link = home / "automations" / ("automations.sqlite3" + sidecar)
    link.unlink(missing_ok=True)
    os.link(target, link)
    with pytest.raises(StorePathError):
        store.list_rules()
    assert target.read_bytes() == b"no es del perfil"


@pytest.mark.skipif(os.name != "nt", reason="the path is pinned with Windows handles")
def test_a_folder_swapped_and_put_back_while_sqlite_opens_is_refused_by_the_pins(tmp_path, monkeypatch):
    # T01, Codex's case: during SQLite's own open the folder becomes a junction to another store
    # and is put back before the next check. The pins make that swap impossible.
    import core.automation_store as automation_store
    from tests.test_runtime_descriptor import make_directory_alias

    home = tmp_path / "perfil"
    home.mkdir()
    outside, outside_db = make_outside(tmp_path)
    before = outside_rules(outside_db)
    store = AutomationStore.for_profile(home)
    folder, aside = home / "automations", home / "automations-apartada"
    real_connect = sqlite3.connect
    attempts = []

    def swap_during_connect(*args, **kwargs):
        try:
            folder.rename(aside)
        except PermissionError:
            attempts.append("blocked")
            return real_connect(*args, **kwargs)
        attempts.append("swapped")  # what happened before the pins: write outside, then put it back
        make_directory_alias(folder, outside)
        try:
            return real_connect(*args, **kwargs)
        finally:
            folder.rmdir()
            aside.rename(folder)

    monkeypatch.setattr(automation_store.sqlite3, "connect", swap_during_connect)
    store.create_rule(rule())
    monkeypatch.setattr(automation_store.sqlite3, "connect", real_connect)
    assert attempts and set(attempts) == {"blocked"}
    assert outside_rules(outside_db) == before
    assert len(store.list_rules()) == 1


@pytest.mark.skipif(os.name != "nt", reason="the path is pinned with Windows handles")
def test_the_database_cannot_be_replaced_while_a_connection_uses_it(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    with closing(store._connect()):
        with pytest.raises(PermissionError):
            store.path.rename(store.path.with_name("apartada.sqlite3"))
        with pytest.raises(PermissionError):
            store.path.parent.rename(home / "otra")
        with pytest.raises(PermissionError):
            home.rename(tmp_path / "perfil movido")
    store.path.parent.rename(home / "otra")  # released with the connection: nothing stays pinned
    (home / "otra").rename(store.path.parent)


@pytest.mark.skipif(os.name != "nt", reason="the path is pinned with Windows handles")
def test_the_pins_alone_keep_the_folder_and_the_database_in_place(tmp_path):
    # Without any SQLite connection open: only the pins' read access and missing
    # FILE_SHARE_DELETE stop a rename (an attributes-only handle would not).
    from core.automation_store import _Pins

    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    folder = store.path.parent
    pins = _Pins()
    pins.pin(folder, directory=True)
    with pytest.raises(PermissionError):
        folder.rename(home / "otra")
    pins.release()
    pins.pin(store.path, directory=False)
    with pytest.raises(PermissionError):
        store.path.rename(store.path.with_name("apartada.sqlite3"))
    pins.release()
    folder.rename(home / "otra")  # released: nothing stays pinned
    (home / "otra").rename(folder)


@pytest.mark.skipif(os.name != "nt", reason="the path is pinned with Windows handles")
def test_the_first_open_cannot_be_redirected_while_the_database_is_created(tmp_path, monkeypatch):
    # T01 on the very first open: no database file yet, so only the folder's pin protects it.
    import core.automation_store as automation_store
    from tests.test_runtime_descriptor import make_directory_alias

    home = tmp_path / "perfil"
    (home / "automations").mkdir(parents=True)
    outside, outside_db = make_outside(tmp_path)
    before = outside_rules(outside_db)
    folder, aside = home / "automations", home / "automations-apartada"
    real_connect, attempts = sqlite3.connect, []

    def swap_during_connect(*args, **kwargs):
        try:
            folder.rename(aside)
        except PermissionError:
            attempts.append("blocked")
            return real_connect(*args, **kwargs)
        attempts.append("swapped")
        make_directory_alias(folder, outside)
        try:
            return real_connect(*args, **kwargs)
        finally:
            folder.rmdir()
            aside.rename(folder)

    monkeypatch.setattr(automation_store.sqlite3, "connect", swap_during_connect)
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    monkeypatch.setattr(automation_store.sqlite3, "connect", real_connect)
    assert attempts and set(attempts) == {"blocked"}
    assert outside_rules(outside_db) == before and len(store.list_rules()) == 1


def plant_during_connect(monkeypatch, plants):
    """Codex's S01 timing: create hardlinks to outside files while SQLite opens the store."""
    import core.automation_store as automation_store

    real_connect, outcome = sqlite3.connect, {}

    def connect_with_plants(database, *args, **kwargs):
        for link, target in plants.items():
            if Path(database).name.startswith(".") or link.name in outcome:
                continue  # only the real path, once each
            try:
                os.link(target, link)
                outcome[link.name] = "planted"
            except FileExistsError:
                outcome[link.name] = "exists"  # Iris' own file is already there
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(automation_store.sqlite3, "connect", connect_with_plants)
    return outcome


@pytest.mark.parametrize("sidecar", ["-shm", "-journal", "-wal"])
def test_a_sidecar_linked_while_sqlite_opens_is_never_written(tmp_path, monkeypatch, sidecar):
    # S01: before, SQLite initialised a -shm planted during connect (27 → 32 768 bytes) and only
    # then was the link refused. Now -shm is never used, -journal neither, and the -wal is Iris' own.
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    target = tmp_path / f"ajeno{sidecar}.bin"
    target.write_bytes(b"archivo exterior de 27 bytes")
    before = target.read_bytes()
    outcome = plant_during_connect(monkeypatch, {store.path.with_name(store.path.name + sidecar): target})
    try:
        store.create_rule(rule())
        refused = False
    except StorePathError:
        refused = True
    monkeypatch.undo()
    assert target.read_bytes() == before  # never touched, whatever happened next
    if sidecar == "-wal":
        assert outcome == {"automations.sqlite3-wal": "exists"} and not refused
    else:
        assert outcome == {store.path.name + sidecar: "planted"} and refused  # the link is refused
        store.path.with_name(store.path.name + sidecar).unlink()
    assert len(store.list_rules()) == (1 if sidecar == "-wal" else 0)


def test_a_new_store_is_never_created_at_its_real_path_by_sqlite(tmp_path, monkeypatch):
    # S01 on creation: links planted at the real sidecar names while the new store is built.
    home = tmp_path / "perfil"
    (home / "automations").mkdir(parents=True)
    db = home / "automations" / "automations.sqlite3"
    targets = {}
    for sidecar in ("-shm", "-wal", "-journal"):
        targets[db.with_name(db.name + sidecar)] = tmp_path / f"ajeno{sidecar}.bin"
        targets[db.with_name(db.name + sidecar)].write_bytes(b"archivo exterior")
    import core.automation_store as automation_store

    real_image, created = automation_store._new_store_image, []

    def plant_when_building():
        for link, target in targets.items():
            os.link(target, link)
        created.append(True)
        return real_image()

    monkeypatch.setattr(automation_store, "_new_store_image", plant_when_building)
    with pytest.raises(StorePathError):
        AutomationStore.for_profile(home)  # built aside, then its real path holds links: refused
    monkeypatch.undo()
    assert created and all(target.read_bytes() == b"archivo exterior" for target in targets.values())


# ── C01: nothing of a new store is opened by SQLite before it is the store ──


def sha256(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def predictable_temp_name(monkeypatch):
    """The random name of the next new store, known in advance (as a watcher could learn it)."""
    import core.automation_store as automation_store

    real_new_id = automation_store.new_id
    fixed = [real_new_id()]
    monkeypatch.setattr(automation_store, "new_id", lambda: fixed.pop() if fixed else real_new_id())
    return f".automations.sqlite3.{fixed[0]}.new"


@pytest.mark.parametrize("stage", ["connect", "wal-mode"])
@pytest.mark.parametrize("sidecar", ["-wal", "-journal", "-shm"])
def test_c01_a_link_to_the_new_stores_temporary_sidecars_is_never_written(tmp_path, monkeypatch, stage, sidecar):
    # Codex's C01: a hardlink <temporal>-wal → an outside file, planted after the temporary store
    # was put in WAL mode, received 86 552 bytes. The new store is now built in memory and written
    # by Iris, so SQLite opens neither the temporary file nor any sidecar of it.
    import core.automation_store as automation_store

    home = tmp_path / "perfil"
    (home / "automations").mkdir(parents=True)
    outside = tmp_path / "fuera.bin"
    outside.write_bytes(b"OUTSIDE SYNTHETIC SENTINEL\n")
    before = sha256(outside)
    real_connect, events = sqlite3.connect, []

    class PlantAfterWal(sqlite3.Connection):
        temp_path = ""

        def execute(self, sql, *params):
            result = super().execute(sql, *params)
            if sql == "PRAGMA journal_mode = WAL":
                os.link(outside, self.temp_path + sidecar)
                events.append("planted after WAL mode")
            return result

    def connect_then_link(database, *args, **kwargs):
        path = Path(database)
        if not path.name.startswith("."):
            return real_connect(database, *args, **kwargs)
        events.append("sqlite opened the temporary store")
        if stage == "wal-mode":
            kwargs["factory"] = PlantAfterWal
        conn = real_connect(database, *args, **kwargs)
        conn.temp_path = str(path)
        if stage == "connect":
            os.link(outside, path.with_name(path.name + sidecar))
        return conn

    monkeypatch.setattr(automation_store.sqlite3, "connect", connect_then_link)
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    monkeypatch.undo()
    assert events == []  # SQLite never opened anything under the temporary name
    assert sha256(outside) == before and outside.stat().st_size == 27
    assert len(store.list_rules()) == 1


def test_c01_links_planted_in_advance_at_the_temporary_names_are_never_written(tmp_path, monkeypatch):
    # The strongest watcher: it knows the random name before the store is built and plants
    # links at every sidecar name of it. None is opened; the store is created and works.
    home = tmp_path / "perfil"
    folder = home / "automations"
    folder.mkdir(parents=True)
    temp = folder / predictable_temp_name(monkeypatch)
    targets = {}
    for suffix in ("-wal", "-journal", "-shm"):
        target = tmp_path / f"fuera{suffix}.bin"
        target.write_bytes(b"OUTSIDE SYNTHETIC SENTINEL\n")
        targets[target] = sha256(target)
        os.link(target, temp.with_name(temp.name + suffix))
    store = AutomationStore.for_profile(home)
    monkeypatch.undo()
    store.create_rule(rule())
    assert all(sha256(target) == digest for target, digest in targets.items())
    assert len(store.list_rules()) == 1 and not temp.exists()


def test_c01_a_link_at_the_temporary_name_itself_is_refused_without_publishing_anything(tmp_path, monkeypatch):
    home = tmp_path / "perfil"
    folder = home / "automations"
    folder.mkdir(parents=True)
    temp = folder / predictable_temp_name(monkeypatch)
    outside = tmp_path / "fuera.bin"
    outside.write_bytes(b"OUTSIDE SYNTHETIC SENTINEL\n")
    before = sha256(outside)
    os.link(outside, temp)
    with pytest.raises(StorePathError):
        AutomationStore.for_profile(home)
    monkeypatch.undo()
    assert sha256(outside) == before
    assert sorted(p.name for p in folder.iterdir()) == sorted(["automations.lock", temp.name])  # only the plant
    temp.unlink()
    if os.name == "nt":  # resources released: nothing stays pinned
        folder.rename(home / "otra")
        (home / "otra").rename(folder)
    store = AutomationStore.for_profile(home)  # then a normal creation
    store.create_rule(rule())
    assert len(store.list_rules()) == 1 and sha256(outside) == before


def test_c01_a_temporary_file_swapped_before_it_gets_its_name_is_refused(tmp_path, monkeypatch):
    # After Iris closed its file and before the rename, a watcher replaces it with a link.
    import core.automation_store as automation_store

    home = tmp_path / "perfil"
    folder = home / "automations"
    folder.mkdir(parents=True)
    outside = tmp_path / "fuera.bin"
    outside.write_bytes(b"OUTSIDE SYNTHETIC SENTINEL\n")
    before = sha256(outside)
    real_rename, swapped = os.rename, []

    def swap_then_rename(source, destination, *args, **kwargs):
        if Path(source).name.startswith(".automations.sqlite3."):
            Path(source).unlink()
            os.link(outside, source)
            swapped.append(True)
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(automation_store.os, "rename", swap_then_rename)
    with pytest.raises(StorePathError):
        AutomationStore.for_profile(home)
    monkeypatch.undo()
    assert swapped and sha256(outside) == before
    db = folder / "automations.sqlite3"
    with pytest.raises(StorePathError):
        AutomationStore.for_profile(home)  # the link at the real path is refused by every open
    assert sha256(outside) == before
    db.unlink()  # removes the link, keeps its target
    assert AutomationStore.for_profile(home).list_rules() == [] and sha256(outside) == before


def test_c01_a_failed_write_publishes_no_partial_store(tmp_path, monkeypatch):
    import core.automation_store as automation_store

    home = tmp_path / "perfil"
    folder = home / "automations"
    folder.mkdir(parents=True)

    def full_disk(fd):
        raise OSError(28, "No queda espacio (sintético)")

    monkeypatch.setattr(automation_store.os, "fsync", full_disk)
    with pytest.raises(OSError):
        AutomationStore.for_profile(home)
    monkeypatch.undo()
    assert sorted(p.name for p in folder.iterdir()) == ["automations.lock"]  # no store, no temporary file
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    assert len(store.list_rules()) == 1


def test_c01_a_new_store_is_a_valid_wal_store_with_its_own_identity(tmp_path):
    stores = []
    for name in ("perfil", "otro"):
        (tmp_path / name).mkdir()
        stores.append(AutomationStore.for_profile(tmp_path / name))
    db = stores[0].path
    assert db.read_bytes()[18:20] == b"\x02\x02"  # WAL mode is in the file itself
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert dict(conn.execute("SELECT key, value FROM meta"))["store"] == "iris.automation-store/v1"
    ids = []
    for store in stores:
        with closing(store._connect()) as conn:
            ids.append(conn.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone()[0])
    assert ids[0] != ids[1]


# ── C02: a newer store whose version is still in its WAL is refused untouched ──


def snapshot(db):
    """Presence, size and hash of the database and every sidecar."""
    names = [db, *(db.with_name(db.name + suffix) for suffix in ("-wal", "-shm", "-journal"))]
    return {p.name: (p.stat().st_size, sha256(p)) for p in names if p.is_file()}


CRASH_AFTER_COMMIT = """import json, os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1], isolation_level=None)
conn.execute("PRAGMA wal_autocheckpoint = 0")
for batch in json.loads(sys.argv[2]):
    conn.execute("BEGIN IMMEDIATE")
    for statement in batch:
        conn.execute(statement)
    conn.execute("COMMIT")
os._exit(0)
"""


def crash_after_commit(db, batches):
    """Another process commits each batch and dies without a checkpoint: the commits are only in the WAL."""
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", CRASH_AFTER_COMMIT, str(db), json.dumps(batches)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert db.with_name(db.name + "-wal").stat().st_size > 32


def recovered_by_sqlite(db, tmp_path):
    """What SQLite itself recovers, from a copy: (user_version, rows of crash_committed)."""
    import shutil

    copy = tmp_path / f"copia-{new_id()}"
    copy.mkdir()
    for suffix in ("", "-wal"):
        source = db.with_name(db.name + suffix)
        if source.exists():
            shutil.copyfile(source, copy / (db.name + suffix))
    with closing(sqlite3.connect(copy / db.name)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        rows = conn.execute("SELECT x FROM crash_committed").fetchall() if "crash_committed" in tables else []
    return version, rows


def test_c02_a_future_version_committed_only_in_the_wal_is_refused_untouched(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    db = store.path
    crash_after_commit(db, [["PRAGMA user_version = 7", "CREATE TABLE crash_committed (x TEXT)",
                             "INSERT INTO crash_committed VALUES ('committed before the crash')"]])
    assert int.from_bytes(db.read_bytes()[60:64], "big") == 2  # the header still says 2
    before = snapshot(db)
    assert {"automations.sqlite3", "automations.sqlite3-wal"} <= set(before)
    with pytest.raises(StoreVersionError, match="7"):
        AutomationStore.for_profile(home)
    with pytest.raises(StoreVersionError):
        store.list_rules()  # an Iris already running refuses it too
    assert snapshot(db) == before  # same files, sizes and hashes: nothing recovered or checkpointed
    assert recovered_by_sqlite(db, tmp_path) == (7, [("committed before the crash",)])  # nothing lost or moved down


def test_c02_the_current_version_committed_only_in_the_wal_is_recovered(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    db = store.path
    crash_after_commit(db, [["CREATE TABLE crash_committed (x TEXT)",
                             "INSERT INTO crash_committed VALUES ('committed before the crash')"]])
    reopened = AutomationStore.for_profile(home)
    assert len(reopened.list_rules()) == 1
    with closing(reopened._connect()) as conn:
        assert conn.execute("SELECT x FROM crash_committed").fetchall() == [("committed before the crash",)]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_c02_a_future_version_already_checkpointed_in_wal_mode_is_refused_untouched(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    assert not store.path.with_name(store.path.name + "-wal").exists()
    before = snapshot(store.path)
    with pytest.raises(StoreVersionError):
        AutomationStore.for_profile(home)
    assert snapshot(store.path) == before  # not even an empty -wal was created


CRASH_INSIDE_IRIS = """import os, sys
from pathlib import Path
from core.automation_contracts import AutomationRule
from core.automation_store import AutomationStore, _dump
store = AutomationStore.for_profile(Path(sys.argv[1]))
rule = AutomationRule.model_validate_json(sys.argv[2])
conn = store._connect()
conn.execute("BEGIN IMMEDIATE")
conn.execute("INSERT INTO rules VALUES (?, ?, ?)", (rule.id, rule.revision, _dump(rule)))
conn.execute("COMMIT")
os._exit(0)
"""


def test_c02_after_iris_itself_crashes_its_committed_write_is_recovered(tmp_path):
    import subprocess
    import sys

    home = tmp_path / "perfil"
    home.mkdir()
    AutomationStore.for_profile(home)
    wanted = rule()
    done = subprocess.run([sys.executable, "-c", CRASH_INSIDE_IRIS, str(home), wanted.model_dump_json(by_alias=True)],
                          capture_output=True, text=True, timeout=60, cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stderr
    db = home / "automations" / "automations.sqlite3"
    assert db.with_name(db.name + "-wal").stat().st_size > 32  # the write is only in the WAL
    assert [found.id for found in AutomationStore.for_profile(home).list_rules()] == [wanted.id]


@pytest.mark.parametrize("case", ["two-commits", "torn-last-commit", "later-commit-without-page-one",
                                  "bad-header-checksum", "bad-frame-checksum", "stale-salt"])
def test_c02_the_wal_is_read_exactly_as_sqlite_recovers_it(tmp_path, case):
    # Differential check: the version Iris reads from a WAL is the one SQLite recovers from a copy.
    from core.automation_store import _committed_page_one

    db = tmp_path / "prueba.sqlite3"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE crash_committed (x TEXT)")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    batches = {
        "later-commit-without-page-one": [["PRAGMA user_version = 7"], ["INSERT INTO crash_committed VALUES ('x')"]],
        "bad-header-checksum": [["PRAGMA user_version = 7"]],
        # page 1 is its transaction's first frame; the commit frame, last, is torn away
        "torn-last-commit": [["PRAGMA user_version = 3"],
                             ["PRAGMA user_version = 7", "INSERT INTO crash_committed VALUES (hex(randomblob(6000)))"]],
    }.get(case, [["PRAGMA user_version = 3"], ["PRAGMA user_version = 7"]])
    crash_after_commit(db, batches)
    wal = db.with_name(db.name + "-wal")
    data = bytearray(wal.read_bytes())
    frame_size = 24 + int.from_bytes(data[8:12], "big")
    if case == "torn-last-commit":
        data = data[:-100]
    elif case == "bad-header-checksum":
        data[27] ^= 0xFF
    elif case == "bad-frame-checksum":
        data[len(data) - frame_size + 24 + 200] ^= 0xFF  # inside the last frame's page
    elif case == "stale-salt":
        data[len(data) - frame_size + 8] ^= 0xFF  # the checksums do not cover a frame's salts
    wal.write_bytes(bytes(data))
    with open(wal, "rb") as handle:
        page = _committed_page_one(handle)
    ours = int.from_bytes((page if page is not None else db.read_bytes())[60:64], "big")
    assert ours == recovered_by_sqlite(db, tmp_path)[0]
    assert ours == {"two-commits": 7, "torn-last-commit": 3, "later-commit-without-page-one": 7,
                    "bad-header-checksum": 2, "bad-frame-checksum": 3, "stale-salt": 3}[case]


def test_wal_still_works_and_no_shm_is_ever_created(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    store.create_rule(rule())
    with closing(store._connect()) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert not store.path.with_name(store.path.name + "-shm").exists()
    leftovers = sorted(p.name for p in store.path.parent.iterdir())
    assert leftovers == ["automations.lock", "automations.sqlite3"]  # -wal removed on close, no -shm


@pytest.mark.skipif(os.name != "nt", reason="the path is pinned with Windows handles")
def test_a_refused_open_releases_its_pins_and_the_stores_lock(tmp_path, monkeypatch):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    target = tmp_path / "ajeno.bin"
    target.write_bytes(b"x")
    plant_during_connect(monkeypatch, {store.path.with_name(store.path.name + "-shm"): target})
    with pytest.raises(StorePathError):
        store.list_rules()
    monkeypatch.undo()
    store.path.with_name(store.path.name + "-shm").unlink()
    folder = store.path.parent
    folder.rename(home / "otra")  # nothing stays pinned
    (home / "otra").rename(folder)
    errors = []

    def other_thread():
        try:
            store.list_rules()  # the lock was released: another thread opens at once
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join(10)
    assert not worker.is_alive() and errors == []


def test_two_stores_and_threads_take_turns_without_locking_errors(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    stores = [AutomationStore.for_profile(home) for _ in range(2)]
    errors = []

    def work(store):
        try:
            for _ in range(15):
                store.create_rule(rule())
                store.list_rules()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(store,)) for store in stores for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert errors == [] and len(stores[0].list_rules()) == 60


def test_a_connection_that_reached_another_store_is_refused(tmp_path):
    home = tmp_path / "perfil"
    home.mkdir()
    store = AutomationStore.for_profile(home)
    _, outside_db = make_outside(tmp_path)

    def another_store(factory):
        return sqlite3.connect(str(outside_db), factory=factory)

    with pytest.raises(StorePathError, match="no llegó"):
        store._checked(another_store)
    assert len(outside_rules(outside_db)) == 1


def test_an_authorised_alias_of_the_profile_root_still_works(tmp_path):
    from tests.test_runtime_descriptor import make_directory_alias

    real = tmp_path / "perfil real"
    real.mkdir()
    alias = tmp_path / "alias del perfil"
    make_directory_alias(alias, real)
    try:
        store = AutomationStore.for_profile(alias)  # the root is canonicalised, not refused
        store.create_rule(rule())
        assert store.path.parent.parent == real.resolve()
        assert len(AutomationStore.for_profile(real).list_rules()) == 1
    finally:
        alias.rmdir()


def test_unreadable_rows_are_reported_not_deleted(store):
    db, clock = store
    saved = db.create_rule(rule())
    with closing(sqlite3.connect(db.path)) as conn, conn:  # closed: Iris' connections are exclusive
        conn.execute("UPDATE rules SET document = ? WHERE id = ?", (json.dumps({"schema": "x"}), saved.id))
    assert db.check() == [f"rules:{saved.id}"]
    with closing(sqlite3.connect(db.path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 1


# ── Presence ──────────────────────────────────────────────────────────────


class Clocks:
    def __init__(self):
        self.mono, self.wall = 1000.0, 1_800_000_000.0

    def step(self, seconds, *, wall_extra=0.0):
        self.mono += seconds
        self.wall += seconds + wall_extra


@pytest.fixture
def presence():
    clocks = Clocks()
    tracker = PresenceTracker(monotonic=lambda: clocks.mono, wall=lambda: clocks.wall, run_id="r1")
    return tracker, clocks


def test_iris_idle_counts_observed_time_only(presence):
    tracker, clocks = presence
    assert tracker.state("iris_idle").status == "unknown"
    tracker.iris_tick(local_surface_open=True)
    first = tracker.state("iris_idle")
    assert first.inactive and first.confirmed_s == 0 and first.episode == "iris_idle:r1:1"
    for _ in range(60):
        clocks.step(5)
        tracker.iris_tick(local_surface_open=True)
    assert tracker.state("iris_idle").confirmed_s == 300
    clocks.step(600)  # suspended: no heartbeat
    assert tracker.state("iris_idle").status == "unknown"
    tracker.iris_tick(local_surface_open=True)
    after = tracker.state("iris_idle")
    assert after.confirmed_s == 300 and after.episode == "iris_idle:r1:1"  # the gap was not added
    assert after.reason == "observed"  # observation resumed; the stale reason does not linger
    tracker.iris_interaction()
    assert tracker.state("iris_idle").condition == "active" and tracker.state("iris_idle").episode == ""
    assert tracker.state("iris_idle").reason == "interaction"
    clocks.step(5)
    tracker.iris_tick(local_surface_open=True)
    assert tracker.state("iris_idle").episode == "iris_idle:r1:2"  # a new episode, from zero
    assert tracker.state("iris_idle").reason == "observed"
    tracker.iris_tick(local_surface_open=False)
    assert tracker.state("iris_idle").reason == "iris_closed"
    tracker.iris_tick(local_surface_open=True)
    assert tracker.state("iris_idle").reason == "observed"  # the dev stack showed "iris_closed" here


def test_a_read_from_another_thread_never_sees_half_an_update(presence, monkeypatch):
    # F05 reads in a worker thread while Core's loop ticks: a read waits for the whole update.
    tracker, _ = presence
    seen, readers = [], []
    original = tracker._new_episode

    def new_episode_with_a_reader(lane):
        reader = threading.Thread(target=lambda: seen.append(tracker.state("iris_idle")))
        reader.start()
        reader.join(0.2)
        readers.append(reader)
        original(lane)

    monkeypatch.setattr(tracker, "_new_episode", new_episode_with_a_reader)
    tracker.iris_tick(local_surface_open=True)
    readers[0].join(5)
    assert seen[0].condition == "idle" and seen[0].episode == "iris_idle:r1:1"


def test_clock_jumps_and_a_closed_iris_add_no_time(presence):
    tracker, clocks = presence
    tracker.iris_tick(local_surface_open=True)
    clocks.step(5)
    tracker.iris_tick(local_surface_open=True)
    clocks.step(5, wall_extra=3600)  # the wall clock jumps an hour
    tracker.iris_tick(local_surface_open=True)
    clocks.step(5, wall_extra=-20)  # and goes back
    tracker.iris_tick(local_surface_open=True)
    assert tracker.state("iris_idle").confirmed_s == 5
    tracker.iris_tick(local_surface_open=False)
    state = tracker.state("iris_idle")
    assert state.status == "unknown" and state.reason == "iris_closed"


def test_observed_absence_needs_prior_presence_and_reliable_continuous_samples(presence):
    tracker, clocks = presence
    tracker.camera_sample(present=False, confidence=0.9, surface="face")
    assert tracker.state("observed_absence").status == "unknown"  # never seen present
    tracker.camera_sample(present=True, confidence=0.9, surface="face")
    for _ in range(4):
        clocks.step(2)
        tracker.camera_sample(present=False, confidence=0.9, surface="face")
    absent = tracker.state("observed_absence")
    assert absent.inactive and absent.confirmed_s == 6  # counted from the first absent sample
    clocks.step(2)
    tracker.camera_sample(present=False, confidence=0.4, surface="face")  # doubtful
    assert tracker.state("observed_absence").status == "unknown"
    clocks.step(2)
    tracker.camera_sample(present=False, confidence=0.9, surface="face")
    assert tracker.state("observed_absence").status == "unknown"  # presence needed again


@pytest.mark.parametrize("reason", ["camera_off", "hidden", "disconnected", "permission_denied", "paused"])
def test_camera_states_without_signal_are_unknown_never_absence(presence, reason):
    tracker, clocks = presence
    tracker.camera_sample(present=True, confidence=0.9, surface="face")
    clocks.step(2)
    tracker.camera_sample(present=False, confidence=0.9, surface="face")
    tracker.camera_unavailable(reason)
    state = tracker.state("observed_absence")
    assert state.status == "unknown" and state.reason == reason and state.confirmed_s == 0


def test_stale_camera_signal_and_surface_change_stop_the_count(presence):
    tracker, clocks = presence
    tracker.camera_sample(present=True, confidence=0.9, surface="face")
    clocks.step(2)
    tracker.camera_sample(present=False, confidence=0.9, surface="face")
    clocks.step(9)  # longer than Vision V2's 8 s gap
    assert tracker.state("observed_absence").status == "unknown"
    tracker.camera_sample(present=False, confidence=0.9, surface="face")
    assert tracker.state("observed_absence").status == "unknown"  # the broken episode needs presence again
    tracker.camera_sample(present=True, confidence=0.9, surface="face")
    clocks.step(2)
    tracker.camera_sample(present=False, confidence=0.9, surface="companion")  # another surface
    assert tracker.state("observed_absence").status == "unknown"


def test_desktop_idle_is_unknown_until_its_adapter_reports(presence):
    tracker, clocks = presence
    assert tracker.state("desktop_idle").reason == "no_adapter"
    tracker.desktop_sample(idle=True)
    clocks.step(10)
    tracker.desktop_sample(idle=True)
    assert tracker.state("desktop_idle").confirmed_s == 10
    tracker.desktop_sample(idle=False)
    assert tracker.state("desktop_idle").condition == "active"


def test_a_restart_never_resumes_an_episode(presence):
    tracker, clocks = presence
    tracker.iris_tick(local_surface_open=True)
    restarted = PresenceTracker(monotonic=lambda: clocks.mono, wall=lambda: clocks.wall, run_id="r2")
    restarted.iris_tick(local_surface_open=True)
    assert restarted.state("iris_idle").episode == "iris_idle:r2:1"
    assert restarted.state("iris_idle").confirmed_s == 0
