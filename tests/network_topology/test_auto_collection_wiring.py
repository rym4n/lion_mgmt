import asyncio
import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import settings
from services import app_factory
from services.network_topology import views


def test_upload_blueprint_is_registered_with_topology_license_module():
    assert ("services.network_topology.upload_views", "upload_bp") in app_factory.LICENSED_BLUEPRINT_SPECS["topology_bp"]


def test_v2_flags_default_on(monkeypatch):
    names = ("TOPOLOGY_V2_INGRESS_ENABLED", "TOPOLOGY_V2_ANALYSIS_ENABLED", "TOPOLOGY_V2_PUBLISH_ENABLED", "TOPOLOGY_V2_VIEW_ENABLED", "TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(settings)
    try:
        assert all(getattr(settings, name) is True for name in names)
    finally:
        monkeypatch.undo()
        importlib.reload(settings)


def test_v2_sync_stub_removed():
    assert "尚未启用" not in inspect.getsource(views._sync_v2_topology_snapshot)


def test_listener_registered():
    from core.listeners import LISTENER_TUPLE
    from core.listeners.topology_analysis import TopologyAnalysisListener
    assert TopologyAnalysisListener in LISTENER_TUPLE


@pytest.mark.asyncio
async def test_sync_delegates_after_permission_and_topology_checks(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)
    permission = AsyncMock()
    adapter = AsyncMock(return_value=SimpleNamespace(status=202))
    monkeypatch.setattr(views, "check_permissions", permission)
    monkeypatch.setattr(views, "_validate_snapshot_shape", lambda data: None)
    monkeypatch.setattr(views, "_tenant_topology", AsyncMock(return_value=object()))
    monkeypatch.setattr(views.config_sync_views, "sync_topology_snapshot", adapter)
    request = object()
    result = await views._sync_v2_topology_snapshot(request, {}, "tenant", "topology")
    assert result.status == 202
    permission.assert_awaited_once_with(request, "network_topology:sync")
    adapter.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_listener_starts_once_and_awaits_shutdown(monkeypatch):
    from core.listeners.topology_analysis import TopologyAnalysisListener
    from services.network_topology import worker, analysis_pipeline
    started = asyncio.Event()
    stopped = asyncio.Event()
    async def forever():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    service = SimpleNamespace(run_forever=forever, shutdown=AsyncMock())
    factory = Mock(return_value=service)
    monkeypatch.setattr(worker, "TopologyAnalysisWorker", factory)
    monkeypatch.setattr(analysis_pipeline, "build_default_pipeline", lambda: object())
    app = SimpleNamespace(ctx=SimpleNamespace())
    listener = TopologyAnalysisListener()
    await listener.after_server_start(app, asyncio.get_running_loop())
    await started.wait()
    await listener.after_server_start(app, asyncio.get_running_loop())
    assert factory.call_count == 1
    await listener.before_server_stop(app, asyncio.get_running_loop())
    assert stopped.is_set()
    assert app.ctx.topology_analysis_task.done()
    assert app.ctx.topology_collection_sweep_task.done()
    service.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_pipeline_fails_closed_for_pending_agent_observations():
    from services.network_topology.analysis_pipeline import build_default_pipeline
    from services.network_topology.worker import PipelineFailure
    pipeline = build_default_pipeline()
    with pytest.raises(PipelineFailure, match="AGENT_NOT_CONFIGURED"):
        await pipeline.agent_stage.run(SimpleNamespace(agent_observation_ids=["raw"]), {}, None)


@pytest.mark.asyncio
async def test_worker_cancellation_drains_stage_and_heartbeat():
    from services.network_topology.worker import TopologyAnalysisWorker
    started = asyncio.Event()
    finished = asyncio.Event()
    async def stage(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
    service = TopologyAnalysisWorker(worker_id="test", pipeline=SimpleNamespace(execute_stage=stage))
    heartbeat_started = asyncio.Event()
    heartbeat_finished = asyncio.Event()
    async def heartbeat(*args):
        heartbeat_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            heartbeat_finished.set()
    service._heartbeat_until_stopped = heartbeat
    task = asyncio.create_task(service._execute_with_heartbeat(None, None, None))
    await started.wait()
    await heartbeat_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert heartbeat_finished.is_set()


@pytest.mark.asyncio
async def test_listener_cancels_task_even_if_shutdown_fails(monkeypatch):
    from core.listeners.topology_analysis import TopologyAnalysisListener
    task = asyncio.create_task(asyncio.Event().wait())
    app = SimpleNamespace(ctx=SimpleNamespace(
        topology_analysis_worker=SimpleNamespace(shutdown=AsyncMock(side_effect=RuntimeError("release failed"))),
        topology_analysis_task=task,
    ))
    with pytest.raises(RuntimeError, match="release failed"):
        await TopologyAnalysisListener().before_server_stop(app, asyncio.get_running_loop())
    assert task.cancelled()
