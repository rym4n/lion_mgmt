# coding=utf-8
import asyncio
from types import SimpleNamespace

import pytest


class _App:
    def __init__(self):
        self.ctx = SimpleNamespace(cache_client=object())
        self.tasks = {}

    def add_task(self, coroutine, *, name):
        self.tasks[name] = asyncio.create_task(coroutine)

    async def cancel_task(self, name):
        task = self.tasks.get(name)
        if task is None or task.done():
            return
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_dashboard_refresh_listener_survives_refresh_failure(monkeypatch):
    from core.listeners.dashboard import DashboardMaterializedViewListener

    calls = 0
    second_call = asyncio.Event()

    async def refresh(_app):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database temporarily unavailable")
        second_call.set()

    monkeypatch.setattr(
        "core.listeners.dashboard.refresh_dashboard_materialized_views", refresh
    )
    monkeypatch.setattr(
        "core.listeners.dashboard._dashboard_refresh_interval_seconds", lambda: 0
    )

    app = _App()
    listener = DashboardMaterializedViewListener()
    await listener.after_server_start(app, asyncio.get_running_loop())
    try:
        await asyncio.wait_for(second_call.wait(), timeout=1)
    finally:
        await listener.before_server_stop(app, asyncio.get_running_loop())

    assert calls >= 2


@pytest.mark.asyncio
async def test_dashboard_refresh_listener_is_registered():
    from core.listeners import CacheListener, LISTENER_TUPLE
    from core.listeners.dashboard import DashboardMaterializedViewListener

    assert DashboardMaterializedViewListener in LISTENER_TUPLE
    assert LISTENER_TUPLE.index(CacheListener) < LISTENER_TUPLE.index(
        DashboardMaterializedViewListener
    )
