"""Fail-closed internal HTTP endpoints used by lion_agent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import orjson
from sanic import Blueprint
from sanic import json as sanic_json

from core.response import response_ok
from models.network_topology_v2 import TopologyAgentJob, TopologyAnalysisRun
from services.network_topology.agent_jobs import (
    AGENT_TIMEOUT_SECONDS, authenticate, build_agent_input, convert_documents, schema_bundle,
)
from services.network_topology.analysis_pipeline import OrmAnalysisPipelineRepository
from services.network_topology.collection.errors import CollectionError
from services.network_topology.payload_store import PayloadStore

agent_internal_bp = Blueprint("network_topology_agent_internal", url_prefix="/network-topology/internal")


def _error(exc: CollectionError):
    return sanic_json({"code": 4000, "msg": exc.message, "data": {"error_code": exc.code}}, status=exc.status, dumps=orjson.dumps)


def _body(request):
    value = getattr(request, "json", None)
    if not isinstance(value, dict):
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    return value


async def _load_context(job):
    run = await TopologyAnalysisRun.filter(
        id=job.analysis_run_id, tenant_id=job.tenant_id,
        topology_row_id=job.topology_id, deleted=False,
    ).first()
    if run is None:
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    collection = await OrmAnalysisPipelineRepository().load_collection(run, PayloadStore())
    return run, collection


@agent_internal_bp.post("/agent-jobs/input")
async def agent_job_input(request):
    try:
        job = await authenticate(request.headers, _body(request))
        run, collection = await _load_context(job)
        resources = [r for r in build_agent_input(collection, run.agent_observation_ids or [])
                     if r["resource_id"] in job.resource_ids]
        return response_ok({
            "resources": resources, "schemas": schema_bundle(),
            "candidate_schema_id": "https://schemas.aioe.local/aioe.network-topology.llm-fact-candidates/v1",
        })
    except CollectionError as exc:
        return _error(exc)


@agent_internal_bp.post("/agent-facts/submit")
async def agent_facts_submit(request):
    try:
        body = _body(request)
        job = await authenticate(request.headers, body)
        run, collection = await _load_context(job)
        documents = body.get("documents")
        _, errors = convert_documents(collection, run, documents)
        if isinstance(documents, list):
            received = [item.get("resource_id") if isinstance(item, dict) else None for item in documents]
            if set(r for r in received if isinstance(r, str)) != set(job.resource_ids):
                errors.append({"resource_id": None, "message": "submission must cover exactly this job's resources"})
        if errors:
            return sanic_json({"code": 4000, "msg": "候选校验失败", "data": {"error_code": "AGENT_SUBMISSION_INVALID", "errors": errors}}, status=422, dumps=orjson.dumps)
        now = datetime.now(timezone.utc)
        changed = await TopologyAgentJob.filter(
            id=job.id, tenant_id=job.tenant_id, deleted=False,
            analysis_run_id=job.analysis_run_id, state="RUNNING",
            job_token_hash=job.job_token_hash,
            started_at__gt=now - timedelta(seconds=AGENT_TIMEOUT_SECONDS),
        ).update(documents=orjson.dumps(documents).decode(), state="SUBMITTED", utime=now)
        if changed != 1:
            raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
        return response_ok({"accepted": True})
    except CollectionError as exc:
        return _error(exc)
