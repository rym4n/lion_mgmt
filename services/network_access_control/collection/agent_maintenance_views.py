"""Cross-tenant policy normalizer diagnostics (maintenance mode only)."""
from __future__ import annotations

import re
from datetime import datetime

import httpx
import orjson
from sanic import Blueprint

import settings
from core.error_code import ErrorCode
from core.maintenance import current_maintenance_mode
from core.response import response_ok
from libs.error_code.exception import CustomError
from models.network_access_control import NetworkAccessPolicyAgentJob
from services.action_collection.errors import CollectionError
from services.action_collection.internal_auth import reject_tenant_fields
from services.network_topology.collection.views import _actor_id, _body, _error, _page, _required_string

policy_agent_maintenance_bp = Blueprint(
    "network_access_policy_agent_maintenance",
    url_prefix="/network-access-control/maintenance",
)

JOB_FIELDS = (
    "id", "tenant_id", "collection_run_id", "state", "attempt", "resource_ids", "cache_hits",
    "cache_misses", "iterations", "token_usage", "profile_version", "error_code", "error_message",
    "started_at", "finished_at", "ctime",
)
JOB_STATES = frozenset({"QUEUED", "RUNNING", "SUBMITTED", "SUCCEEDED", "FAILED"})
CLEAR_PATH = "/internal/v1/builtin/policy-normalizer/scripts/clear"


def _serialize(values):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()}


def _maintenance_body(request, *, allow_tenant_id=False):
    data = _body(request)
    checked = {key: value for key, value in data.items() if not (allow_tenant_id and key == "tenant_id")}
    reject_tenant_fields(checked)
    if allow_tenant_id and "tenant_id" in data:
        _required_string(data, "tenant_id")
    return data


async def _guard(request):
    if not await current_maintenance_mode(request):
        raise CustomError(ErrorCode.FORBIDDEN)


def _loads(value):
    return orjson.loads(value) if value else []


@policy_agent_maintenance_bp.post("/policy-agent-jobs/list")
async def list_policy_agent_jobs(request):
    await _guard(request)
    try:
        data = _maintenance_body(request, allow_tenant_id=True)
        page, page_size = _page(data)
        filters = {"deleted": False}
        if "tenant_id" in data:
            filters["tenant_id"] = _required_string(data, "tenant_id")
        if "state" in data:
            state = _required_string(data, "state")
            if state not in JOB_STATES:
                raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
            filters["state"] = state
        query = NetworkAccessPolicyAgentJob.filter(**filters)
        total = await query.count()
        rows = await query.order_by("-ctime", "-id").offset((page - 1) * page_size).limit(page_size).values(*JOB_FIELDS)
        return response_ok({"list": [_serialize(row) for row in rows], "total": total, "page": page, "page_size": page_size})
    except CollectionError as error:
        return _error(error)


@policy_agent_maintenance_bp.post("/policy-agent-jobs/detail")
async def detail_policy_agent_job(request):
    await _guard(request)
    try:
        data = _maintenance_body(request)
        job = await NetworkAccessPolicyAgentJob.filter(
            id=_required_string(data, "job_id"), deleted=False
        ).first()
        if job is None:
            raise CollectionError("AGENT_JOB_NOT_FOUND", 404)
        result = _serialize({name: getattr(job, name) for name in JOB_FIELDS})
        result["transcript"] = _loads(job.transcript)
        result["failed"] = _loads(job.failed)
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@policy_agent_maintenance_bp.post("/policy-normalizer/scripts/clear")
async def clear_policy_normalizer_scripts(request):
    await _guard(request)
    try:
        tenant_id = _required_string(_maintenance_body(request, allow_tenant_id=True), "tenant_id")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", tenant_id) or tenant_id in {".", ".."}:
            raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                settings.LION_AGENT_ENDPOINT.rstrip("/") + CLEAR_PATH,
                headers={"X-Internal-Token": settings.LION_AGENT_INTERNAL_TOKEN},
                json={"tenant_id": tenant_id},
            )
        if response.status_code != 200:
            raise CollectionError("AGENT_UNAVAILABLE", 503)
        body = response.json()
        if not isinstance(body, dict) or type(body.get("removed")) is not int or body["removed"] < 0:
            raise CollectionError("AGENT_UNAVAILABLE", 503)
        return response_ok({"removed": body["removed"]})
    except (httpx.RequestError, ValueError):
        return _error(CollectionError("AGENT_UNAVAILABLE", 503))
    except CollectionError as error:
        return _error(error)
