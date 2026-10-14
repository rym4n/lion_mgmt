"""Cross-tenant Agent diagnostics and explicit maintenance operations."""
from __future__ import annotations

import re
from datetime import datetime

import httpx
import orjson
from sanic import Blueprint
from sanic import json as sanic_json

import settings
from core.error_code import ErrorCode
from core.maintenance import current_maintenance_mode
from core.response import response_ok
from libs.error_code.exception import CustomError
from models.network_topology import NetworkTopology
from models.network_topology_v2 import TopologyAgentJob, TopologyAnalysisRun, TopologyCollectionSnapshot
from services.network_topology import analysis_runs
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.views import _actor_id, _body, _error, _page, _required_string

agent_maintenance_bp = Blueprint(
    "network_topology_agent_maintenance", url_prefix="/network-topology/maintenance"
)

# Explicit projection keeps encrypted payloads and authentication hashes out of lists.
JOB_FIELDS = (
    "id", "tenant_id", "analysis_run_id", "topology_id", "collection_run_id", "state", "attempt",
    "resource_ids", "cache_hits", "cache_misses", "iterations", "token_usage", "profile_version",
    "error_code", "error_message", "started_at", "finished_at", "ctime",
)
JOB_STATES = frozenset({"QUEUED", "RUNNING", "SUBMITTED", "SUCCEEDED", "FAILED"})


def _serialize(values):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()}


async def _job(data):
    job = await TopologyAgentJob.filter(id=_required_string(data, "job_id"), deleted=False).first()
    if job is None:
        raise CollectionError("AGENT_JOB_NOT_FOUND", 404)
    return job


@agent_maintenance_bp.post("/agent-jobs/list")
async def list_agent_jobs(request):
    if not await current_maintenance_mode(request):
        raise CustomError(ErrorCode.FORBIDDEN)
    try:
        data = _body(request)
        page, page_size = _page(data)
        filters = {"deleted": False}
        if "tenant_id" in data:
            filters["tenant_id"] = _required_string(data, "tenant_id")
        if "state" in data:
            state = _required_string(data, "state")
            if state not in JOB_STATES:
                raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
            filters["state"] = state
        query = TopologyAgentJob.filter(**filters)
        total = await query.count()
        rows = await query.order_by("-ctime", "-id").offset((page - 1) * page_size).limit(page_size).values(*JOB_FIELDS)
        return response_ok({"list": [_serialize(row) for row in rows], "total": total,
                            "page": page, "page_size": page_size})
    except CollectionError as error:
        return _error(error)


@agent_maintenance_bp.post("/agent-jobs/detail")
async def detail_agent_job(request):
    if not await current_maintenance_mode(request):
        raise CustomError(ErrorCode.FORBIDDEN)
    try:
        job = await _job(_body(request))
        result = _serialize({name: getattr(job, name) for name in JOB_FIELDS})
        # EncryptedTextField decrypts these strings at the ORM boundary.
        result["transcript"] = orjson.loads(job.transcript) if job.transcript else []
        result["documents"] = orjson.loads(job.documents) if job.documents else []
        return response_ok(result)
    except CollectionError as error:
        return _error(error)


@agent_maintenance_bp.post("/agent-jobs/rerun")
async def rerun_agent_job(request):
    if not await current_maintenance_mode(request):
        raise CustomError(ErrorCode.FORBIDDEN)
    try:
        job = await _job(_body(request))
        run = await TopologyAnalysisRun.filter(
            id=job.analysis_run_id, tenant_id=job.tenant_id, deleted=False
        ).first()
        if run is None:
            raise CollectionError("AGENT_ANALYSIS_RUN_NOT_FOUND", 404)
        snapshot = await TopologyCollectionSnapshot.filter(
            id=run.snapshot_row_id, tenant_id=run.tenant_id,
            topology_row_id=run.topology_row_id, deleted=False
        ).first()
        if snapshot is None:
            raise CollectionError("AGENT_SNAPSHOT_NOT_FOUND", 404)
        topology = await NetworkTopology.filter(
            id=run.topology_row_id, tenant_id=run.tenant_id, deleted=False
        ).first()
        if topology is None:
            raise CollectionError("TOPOLOGY_NOT_FOUND", 404)
        result = await analysis_runs.reanalyze_snapshot(
            run.tenant_id, snapshot.snapshot_id, topology_id=str(topology.id),
            target_profile_revision=topology.topology_profile_revision,
            # Analyze the original snapshot with the current profile; retain
            # results on this run without replacing the published graph.
            publish_as_current=False,
            expected_current_graph_revision=topology.current_graph_revision or 0,
            expected_publication_revision=topology.publication_revision or 0,
            actor=_actor_id(request),
        )
        return sanic_json({"code": 0, "data": {**result, "publish_as_current": False}},
                          status=202, dumps=orjson.dumps)
    except analysis_runs.AnalysisRunError as error:
        return _error(CollectionError(error.code, error.status))
    except CollectionError as error:
        return _error(error)


@agent_maintenance_bp.post("/agent-scripts/clear")
async def clear_agent_scripts(request):
    if not await current_maintenance_mode(request):
        raise CustomError(ErrorCode.FORBIDDEN)
    try:
        tenant_id = _required_string(_body(request), "tenant_id")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", tenant_id):
            raise CollectionError("COLLECTION_INVALID_REQUEST", 400)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                settings.LION_AGENT_ENDPOINT.rstrip("/") + "/internal/v1/builtin/topology-normalizer/scripts/clear",
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
