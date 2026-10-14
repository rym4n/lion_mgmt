"""HTTP adapters for topology collection plans and runs."""

from __future__ import annotations

from datetime import datetime, timezone

import orjson
from sanic import Blueprint
from sanic import json as sanic_json
from sanic.request import Request
from sanic_ext import openapi

from core.maintenance import DEFAULT_TENANT_ID, current_maintenance_mode
from core.response import response_ok
from libs.security.authorization import requires_permission
from models.network_topology import NetworkTopology
from services.network_topology.collection import dispatch, manifest_issue, plans, runs, submit, targets
from services.network_topology.collection.errors import CollectionError


collection_bp = Blueprint("network_topology_collection", url_prefix="/network-topology")
__all__ = ["collection_bp", "CollectionError"]


def _error(error: CollectionError):
    return sanic_json(
        {
            "code": 4000,
            "msg": error.message,
            "data": {"error_code": error.code},
        },
        status=error.status,
        dumps=orjson.dumps,
    )


def _body(request: Request) -> dict:
    value = getattr(request, "json", None) or {}
    if not isinstance(value, dict):
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return value


def _reject_tenant_fields(value):
    if isinstance(value, dict):
        if "tenant" in value or "tenant_id" in value:
            raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
        for child in value.values():
            _reject_tenant_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_tenant_fields(child)


def _session(request: Request) -> dict:
    session = getattr(getattr(request, "ctx", None), "session", None)
    if not isinstance(session, dict):
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    return session


def _tenant_id(request: Request) -> str:
    tenant_id = _session(request).get("tenant_id")
    if not tenant_id:
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    return str(tenant_id)


def _actor_id(request: Request) -> str:
    session = _session(request)
    account = session.get("account_info")
    actor = session.get("sub") or getattr(account, "id", None)
    if not actor:
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    return str(actor)


def _required_string(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return value.strip()


async def _require_topology(request: Request, data: dict) -> tuple[str, str]:
    tenant_id = _tenant_id(request)
    topology_id = _required_string(data, "id")
    topology = await NetworkTopology.filter(
        id=topology_id, tenant_id=tenant_id, deleted=False
    ).first()
    if topology is None:
        raise CollectionError("TOPOLOGY_NOT_FOUND", 404)
    return tenant_id, topology_id


def _page(data: dict) -> tuple[int, int]:
    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return page, page_size


def _public_plan(result):
    if not isinstance(result, dict):
        return result
    result = dict(result)
    plan = result.get("plan")
    if isinstance(plan, dict):
        plan = dict(plan)
        plan.pop("timed_id", None)
        result["plan"] = plan
    return result


def _internal_body(request: Request) -> dict:
    data = _body(request)
    _require_internal_caller(request, data)
    return data


def _require_internal_caller(request: Request, data: dict | None = None) -> None:
    session = _session(request)
    if str(session.get("tenant_id") or "") != DEFAULT_TENANT_ID:
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    _reject_tenant_fields(_body(request) if data is None else data)


async def _open_internal_run(data: dict):
    plan_id = _required_string(data, "plan_id")
    run_id = data.get("collection_run_id", data.get("run_id"))
    if not isinstance(run_id, str) or not run_id.strip():
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return await runs.require_open_run(plan_id, run_id.strip())


@collection_bp.post("/collection-plan/get")
@openapi.definition(summary="获取网络拓扑采集计划")
@requires_permission("network_topology:r")
async def get_collection_plan(request: Request):
    try:
        data = _body(request)
        _reject_tenant_fields(data)
        tenant_id, topology_id = await _require_topology(request, data)
        result = await plans.get_plan(tenant_id, topology_id)
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/collection-plan/save")
@openapi.definition(summary="保存网络拓扑采集计划")
@requires_permission("network_topology:u")
async def save_collection_plan(request: Request):
    try:
        data = _body(request)
        _reject_tenant_fields(data)
        tenant_id, topology_id = await _require_topology(request, data)
        result = await plans.save_plan(
            tenant_id, topology_id, data, account_id=_actor_id(request)
        )
        return response_ok(_public_plan(result))
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/collection-run/trigger")
@openapi.definition(summary="手动触发网络拓扑采集")
@requires_permission("network_topology:u")
async def trigger_collection_run(request: Request):
    try:
        data = _body(request)
        _reject_tenant_fields(data)
        tenant_id, topology_id = await _require_topology(request, data)
        result = await runs.trigger_manual(
            tenant_id, topology_id, account_id=_actor_id(request)
        )
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/collection-run/cancel")
@openapi.definition(summary="取消网络拓扑采集")
@requires_permission("network_topology:u")
async def cancel_collection_run(request: Request):
    try:
        data = _body(request)
        _reject_tenant_fields(data)
        tenant_id, topology_id = await _require_topology(request, data)
        result = await runs.cancel_run(
            tenant_id, topology_id, account_id=_actor_id(request)
        )
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/collection-run/list")
@openapi.definition(summary="获取网络拓扑采集记录")
@requires_permission("network_topology:r")
async def list_collection_runs(request: Request):
    try:
        data = _body(request)
        _reject_tenant_fields(data)
        tenant_id, topology_id = await _require_topology(request, data)
        page, page_size = _page(data)
        result = await runs.list_runs(tenant_id, topology_id, page, page_size)
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/collection-run/detail")
@openapi.definition(summary="获取网络拓扑采集详情")
@requires_permission("network_topology:r")
async def detail_collection_run(request: Request):
    try:
        data = _body(request)
        _reject_tenant_fields(data)
        tenant_id, _topology_id = await _require_topology(request, data)
        run_id = _required_string(data, "run_id")
        result = await runs.run_detail(
            tenant_id,
            run_id,
            maintenance=await current_maintenance_mode(request),
        )
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/internal/collection/start")
@requires_permission("network_topology:sync")
async def start_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        result = await runs.start_scheduled(_required_string(data, "plan_id"))
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/internal/collection/manifest")
@requires_permission("network_topology:sync")
async def manifest_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        plan, run = await _open_internal_run(data)
        try:
            items = await runs.OrmRunRepository().get_items(plan.id)
            frozen = await targets.freeze_targets(plan, items)
            manifest = await manifest_issue.issue_action_manifest(
                plan, run, frozen, now=datetime.now(timezone.utc)
            )
        except CollectionError as error:
            await runs.finish_run(run, "FAILED", error_code=error.code)
            # Target errors may contain resource identities or execution
            # details; only the stable public code crosses this boundary.
            raise CollectionError(error.code, error.status) from None
        except Exception:
            error = CollectionError("COLLECTION_MANIFEST_FAILED", 503)
            await runs.finish_run(run, "FAILED", error_code=error.code)
            raise error from None
        return response_ok(
            {
                "collection_run_id": str(run.id),
                "manifest_id": manifest.get("manifest_id"),
                "device_total": getattr(run, "device_total", len(frozen)),
            }
        )
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/internal/collection/dispatch")
@requires_permission("network_topology:sync")
async def dispatch_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        plan, run = await _open_internal_run(data)
        result = await dispatch.start_background(plan, run)
        return sanic_json({"code": 0, "data": result}, status=202, dumps=orjson.dumps)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/internal/collection/snapshot")
@requires_permission("network_topology:sync")
async def snapshot_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        plan, run = await _open_internal_run(data)
        if getattr(run, "state", None) != "COLLECTED":
            raise CollectionError("COLLECTION_NOT_READY", 409)
        result = await submit.submit_snapshot(plan, run)
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@collection_bp.post("/internal/collection/status")
@requires_permission("network_topology:sync")
async def status_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        plan_id = _required_string(data, "plan_id")
        run_id = data.get("collection_run_id", data.get("run_id"))
        if not isinstance(run_id, str) or not run_id.strip():
            raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
        plan, run = await runs.get_run_for_plan(
            plan_id, run_id.strip(), tenant_id=DEFAULT_TENANT_ID
        )
        return response_ok(await submit.status(plan, run))
    except CollectionError as error:
        return _error(error)

