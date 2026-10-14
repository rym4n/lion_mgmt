"""Durable lion_agent normalization stage; management never runs agent code."""
from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timezone

import httpx
import orjson

import settings
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.network_topology_v2 import TopologyAgentJob, TopologyCollectionRun
from services.llm_config.resolver import resolve_llm
from services.network_topology.agent_jobs import AGENT_TIMEOUT_SECONDS, build_agent_input, convert_documents
from services.network_topology.analysis_pipeline import OrmAnalysisPipelineRepository
from services.network_topology.facts.candidate_conversion import ConversionResult
from services.network_topology.payload_store import PayloadStore
from services.network_topology.redaction import scrub_action_output
from services.network_topology.worker import PipelineFailure

AGENT_ERRORS = frozenset({
    "AGENT_TIMEOUT", "AGENT_SUBMIT_REJECTED", "AGENT_VALIDATION_EXHAUSTED", "AGENT_UNAVAILABLE",
    "AGENT_INPUT_UNAVAILABLE",
})
SUMMARY_FIELDS = (
    "state", "profile_version", "cache_hits", "cache_misses", "iterations",
    "token_usage", "transcript", "error_code", "error_message", "finished_at",
)


class NormalizerClient:
    def __init__(self, endpoint=None, token=None):
        self.endpoint = settings.LION_AGENT_ENDPOINT if endpoint is None else endpoint
        self.token = settings.LION_AGENT_INTERNAL_TOKEN if token is None else token

    async def execute(self, payload) -> tuple[int, dict]:
        async with httpx.AsyncClient(timeout=payload["timeout_seconds"] + 60) as client:
            response = await client.post(
                self.endpoint.rstrip("/") + "/internal/v1/builtin/topology-normalizer/execute",
                headers={"X-Internal-Token": self.token}, json=payload,
            )
            return response.status_code, response.json()


class OrmAgentJobRepository:
    async def jobs_for(self, analysis_run_id, *, tenant_id):
        return await TopologyAgentJob.filter(
            analysis_run_id=analysis_run_id, tenant_id=tenant_id, deleted=False,
        ).order_by("attempt")

    async def create_job(self, **values):
        return await TopologyAgentJob.create(**values)

    async def get_job(self, job_id, *, tenant_id):
        return await TopologyAgentJob.filter(id=job_id, tenant_id=tenant_id, deleted=False).first()

    async def save_job(self, job):
        # Do not overwrite documents submitted by the independent HTTP handler.
        await job.save(update_fields=SUMMARY_FIELDS)

    async def collection_run_id_for(self, analysis_run_id, *, tenant_id):
        row = await TopologyCollectionRun.filter(
            analysis_run_id=analysis_run_id, tenant_id=tenant_id, deleted=False,
        ).first()
        return str(row.id) if row else None

    async def load_collection(self, run):
        return await OrmAnalysisPipelineRepository().load_collection(run, PayloadStore())


class AgentStage:
    def __init__(self, *, client=None, repository=None, llm_resolver=resolve_llm):
        self.client = client if client is not None else NormalizerClient()
        self.repository = repository if repository is not None else OrmAgentJobRepository()
        self.llm_resolver = llm_resolver

    async def run(self, run, collection, lease) -> tuple:
        if not getattr(run, "agent_observation_ids", None):
            return ()
        jobs = await self.repository.jobs_for(str(run.id), tenant_id=run.tenant_id)
        if any(job.state == "SUCCEEDED" for job in jobs):
            return ()
        try:
            resolved = await self.llm_resolver(run.tenant_id, purpose="chat")
        except CustomError as exc:
            if exc.enum == ErrorCode.LLM_CONFIG_NOT_CONFIGURED:
                raise PipelineFailure("AGENT_MODEL_NOT_CONFIGURED") from None
            raise
        token = secrets.token_urlsafe(32)
        job = await self.repository.create_job(
            tenant_id=run.tenant_id, analysis_run_id=str(run.id), topology_id=str(run.topology_row_id),
            collection_run_id=await self.repository.collection_run_id_for(str(run.id), tenant_id=run.tenant_id),
            attempt=len(jobs) + 1, state="RUNNING",
            resource_ids=[item["resource_id"] for item in build_agent_input(collection, run.agent_observation_ids)],
            job_token_hash=hashlib.sha256(token.encode()).hexdigest(), started_at=datetime.now(timezone.utc),
        )
        payload = {
            "job_id": str(job.id), "tenant_id": run.tenant_id, "agent_job_token": token,
            "mgmt_base_url": settings.MGMT_INTERNAL_BASE_URL,
            "llm": resolved.to_payload(), "timeout_seconds": AGENT_TIMEOUT_SECONDS,
        }
        sensitive = (token, payload["llm"].get("api_key", ""), settings.LION_AGENT_INTERNAL_TOKEN)
        transcript = []
        try:
            status, body = await self.client.execute(payload)
            if not isinstance(body, dict):
                raise PipelineFailure("AGENT_UNAVAILABLE")
            if status == 422:
                detail = body.get("detail")
                if not isinstance(detail, dict):
                    raise PipelineFailure("AGENT_UNAVAILABLE")
                transcript = detail.get("transcript", [])
                code = detail.get("code")
                raise PipelineFailure(code if isinstance(code, str) and code in AGENT_ERRORS else "AGENT_UNAVAILABLE")
            if status != 200:
                raise PipelineFailure("AGENT_UNAVAILABLE")
            # Submission occurs through another request/ORM instance: reload it.
            submitted = await self.repository.get_job(str(job.id), tenant_id=run.tenant_id)
            if submitted is None or submitted.state != "SUBMITTED":
                raise PipelineFailure("AGENT_SUBMIT_REJECTED")
            job = submitted
            job.state = "SUCCEEDED"
            for field in ("cache_hits", "cache_misses", "iterations"):
                value = body.get(field, 0)
                if type(value) is not int or value < 0:
                    raise PipelineFailure("AGENT_UNAVAILABLE")
                setattr(job, field, value)
            profile = body.get("profile_version")
            if profile is not None and (not isinstance(profile, str) or len(profile) > 255):
                raise PipelineFailure("AGENT_UNAVAILABLE")
            job.profile_version = profile
            job.token_usage = orjson.loads(_redacted_json(body.get("usage", {}), sensitive))
            transcript = body.get("transcript", [])
        except httpx.TimeoutException:
            await self._fail(job, "AGENT_TIMEOUT", transcript, sensitive)
            raise PipelineFailure("AGENT_TIMEOUT") from None
        except (httpx.RequestError, ValueError, TypeError):
            await self._fail(job, "AGENT_UNAVAILABLE", transcript, sensitive)
            raise PipelineFailure("AGENT_UNAVAILABLE") from None
        except PipelineFailure as exc:
            await self._fail(job, exc.error_code, transcript, sensitive)
            raise
        except asyncio.CancelledError:
            await self._fail(job, "AGENT_UNAVAILABLE", transcript, sensitive)
            raise
        job.transcript = _redacted_json(transcript, sensitive)
        job.finished_at = datetime.now(timezone.utc)
        await self.repository.save_job(job)
        return ()

    async def _fail(self, job, code, transcript, sensitive):
        job.state = "FAILED"
        job.error_code = code
        job.error_message = None
        job.transcript = _redacted_json(transcript, sensitive)
        job.finished_at = datetime.now(timezone.utc)
        await self.repository.save_job(job)

    async def load_results(self, run) -> tuple[ConversionResult, ...]:
        jobs = await self.repository.jobs_for(str(run.id), tenant_id=run.tenant_id)
        succeeded = [job for job in jobs if job.state == "SUCCEEDED"]
        if not succeeded:
            return ()
        collection = await self.repository.load_collection(run)
        results = []
        for job in succeeded:
            try:
                documents = orjson.loads(job.documents)
                converted, errors = convert_documents(collection, run, documents)
            except (ValueError, TypeError):
                raise PipelineFailure("EVIDENCE_INVALID") from None
            if errors:
                raise PipelineFailure("EVIDENCE_INVALID")
            received = {item["resource_id"] for item in documents}
            if received != set(job.resource_ids):
                raise PipelineFailure("EVIDENCE_INVALID")
            results.extend(converted)
        return tuple(results)


def _redacted_json(value, sensitive):
    encoded = orjson.dumps(scrub_action_output(value)).decode()
    for secret in sensitive:
        if isinstance(secret, str) and secret:
            encoded = encoded.replace(orjson.dumps(secret).decode()[1:-1], "[REDACTED]")
    return encoded


def build_agent_stage() -> AgentStage:
    return AgentStage()
