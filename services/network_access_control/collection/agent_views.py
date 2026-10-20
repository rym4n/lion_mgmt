"""Fail-closed internal endpoints called by lion_agent's policy normalizer."""
from __future__ import annotations

import orjson
from sanic import Blueprint
from sanic import json as sanic_json

from core.response import response_ok
from models.network_access_control import NetworkAccessPolicyAgentJob
from services.action_collection.agent_tokens import authenticate_job
from services.action_collection.errors import CollectionError
from services.network_access_control.collection import agent_jobs
from services.network_access_control.collection.limits import max_rules_per_device
from services.network_access_control.collection.validation import document_schema

policy_agent_internal_bp = Blueprint("network_access_policy_agent_internal",
                                     url_prefix="/network-access-control/internal")


def _error(exc: CollectionError):
    return sanic_json({"code": 4000, "msg": exc.message, "data": {"error_code": exc.code}},
                      status=exc.status, dumps=orjson.dumps)


def _body(request) -> dict:
    value = getattr(request, "json", None)
    if not isinstance(value, dict):
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    return value


async def _job(request, body):
    return await authenticate_job(request.headers, body, job_model=NetworkAccessPolicyAgentJob)


@policy_agent_internal_bp.post("/policy-agent-jobs/input")
async def policy_agent_job_input(request):
    try:
        job = await _job(request, _body(request))
        raw_outputs, targets = await agent_jobs.load_context(job)
        return response_ok({
            "resources": agent_jobs.build_agent_input(raw_outputs, targets, list(job.resource_ids or [])),
            "document_schema": document_schema(),
            "max_rules_per_device": await max_rules_per_device(),
        })
    except CollectionError as exc:
        return _error(exc)
