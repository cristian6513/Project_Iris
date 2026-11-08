"""Calendar uses Core's existing scheduler, including profiles without automation rules."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.automation_api import AutomationRuntime
from core.automation_engine import Decision, run_loop
from core.calendar_runtime import CalendarRuntime
from test_calendar_models import timed
from test_calendar_service import Cache

NOW = datetime(2026, 10, 16, 15, tzinfo=timezone.utc).timestamp()


class NoTransport:
    def __getattr__(self, name):
        raise AssertionError(f"The calendar scheduler must not call Hermes transport: {name}")


def automation(home, hook):
    return AutomationRuntime(home=home, transport=NoTransport(), permission=lambda: "deny",
                             profile_problem=lambda profile: "", local_surface_open=lambda: False,
                             interval=0, owner="calendar-scheduler-test", after_tick=hook)


async def collect_children(runtime):
    while runtime._tasks:
        await asyncio.gather(*list(runtime._tasks), return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.parametrize("automation_store_exists", [False, True])
def test_real_calendar_ticks_without_a_second_scheduler_or_automation_permission(tmp_path, automation_store_exists):
    home = tmp_path / "profile"
    home.mkdir()
    cache = Cache()
    cache.value["calendars"][0]["events"] = [timed(start="2026-10-16T11:00:00-05:00",
                                                   end="2026-10-16T12:00:00-05:00")]
    calendar = CalendarRuntime(home, google=lambda: cache, clock=lambda: NOW)
    calendar.set_preferences(revision=0, reminders_enabled=True, time_zone="America/Bogota")
    calls = []

    async def run():
        async def after_tick():
            calls.append("calendar")
            await calendar.tick(force=True)
            if len(calls) == 3:
                await runtime.close()

        runtime = automation(home, after_tick)
        if automation_store_exists:
            assert runtime.store(create=True) is not None
            await collect_children(runtime)
        try:
            await asyncio.wait_for(runtime.run(), 5)
            await collect_children(runtime)
            assert len(calls) == 3 and runtime.path.exists() is automation_store_exists
            assert len(calendar.inbox_rows()) == 1
            notice = calendar.inbox_rows()[0]
            assert notice.phase == "before_60" and notice.event_id.startswith("event_")
            assert notice.presented_at is None and notice.read_at is None
            assert cache.reads == 3 and cache.syncs == []
        finally:
            await runtime.close()
            await collect_children(runtime)
            await calendar.close()

    asyncio.run(run())


def test_engine_loop_runs_calendar_hook_in_order_without_spawning_another_task():
    async def run():
        stop = asyncio.Event()
        calls = []
        owner = []

        def tick():
            owner.append(asyncio.current_task())
            calls.append("presence")

        async def after_tick():
            assert asyncio.current_task() is owner[-1]
            calls.append("calendar")

        def evaluate():
            calls.append("automations")
            return [Decision("rule", 1, "ready")]

        async def on_ready(decision):
            assert decision.outcome == "ready"
            calls.append("dispatch")
            stop.set()

        await asyncio.wait_for(run_loop(SimpleNamespace(evaluate=evaluate), tick=tick,
            after_tick=after_tick, on_ready=on_ready, interval=0, stop=stop), 3)
        assert calls == ["presence", "calendar", "automations", "dispatch"]

    asyncio.run(run())


def test_stopping_during_calendar_hook_does_not_evaluate_or_dispatch_after_close():
    async def run():
        stop, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def after_tick():
            entered.set()
            await release.wait()

        def evaluate():
            calls.append("evaluate-after-stop")
            return [Decision("rule", 1, "ready")]

        async def on_ready(_):
            calls.append("dispatch-after-stop")

        task = asyncio.create_task(run_loop(SimpleNamespace(evaluate=evaluate), tick=lambda: None,
            after_tick=after_tick, on_ready=on_ready, interval=0, stop=stop))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            stop.set()
            release.set()
            await asyncio.wait_for(task, 3)
            assert calls == []
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_cancelled_runtime_collects_hook_and_never_opens_a_new_automation_store(tmp_path):
    home = tmp_path / "profile"
    home.mkdir()

    async def run():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        calls = []

        async def after_tick():
            calls.append("calendar")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        runtime = automation(home, after_tick)
        task = asyncio.create_task(runtime.run())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await runtime.close()
            assert cleaned.is_set() and calls == ["calendar"]
            assert not runtime.path.exists() and not runtime._tasks
            await runtime.run()
            assert calls == ["calendar"]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_runtime_close_during_calendar_hook_does_not_open_store_or_restart_hook(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def after_tick():
            entered.set()
            await release.wait()

        runtime = automation(home, after_tick)
        monkeypatch.setattr(runtime, "store", lambda **kwargs: calls.append("opened-after-close"))
        task = asyncio.create_task(runtime.run())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await runtime.close()
            release.set()
            await asyncio.wait_for(task, 3)
            assert calls == []
            await runtime.run()
            assert calls == []
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())
