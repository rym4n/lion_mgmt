"""HTTP adapters for network policy collection (user and internal routes)."""
from __future__ import annotations

import orjson
from sanic import Blueprint
from sanic import json as sanic_json
from sanic.request import Request
from sanic_ext import openapi

from core.maintenance import current_maintenance_mode
from core.response import response_ok
from libs.security.authorization import requires_permission
from services.action_collection.errors import CollectionError
from services.action_collection.internal_auth import (
    actor_id, reject_tenant_fields, require_internal_caller, session_tenant_id,
)
from services.network_access_control.collection import pipeline, plans, runs

policy_collection_bp = Blueprint("network_access_policy_collection", url_prefix="/network-access-control")
__all__ = ["policy_collection_bp"]


def _error(error: CollectionError):
    return sanic_json({"code": 4000, "msg": error.message, "data": {"error_code": error.code}},
                      status=error.status, dumps=orjson.dumps)


def _body(request: Request) -> dict:
    value = getattr(request, "json", None)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return value


def _user_body(request: Request) -> dict:
    data = _body(request)
    reject_tenant_fields(data)
    return data


def _required(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return value.strip()


def _page(data: dict) -> tuple[int, int]:
    page, size = data.get("page", 1), data.get("page_size", 20)
    if type(page) is not int or page < 1 or type(size) is not int or not 1 <= size <= 100:
        raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
    return page, size


def _internal_body(request: Request) -> dict:
    data = _body(request)
    require_internal_caller(request, data)
    return data


@policy_collection_bp.post("/policy-collection/plan/get")
@openapi.definition(summary="获取网络策略采集计划")
@requires_permission("network_access_control:r")
async def get_policy_collection_plan(request: Request):
    try:
        _user_body(request)
        return response_ok(await plans.get_plan(session_tenant_id(request)))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/policy-collection/plan/save")
@openapi.definition(summary="保存网络策略采集计划")
@requires_permission("network_access_control:u")
async def save_policy_collection_plan(request: Request):
    try:
        data = _user_body(request)
        return response_ok(await plans.save_plan(session_tenant_id(request), data, account_id=actor_id(request)))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/policy-collection/run/trigger")
@openapi.definition(summary="立即采集网络策略")
@requires_permission("network_access_control:u")
async def trigger_policy_collection(request: Request):
    try:
        _user_body(request)
        return response_ok(await runs.trigger_manual(session_tenant_id(request), account_id=actor_id(request)))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/policy-collection/run/cancel")
@openapi.definition(summary="取消网络策略采集")
@requires_permission("network_access_control:u")
async def cancel_policy_collection(request: Request):
    try:
        _user_body(request)
        return response_ok(await runs.cancel_run(session_tenant_id(request), account_id=actor_id(request)))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/policy-collection/run/list")
@openapi.definition(summary="网络策略采集记录")
@requires_permission("network_access_control:r")
async def list_policy_collection_runs(request: Request):
    try:
        page, size = _page(_user_body(request))
        return response_ok(await runs.list_runs(session_tenant_id(request), page, size))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/policy-collection/run/detail")
@openapi.definition(summary="网络策略采集详情")
@requires_permission("network_access_control:r")
async def detail_policy_collection_run(request: Request):
    try:
        data = _user_body(request)
        return response_ok(await runs.run_detail(
            session_tenant_id(request), _required(data, "collection_run_id"),
            maintenance=await current_maintenance_mode(request)))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/internal/policy-collection/start")
@requires_permission("network_access_control:sync")
async def start_policy_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        return response_ok(await runs.start_scheduled(_required(data, "plan_id")))
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/internal/policy-collection/dispatch")
@requires_permission("network_access_control:sync")
async def dispatch_policy_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        plan, run = await runs.require_open_run(_required(data, "plan_id"), _required(data, "collection_run_id"))
        result = await pipeline.start_pipeline(plan, run)
        return sanic_json({"code": 0, "data": result}, status=202, dumps=orjson.dumps)
    except CollectionError as error:
        return _error(error)


@policy_collection_bp.post("/internal/policy-collection/status")
@requires_permission("network_access_control:sync")
async def status_policy_collection_internal(request: Request):
    try:
        data = _internal_body(request)
        _plan, run = await runs.get_run_for_plan(_required(data, "plan_id"), _required(data, "collection_run_id"))
        return response_ok(runs.status_projection(run))
    except CollectionError as error:
        return _error(error)
