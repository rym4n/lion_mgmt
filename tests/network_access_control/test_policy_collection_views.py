"""HTTP contracts, tenant boundaries and permissions for policy collection."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from core.maintenance import DEFAULT_TENANT_ID
from libs.security import authorization
from services.action_collection.errors import CollectionError
from services.network_access_control.collection import views
from tests.network_access_control.policy_collection_fakes import FakePolicyRunRepository, plan, run_row


USER_ROUTES = (
    ("get_policy_collection_plan", "/policy-collection/plan/get", "network_access_control:r"),
    ("save_policy_collection_plan", "/policy-collection/plan/save", "network_access_control:u"),
    ("trigger_policy_collection", "/policy-collection/run/trigger", "network_access_control:u"),
    ("cancel_policy_collection", "/policy-collection/run/cancel", "network_access_control:u"),
    ("list_policy_collection_runs", "/policy-collection/run/list", "network_access_control:r"),
    ("detail_policy_collection_run", "/policy-collection/run/detail", "network_access_control:r"),
)
INTERNAL_ROUTES = (
    ("start_policy_collection_internal", "/internal/policy-collection/start", "network_access_control:sync"),
    ("dispatch_policy_collection_internal", "/internal/policy-collection/dispatch", "network_access_control:sync"),
    ("status_policy_collection_internal", "/internal/policy-collection/status", "network_access_control:sync"),
)


def _request(body, tenant_id="t1"):
    return SimpleNamespace(json=body, ctx=SimpleNamespace(session={
        "tenant_id": tenant_id, "sub": "acc-1", "account_info": SimpleNamespace(id="acc-1")}))


def _handler(name):
    fn = getattr(views, name)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _data(response):
    return orjson.loads(response.body)


def test_routes_registered():
    routes = views.policy_collection_bp._future_routes
    assert views.policy_collection_bp.url_prefix == "/network-access-control"
    assert views.policy_collection_bp.name == "network_access_policy_collection"
    for _, path, _ in USER_ROUTES + INTERNAL_ROUTES:
        matching = [r for r in routes if getattr(r, "path", getattr(r, "uri", "")).endswith(path)]
        assert len(matching) == 1, path
        assert set(matching[0].methods) == {"POST"}


def test_blueprint_registered_in_app_factory():
    from services.app_factory import LICENSED_BLUEPRINT_SPECS, _resolve_blueprints
    specs = LICENSED_BLUEPRINT_SPECS["network_access_control_bp"]
    assert ("services.network_access_control.collection.views", "policy_collection_bp") in specs
    assert views.policy_collection_bp in _resolve_blueprints(specs)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path,permission", USER_ROUTES + INTERNAL_ROUTES)
async def test_routes_enforce_permission_before_handling(monkeypatch, name, path, permission):
    class PermissionDenied(Exception):
        pass

    async def deny(request, *required, **kwargs):
        assert required == (permission,)
        raise PermissionDenied

    monkeypatch.setattr(authorization, "check_permissions", deny)
    with pytest.raises(PermissionDenied):
        await getattr(views, name)(_request({}))


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path,permission", USER_ROUTES + INTERNAL_ROUTES)
@pytest.mark.parametrize("body", [{"tenant_id": "x"}, {"tenant": "x"}, {"items": [{"action_input": {"tenant_id": "x"}}]}])
async def test_all_routes_reject_tenant_fields(name, path, permission, body):
    response = await _handler(name)(_request(body, DEFAULT_TENANT_ID))
    assert response.status == 403
    assert _data(response)["data"]["error_code"] == "COLLECTION_CALLER_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path,permission", USER_ROUTES + INTERNAL_ROUTES)
@pytest.mark.parametrize("body", [[], False, 0, "value", ["value"]])
async def test_all_routes_reject_non_object_bodies(name, path, permission, body):
    response = await _handler(name)(_request(body))
    assert response.status == 400
    assert _data(response)["data"]["error_code"] == "COLLECTION_INVALID_REQUEST"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path,permission", USER_ROUTES + INTERNAL_ROUTES)
async def test_all_routes_require_session_tenant(name, path, permission):
    response = await _handler(name)(_request({"collection_run_id": "r1", "plan_id": "p1"}, None))
    assert response.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path,permission", INTERNAL_ROUTES)
async def test_internal_requires_default_tenant(name, path, permission):
    response = await _handler(name)(_request({"plan_id": "p1", "collection_run_id": "r1"}))
    assert response.status == 403
    assert _data(response)["data"]["error_code"] == "COLLECTION_CALLER_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,path,permission", INTERNAL_ROUTES)
async def test_internal_routes_require_session_authentication(monkeypatch, name, path, permission):
    from core.middlewares import security

    class AuthenticationRequired(Exception):
        pass

    async def authenticate(request):
        raise AuthenticationRequired

    monkeypatch.setattr(security, "authenticate", authenticate)
    request = SimpleNamespace(path="/network-access-control" + path, method="POST", headers={})
    with pytest.raises(AuthenticationRequired):
        await security.SecurityMiddleware().before_request(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,service,method,body,args,kwargs", [
    ("get_policy_collection_plan", "plans", "get_plan", {}, ("t1",), {}),
    ("save_policy_collection_plan", "plans", "save_plan", {"enabled": False}, ("t1", {"enabled": False}), {"account_id": "acc-1"}),
    ("trigger_policy_collection", "runs", "trigger_manual", {}, ("t1",), {"account_id": "acc-1"}),
    ("cancel_policy_collection", "runs", "cancel_run", {}, ("t1",), {"account_id": "acc-1"}),
    ("list_policy_collection_runs", "runs", "list_runs", {}, ("t1", 1, 20), {}),
    ("list_policy_collection_runs", "runs", "list_runs", {"page": 2, "page_size": 100}, ("t1", 2, 100), {}),
    ("start_policy_collection_internal", "runs", "start_scheduled", {"plan_id": " p1 "}, ("p1",), {}),
])
async def test_service_scope_arguments_and_success_dto(monkeypatch, name, service, method, body, args, kwargs):
    called = AsyncMock(return_value={"result": "ok"})
    monkeypatch.setattr(getattr(views, service), method, called)
    tenant = DEFAULT_TENANT_ID if name.endswith("internal") else "t1"
    response = await _handler(name)(_request(body, tenant))
    assert response.status == 200
    assert _data(response) == {"code": 0, "data": {"result": "ok"}}
    called.assert_awaited_once_with(*args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"page": 0}, {"page": True}, {"page": "1"}, {"page_size": 0}, {"page_size": 101}, {"page_size": False}])
async def test_list_validates_pagination(body):
    response = await _handler("list_policy_collection_runs")(_request(body))
    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("name,body", [
    ("detail_policy_collection_run", {}),
    ("detail_policy_collection_run", {"collection_run_id": " "}),
    ("start_policy_collection_internal", {"plan_id": 1}),
    ("dispatch_policy_collection_internal", {"plan_id": "p1"}),
    ("status_policy_collection_internal", {"collection_run_id": "r1"}),
])
async def test_required_identifiers(name, body):
    response = await _handler(name)(_request(body, DEFAULT_TENANT_ID))
    assert response.status == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("maintenance", [False, True])
async def test_detail_passes_server_maintenance_scope(monkeypatch, maintenance):
    mode = AsyncMock(return_value=maintenance)
    detail = AsyncMock(return_value={"id": "r1", "agent": None})
    monkeypatch.setattr(views, "current_maintenance_mode", mode)
    monkeypatch.setattr(views.runs, "run_detail", detail)
    request = _request({"collection_run_id": " r1 "})
    response = await _handler("detail_policy_collection_run")(request)
    assert _data(response)["data"] == {"id": "r1", "agent": None}
    mode.assert_awaited_once_with(request)
    detail.assert_awaited_once_with("t1", "r1", maintenance=maintenance)


@pytest.mark.asyncio
async def test_internal_dispatch_returns_202(monkeypatch):
    owner, row = plan(tenant_id="t2"), run_row("r1", tenant_id="t2")
    repo = FakePolicyRunRepository(plans=[owner])
    repo.runs[row.id] = row
    start = AsyncMock(return_value={"collection_run_id": "r1", "run_state": "COLLECTING"})
    monkeypatch.setattr(views.runs, "OrmPolicyRunRepository", lambda: repo)
    monkeypatch.setattr(views.pipeline, "start_pipeline", start)
    response = await _handler("dispatch_policy_collection_internal")(
        _request({"plan_id": "p1", "collection_run_id": "r1"}, DEFAULT_TENANT_ID))
    assert response.status == 202
    assert _data(response) == {"code": 0, "data": {"collection_run_id": "r1", "run_state": "COLLECTING"}}
    start.assert_awaited_once_with(owner, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"])
async def test_terminal_duplicate_dispatch_returns_current_state_without_pipeline(monkeypatch, state):
    repo = FakePolicyRunRepository(plans=[plan(tenant_id="ordinary-tenant")])
    row = run_row("r1", tenant_id="ordinary-tenant", state=state,
                  started_at=datetime.now(timezone.utc) - timedelta(days=1))
    repo.runs[row.id] = row
    monkeypatch.setattr(views.runs, "OrmPolicyRunRepository", lambda: repo)
    start = AsyncMock()
    monkeypatch.setattr(views.pipeline, "start_pipeline", start)
    for _ in range(2):
        response = await _handler("dispatch_policy_collection_internal")(
            _request({"plan_id": "p1", "collection_run_id": "r1"}, DEFAULT_TENANT_ID))
        assert response.status == 202
        assert _data(response) == {"code": 0, "data": {"collection_run_id": "r1", "run_state": state}}
    assert row.state == state and row.error_code is None
    start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "COLLECTING", "NORMALIZING", "APPLYING"])
async def test_active_timed_out_dispatch_closes_without_pipeline(monkeypatch, state):
    repo = FakePolicyRunRepository(plans=[plan(run_timeout_seconds=600)])
    row = run_row("r1", state=state, started_at=datetime.now(timezone.utc) - timedelta(seconds=601))
    repo.runs[row.id] = row
    monkeypatch.setattr(views.runs, "OrmPolicyRunRepository", lambda: repo)
    monkeypatch.setattr(views.runs, "notify_after_commit", AsyncMock())
    start = AsyncMock()
    monkeypatch.setattr(views.pipeline, "start_pipeline", start)
    response = await _handler("dispatch_policy_collection_internal")(
        _request({"plan_id": "p1", "collection_run_id": "r1"}, DEFAULT_TENANT_ID))
    assert response.status == 409
    assert _data(response)["data"]["error_code"] == "COLLECTION_RUN_CLOSED"
    assert row.state == "FAILED" and row.error_code == "COLLECTION_RUN_TIMEOUT"
    start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing_plan", "missing_run", "cross_plan", "cross_tenant"])
async def test_dispatch_rejects_unverified_run_without_pipeline(monkeypatch, invalid):
    repo = FakePolicyRunRepository(plans=[] if invalid == "missing_plan" else [plan()])
    if invalid != "missing_run":
        repo.runs["r1"] = run_row("r1", state="SUCCEEDED",
                                  plan_id="p2" if invalid == "cross_plan" else "p1",
                                  tenant_id="t2" if invalid == "cross_tenant" else "t1")
    monkeypatch.setattr(views.runs, "OrmPolicyRunRepository", lambda: repo)
    start = AsyncMock()
    monkeypatch.setattr(views.pipeline, "start_pipeline", start)
    response = await _handler("dispatch_policy_collection_internal")(
        _request({"plan_id": "p1", "collection_run_id": "r1"}, DEFAULT_TENANT_ID))
    assert response.status == 404
    assert _data(response)["data"]["error_code"] == (
        "PLAN_NOT_FOUND" if invalid == "missing_plan" else "COLLECTION_RUN_NOT_FOUND")
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_status_resolves_plan_tenant_and_reads_terminal_run(monkeypatch):
    repo = FakePolicyRunRepository(plans=[plan(tenant_id="ordinary-tenant")])
    repo.runs["r1"] = run_row(id="r1", tenant_id="ordinary-tenant", state="PARTIAL", device_total=2, device_succeeded=1)
    monkeypatch.setattr(views.runs, "OrmPolicyRunRepository", lambda: repo)
    response = await _handler("status_policy_collection_internal")(
        _request({"plan_id": "p1", "collection_run_id": "r1"}, DEFAULT_TENANT_ID))
    assert response.status == 200
    assert _data(response)["data"] == {
        "terminal": True, "run_state": "PARTIAL", "device_total": 2, "device_succeeded": 1,
        "compare_run_id": None, "error_code": None, "error_message": None, "devices": []}
    repo.runs["r1"].tenant_id = "wrong-tenant"
    denied = await _handler("status_policy_collection_internal")(
        _request({"plan_id": "p1", "collection_run_id": "r1"}, DEFAULT_TENANT_ID))
    assert denied.status == 404
    assert _data(denied)["data"]["error_code"] == "COLLECTION_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_error_mapping(monkeypatch):
    monkeypatch.setattr(views.runs, "trigger_manual", AsyncMock(side_effect=CollectionError("PLAN_EMPTY", 400)))
    response = await _handler("trigger_policy_collection")(_request({}))
    assert response.status == 400
    assert _data(response) == {"code": 4000, "msg": "PLAN_EMPTY", "data": {"error_code": "PLAN_EMPTY"}}


@pytest.mark.asyncio
async def test_error_message_and_status_preserved(monkeypatch):
    monkeypatch.setattr(views.runs, "start_scheduled", AsyncMock(
        side_effect=CollectionError("COLLECTION_RUN_ACTIVE", 409, "采集中")))
    response = await _handler("start_policy_collection_internal")(_request({"plan_id": "p1"}, DEFAULT_TENANT_ID))
    assert response.status == 409
    assert _data(response) == {"code": 4000, "msg": "采集中", "data": {"error_code": "COLLECTION_RUN_ACTIVE"}}
