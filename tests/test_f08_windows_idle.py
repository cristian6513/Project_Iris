"""F08a: native metadata + real tracker/store/engine/API, with synthetic input.

No input injection, personal settings, Hermes, camera or microphone.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.automation_api import AutomationRuntime, automation_router
from core.automation_contracts import AutomationRule, new_id
from core.automation_store import AutomationStore, RevisionConflict
from core.presence_state import PresenceTracker
from core.windows_idle import DesktopIdleObserver, InputSample, NativeObservationError


class Reader:
    supported = True
    def __init__(self, clock):
        self.clock, self.calls, self.tick, self.error = clock, 0, 0, False
    def read(self):
        self.calls += 1
        if self.error:
            raise NativeObservationError('synthetic locked session')
        return InputSample(int(self.clock[0] * 1000), self.tick)


@pytest.fixture
def world():
    clock = [0., 1_800_000_000.]
    tracker = PresenceTracker(monotonic=lambda: clock[0], wall=lambda: clock[1], run_id='f08')
    reader = Reader(clock)
    return SimpleNamespace(clock=clock, tracker=tracker, reader=reader,
                          observer=DesktopIdleObserver(tracker, reader=reader))


def step(w, seconds=5, *, enabled=True, revision=1):
    w.clock[0] += seconds
    w.clock[1] += seconds
    return w.observer.tick(enabled=enabled, revision=revision)


def test_disabled_and_unsupported_never_call_native_input(world):
    step(world, enabled=False)
    world.reader.supported = False
    step(world)
    assert world.reader.calls == 0 and world.tracker.state('desktop_idle').status == 'unknown'


def test_299_300_observed_seconds_then_input_outside_iris_ends_episode(world):
    world.observer.tick(enabled=True, revision=1)
    step(world)  # first interval begins an episode at zero
    for _ in range(59):
        step(world)
    step(world, 4)
    assert world.tracker.state('desktop_idle').confirmed_s == 299
    step(world, 1)
    state = world.tracker.state('desktop_idle')
    assert state.confirmed_s == 300 and state.inactive
    world.reader.tick = int((world.clock[0]+1)*1000)
    assert step(world, 1) is True
    assert world.tracker.state('desktop_idle').condition == 'active'
    assert not world.tracker.state('desktop_idle').episode
    assert step(world) is False
    assert world.tracker.state('desktop_idle').episode != state.episode


@pytest.mark.parametrize('kind', ['sleep', 'clock', 'error', 'disable', 'revision', 'restart'])
def test_unknown_intervals_never_complete_or_resume_an_old_episode(world, kind):
    world.observer.tick(enabled=True, revision=1)
    for _ in range(4):
        step(world)
    old = world.tracker.state('desktop_idle').episode
    if kind == 'clock':
        world.clock[1] += 1000
    if kind == 'restart':
        world.observer = DesktopIdleObserver(world.tracker, reader=world.reader)
    world.reader.error = kind == 'error'
    step(world, 600 if kind == 'sleep' else 5, enabled=kind != 'disable', revision=2 if kind == 'revision' else 1)
    assert world.tracker.state('desktop_idle').confirmed_s == 0
    assert world.tracker.state('desktop_idle').episode != old
    world.reader.error = False
    for _ in range(4):
        step(world, revision=2)
    assert world.tracker.state('desktop_idle').status == 'known'
    assert world.tracker.state('desktop_idle').confirmed_s <= 15


def test_dword_wrap_is_observed_continuity_not_a_new_49_day_idle_period(world):
    world.clock[:] = [(2**32 - 1000)/1000, 1_800_000_000.]
    world.reader.tick = 2**32 - 2000
    world.observer.tick(enabled=True, revision=1)
    for _ in range(3):
        step(world)
    assert world.tracker.state('desktop_idle').confirmed_s == 10


def test_nonincremental_input_is_activity_not_more_idle(world):
    world.clock[0] = 100
    world.reader.tick = 90_000
    world.observer.tick(enabled=True, revision=1)
    step(world)
    world.reader.tick = 89_000  # Windows can report non-increasing SendInput ticks
    assert step(world)
    assert world.tracker.state('desktop_idle').confirmed_s == 0


def runtime(tmp_path, world):
    r = AutomationRuntime(home=tmp_path, transport=object(), permission=lambda:'deny',
        profile_problem=lambda p:'', local_surface_open=lambda:False, desktop_reader=world.reader)
    r.tracker = world.tracker
    r.desktop_observer = world.observer
    r.store(create=True)
    return r


def test_real_runtime_observes_minimized_without_observing_iris_and_disable_stops_it(tmp_path, world):
    r = runtime(tmp_path, world)
    assert r.ready_sources() == {'iris_idle'}
    r._tick()
    assert world.reader.calls == 0
    prefs = r.configure_presence(expected_revision=0, desktop_idle=True)
    assert prefs.revision == 1 and 'desktop_idle' in r.ready_sources()
    for _ in range(10):
        world.clock[0] += 5
        world.clock[1] += 5
        r._tick()
    assert r.tracker.state('iris_idle').status == 'unknown'
    assert r.tracker.state('desktop_idle').confirmed_s == 40
    world.reader.tick = int((world.clock[0]+1)*1000)
    world.clock[0] += 1
    world.clock[1] += 1
    r._tick()
    assert r.take_desktop_return() and not r.take_desktop_return()
    r.configure_presence(expected_revision=1, desktop_idle=False)
    calls = world.reader.calls
    r._tick()
    assert world.reader.calls == calls and r.tracker.state('desktop_idle').status == 'unknown'
    asyncio.run(r.close())
    r._tick()
    assert world.reader.calls == calls


def test_preferences_survive_restart_and_reject_stale_or_nonboolean_changes(tmp_path):
    store = AutomationStore.for_profile(tmp_path)
    assert not store.automation_preferences().desktop_idle
    saved = store.set_automation_preferences(expected_revision=0, desktop_idle=True)
    assert saved.revision == 1
    reopened = AutomationStore.for_profile(tmp_path)
    assert reopened.automation_preferences() == saved
    with pytest.raises(RevisionConflict):
        reopened.set_automation_preferences(expected_revision=0, desktop_idle=False)
    for revision in (False, -1, '1'):
        with pytest.raises(ValueError):
            reopened.set_automation_preferences(expected_revision=revision, desktop_idle=False)
    for value in ('true', 1, None):
        with pytest.raises(ValueError):
            reopened.set_automation_preferences(expected_revision=1, desktop_idle=value)
    assert reopened.automation_preferences() == saved


def test_real_api_requires_consent_and_does_not_change_automation_permission(tmp_path, world):
    r = runtime(tmp_path, world)
    app = FastAPI()
    app.include_router(automation_router(lambda:r, permission=lambda:'deny'))
    with TestClient(app) as client:
        before = client.get('/api/automations').json()
        assert before['presence']['desktop_idle'] is False
        assert client.put('/api/automations/preferences',json={'expected_revision':0,'desktop_idle':True}).status_code == 409
        assert client.put('/api/automations/preferences',json={'expected_revision':False,'desktop_idle':True,'confirm':True}).status_code == 400
        response = client.put('/api/automations/preferences',json={'expected_revision':0,'desktop_idle':True,'confirm':True})
        assert response.status_code == 200 and response.json()['desktop_idle'] is True
        after = client.get('/api/automations').json()
        assert after['permission'] == 'deny' and 'desktop_idle' in after['ready_sources']
        assert client.put('/api/automations/preferences',json={'expected_revision':0,'desktop_idle':False}).status_code == 409
    assert world.reader.calls == 0  # saving a preference starts neither a probe nor a task


def test_an_active_iris_rule_cannot_switch_to_windows_without_consent(tmp_path, world):
    r = runtime(tmp_path, world)
    app = FastAPI()
    app.include_router(automation_router(lambda:r,permission=lambda:'deny'))
    with TestClient(app) as client:
        rule=client.post('/api/automations/rules',json={'title':'Synthetic',
            'trigger':{'source':'iris_idle','threshold_s':60},'execution':{'prompt':'Synthetic'}}).json()
        active=client.put('/api/automations/rules/'+rule['id'],json={'expected_revision':1,'enabled':True,'confirm':True}).json()
        response=client.put('/api/automations/rules/'+rule['id'],json={'expected_revision':active['revision'],
            'trigger':{'source':'desktop_idle','threshold_s':60}})
        assert response.status_code==409 and response.json()['detail']['code']=='rule_not_ready'
        assert r.store().get_rule(rule['id']).trigger.source=='iris_idle'


def test_real_engine_has_no_windows_occurrence_without_consent_then_one_per_episode(tmp_path, world):
    r = runtime(tmp_path, world)
    rule = r.store().create_rule(AutomationRule(id=new_id(), title='Synthetic task',enabled=True,
        trigger={'source':'desktop_idle','threshold_s':60},execution={'prompt':'Do not contact a provider'},
        created_at=world.clock[1],updated_at=world.clock[1]))
    for _ in range(20):
        world.clock[0]+=5; world.clock[1]+=5
        r._tick()
    assert not r._engine.evaluate() or all(d.outcome != 'ready' for d in r._engine.evaluate())
    assert r.store().occurrences() == []
    r.configure_presence(expected_revision=0,desktop_idle=True)
    for n in range(14):
        world.clock[0]+=5; world.clock[1]+=5
        r._tick()
        r._engine.evaluate()
    occurrences = r.store().occurrences()
    assert len(occurrences)==1 and occurrences[0].rule_id==rule.id
    r.configure_presence(expected_revision=1,desktop_idle=False)
    assert not r.tracker.state('desktop_idle').inactive
