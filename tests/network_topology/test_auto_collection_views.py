from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from core.maintenance import DEFAULT_TENANT_ID
from services.network_topology.collection import plans, runs, views
from tests.network_topology.auto_collection_fakes import FakeRunRepository, plan, run_row


def _request(body, tenant_id):
    return SimpleNamespace(
        json=body,
        ctx=SimpleNamespace(
            session={
                "tenant_id": tenant_id,
                "sub": "acc-1",
                "account_info": SimpleNamespace(id="acc-1"),
            }
        ),
    )


def test_internal_caller_must_be_default_tenant():
    with pytest.raises(views.CollectionError) as exc:
        views._require_internal_caller(_request({"plan_id": "p1"}, "tenant-b"))
    assert exc.value.code == "COLLECTION_CALLER_FORBIDDEN"
    views._require_internal_caller(_request({"plan_id": "p1"}, DEFAULT_TENANT_ID))


def test_internal_caller_rejects_tenant_in_body():
    with pytest.raises(views.CollectionError):
        views._require_internal_caller(
            _request({"plan_id": "p1", "tenant_id": "x"}, DEFAULT_TENANT_ID)
        )


def test_routes_registered():
    paths = {
        getattr(route, "path", getattr(route, "uri", ""))
        for route in views.collection_bp._future_routes
    }
    for path in (
        "/collection-plan/get",
        "/collection-plan/save",
        "/collection-run/trigger",
        "/collection-run/cancel",
        "/collection-run/list",
        "/collection-run/detail",
        "/internal/collection/start",
        "/internal/collection/manifest",
        "/internal/collection/dispatch",
        "/internal/collection/snapshot",
        "/internal/collection/status",
    ):
        assert any(item.endswith(path) for item in paths), path


@pytest.mark.asyncio
async def test_collection_summaries_batches_queries(monkeypatch):
    calls = []

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def __await__(self):
            async def done():
                return self.rows

            return done().__await__()

    plan = SimpleNamespace(
        id="p1",
        topology_id="top-1",
        enabled=True,
        schedule_cron="0 0 2 * * *",
        last_run_id="run-1",
        items=[SimpleNamespace(enabled=True)],
    )
    run = SimpleNamespace(
        id="run-1",
        state="SUCCEEDED",
        result="changed",
        finished_at=None,
        error_code=None,
    )

    class PlanModel:
        @classmethod
        def filter(cls, **kwargs):
            calls.append(("plan", kwargs))
            return Query([plan])

    class RunModel:
        @classmethod
        def filter(cls, *conditions, **kwargs):
            calls.append(("run", kwargs))
            assert conditions[0].join_type == "OR"
            return Query([run])

    monkeypatch.setattr(plans, "TopologyCollectionPlan", PlanModel)
    monkeypatch.setattr(plans, "TopologyCollectionRun", RunModel)
    result = await plans.collection_summaries("tenant-a", ["top-1"])
    assert len(calls) == 2
    assert calls[0][1]["topology_id__in"] == ["top-1"]
    assert calls[1][1]["tenant_id"] == "tenant-a"
    assert result["top-1"]["last_run"]["id"] == "run-1"


def _unwrapped(handler):
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


@pytest.mark.asyncio
async def test_save_rejects_tenant_before_topology_lookup(monkeypatch):
    topology_lookup = pytest.MonkeyPatch()
    try:
        topology_lookup.setattr(views, "_require_topology", pytest.fail)
        response = await _unwrapped(views.save_collection_plan)(
            _request({"id": "top-1", "tenant_id": "spoof"}, "tenant-a")
        )
    finally:
        topology_lookup.undo()
    assert response.status == 403


@pytest.mark.asyncio
async def test_run_detail_requires_topology_id(monkeypatch):
    response = await _unwrapped(views.detail_collection_run)(
        _request({"run_id": "run-1"}, "tenant-a")
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_internal_status_scopes_run_to_plan_tenant():
    repo = FakeRunRepository(plans=[plan("p1", tenant_id="tenant-a")])
    repo.runs["run-other"] = run_row("run-other", plan_id="p1", tenant_id="tenant-b")
    with pytest.raises(views.CollectionError) as exc:
        await runs.get_run_for_plan("p1", "run-other", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_NOT_FOUND"
    repo.runs["run-own"] = run_row("run-own", plan_id="p1", tenant_id="tenant-a")
    resolved_plan, resolved_run = await runs.get_run_for_plan("p1", "run-own", repository=repo)
    assert resolved_plan.tenant_id == "tenant-a" and resolved_run.id == "run-own"

    default_plan = plan("p-default", tenant_id=DEFAULT_TENANT_ID)
    repo = FakeRunRepository(plans=[default_plan])
    repo.runs["run-other"] = run_row(
        "run-other", plan_id="p-default", tenant_id="tenant-b"
    )
    with pytest.raises(views.CollectionError) as exc:
        await runs.get_run_for_plan("p-default", "run-other", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["items", "freeze", "issue"])
@pytest.mark.parametrize("known_error", [True, False])
@pytest.mark.parametrize("notification_fails", [False, True])
async def test_manifest_failure_terminalizes_open_run(monkeypatch, stage, known_error, notification_fails):
    repo = FakeRunRepository(plans=[plan(
        "p1", notify_enabled=notification_fails,
        consecutive_failures=2 if notification_fails else 0,
    )])
    notification = AsyncMock(side_effect=RuntimeError("INTERNAL_NOTIFICATION_SECRET"))
    monkeypatch.setattr(runs, "notify_failure", notification)
    row = run_row("run-1", plan_id="p1", state="PENDING")
    repo.runs[row.id] = row
    monkeypatch.setattr(views, "_open_internal_run", AsyncMock(return_value=(repo.plans["p1"], row)))
    items = AsyncMock(return_value=[])
    freeze = AsyncMock(return_value=[])
    issue = AsyncMock(return_value={"manifest_id": "manifest-1"})
    error = (
        views.CollectionError("RESOURCE_NOT_FOUND", 409, "INTERNAL_SECRET")
        if known_error else RuntimeError("INTERNAL_SECRET")
    )
    {"items": items, "freeze": freeze, "issue": issue}[stage].side_effect = error
    monkeypatch.setattr(views.runs, "OrmRunRepository", lambda: SimpleNamespace(get_items=items))
    monkeypatch.setattr(views.targets, "freeze_targets", freeze)
    monkeypatch.setattr(views.manifest_issue, "issue_action_manifest", issue)
    finish_run = runs.finish_run

    async def finish(actual_run, state, **kwargs):
        await finish_run(actual_run, state, repository=repo, **kwargs)

    monkeypatch.setattr(views.runs, "finish_run", finish)
    response = await _unwrapped(views.manifest_collection_internal)(
        _request({"plan_id": "p1", "collection_run_id": row.id}, DEFAULT_TENANT_ID)
    )
    assert row.state == "FAILED"
    assert await repo.active_run(row.tenant_id, row.topology_id) is None
    expected_code = "RESOURCE_NOT_FOUND" if known_error else "COLLECTION_MANIFEST_FAILED"
    assert row.error_code == expected_code
    assert response.status == (409 if known_error else 503)
    assert orjson.loads(response.body)["data"]["error_code"] == expected_code
    assert b"INTERNAL_SECRET" not in response.body
    assert "INTERNAL_SECRET" not in str(getattr(row, "error_message", ""))
    assert b"INTERNAL_NOTIFICATION_SECRET" not in response.body
    if notification_fails:
        notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_manifest_closed_run_does_not_finish_unresolved_run(monkeypatch):
    monkeypatch.setattr(views, "_open_internal_run", AsyncMock(
        side_effect=views.CollectionError("COLLECTION_RUN_CLOSED", 409)
    ))
    finish = AsyncMock()
    monkeypatch.setattr(views.runs, "finish_run", finish)
    response = await _unwrapped(views.manifest_collection_internal)(
        _request({"plan_id": "p1", "collection_run_id": "run-1"}, DEFAULT_TENANT_ID)
    )
    assert response.status == 409
    finish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["MANIFEST_UPGRADE_INTENT_INVALID", "MANIFEST_PROFILE_STALE"])
async def test_manifest_rejection_preserves_stable_domain_error(monkeypatch, code):
    from services.network_topology.manifest import ManifestError

    row = run_row("run-1", plan_id="p1", state="PENDING")
    monkeypatch.setattr(views, "_open_internal_run", AsyncMock(return_value=(plan("p1"), row)))
    monkeypatch.setattr(views.runs, "OrmRunRepository", lambda: SimpleNamespace(get_items=AsyncMock(return_value=[])))
    monkeypatch.setattr(views.targets, "freeze_targets", AsyncMock(return_value=[]))
    rejection = ManifestError(code)
    rejection.args = ("INTERNAL_SECRET",)
    monkeypatch.setattr(views.manifest_issue, "issue_action_manifest", AsyncMock(side_effect=rejection))
    finish = AsyncMock()
    monkeypatch.setattr(views.runs, "finish_run", finish)

    response = await _unwrapped(views.manifest_collection_internal)(
        _request({"plan_id": "p1", "collection_run_id": row.id}, DEFAULT_TENANT_ID)
    )

    assert response.status == rejection.status
    assert orjson.loads(response.body)["data"]["error_code"] == code
    assert orjson.loads(response.body)["msg"] == rejection.public_message
    finish.assert_awaited_once_with(row, "FAILED", error_code=code)
    assert b"INTERNAL_SECRET" not in response.body


@pytest.mark.asyncio
async def test_default_internal_caller_can_poll_ordinary_tenant_plan(monkeypatch):
    repo = FakeRunRepository(plans=[plan("ordinary", tenant_id="tenant-a")])
    repo.runs["own"] = run_row("own", plan_id="ordinary", tenant_id="tenant-a")
    original = runs.get_run_for_plan
    async def resolve(*args, **kwargs):
        return await original(*args, **kwargs, repository=repo)
    monkeypatch.setattr(runs, "get_run_for_plan", resolve)
    status = AsyncMock(return_value={"run_state": "COLLECTING"})
    monkeypatch.setattr(views.submit, "status", status)
    response = await _unwrapped(views.status_collection_internal)(
        _request({"plan_id": "ordinary", "collection_run_id": "own"}, DEFAULT_TENANT_ID)
    )
    assert response.status == 200
    assert status.await_args.args[0].tenant_id == "tenant-a"
