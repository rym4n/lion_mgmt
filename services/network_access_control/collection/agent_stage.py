"""NORMALIZING stage: delegate policy normalization to lion_agent, re-validate per device."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import orjson

import settings
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.network_access_control import NetworkAccessPolicyAgentJob, NetworkAccessPolicyCollectionRun
from services.action_collection.agent_tokens import issue_job_token
from services.action_collection.outputs import scrub_action_output
from services.llm_config.resolver import resolve_llm
from services.network_access_control.collection.agent_jobs import AGENT_TIMEOUT_SECONDS, observations_for
from services.network_access_control.collection.limits import max_rules_per_device
from services.network_access_control.collection.pipeline import NormalizeResult
from services.network_access_control.collection.validation import validate_document

EXECUTE_PATH = "/internal/v1/builtin/policy-normalizer/execute"
MIN_AGENT_SECONDS = 60
AGENT_ERRORS = frozenset({
    "AGENT_TIMEOUT", "AGENT_SUBMIT_REJECTED", "AGENT_VALIDATION_EXHAUSTED",
    "AGENT_UNAVAILABLE", "AGENT_INPUT_UNAVAILABLE",
})
SUMMARY_FIELDS = (
    "state", "profile_version", "cache_hits", "cache_misses", "iterations",
    "token_usage", "transcript", "error_code", "error_message", "finished_at",
)


class _AgentFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PolicyNormalizerClient:
    def __init__(self, endpoint=None, token=None):
        self.endpoint = settings.LION_AGENT_ENDPOINT if endpoint is None else endpoint
        self.token = settings.LION_AGENT_INTERNAL_TOKEN if token is None else token

    async def execute(self, payload) -> tuple[int, dict]:
        async with httpx.AsyncClient(timeout=payload["timeout_seconds"] + 60) as client:
            response = await client.post(
                self.endpoint.rstrip("/") + EXECUTE_PATH,
                headers={"X-Internal-Token": self.token}, json=payload,
            )
            return response.status_code, response.json()


class OrmPolicyAgentJobRepository:
    async def jobs_for(self, collection_run_id, *, tenant_id):
        return await NetworkAccessPolicyAgentJob.filter(
            collection_run_id=collection_run_id, tenant_id=tenant_id, deleted=False,
        ).order_by("attempt")

    async def create_job(self, **values):
        return await NetworkAccessPolicyAgentJob.create(**values)

    async def get_job(self, job_id, *, tenant_id):
        return await NetworkAccessPolicyAgentJob.filter(
            id=job_id, tenant_id=tenant_id, deleted=False,
        ).first()

    async def save_job(self, job):
        # Documents and failed resources are written by the independent submit handler.
        await job.save(update_fields=SUMMARY_FIELDS)

    async def load_raw_outputs(self, run) -> dict:
        row = await NetworkAccessPolicyCollectionRun.filter(
            id=run.id, tenant_id=run.tenant_id, deleted=False,
        ).first()
        if row is None or not row.raw_outputs:
            return {}
        try:
            value = orjson.loads(row.raw_outputs)
        except (orjson.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


def _failed(resource_ids, code) -> dict[str, NormalizeResult]:
    return {rid: NormalizeResult(rid, "failed", None, code) for rid in resource_ids}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _redacted_json(value, sensitive) -> str:
    encoded = orjson.dumps(scrub_action_output(value)).decode()
    for secret in sensitive:
        if isinstance(secret, str) and secret:
            encoded = encoded.replace(orjson.dumps(secret).decode()[1:-1], "[REDACTED]")
    return encoded


class AgentNormalizer:
    def __init__(self, *, client=None, repository=None, llm_resolver=resolve_llm,
                 rules_limit=max_rules_per_device, now=None):
        self.client = client if client is not None else PolicyNormalizerClient()
        self.repository = repository if repository is not None else OrmPolicyAgentJobRepository()
        self.llm_resolver = llm_resolver
        self.rules_limit = rules_limit
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def __call__(self, plan, run, resource_ids: list[str]) -> dict[str, NormalizeResult]:
        resource_ids = sorted(set(resource_ids))
        if not resource_ids:
            return {}
        try:
            resolved = await self.llm_resolver(run.tenant_id, purpose="chat")
        except CustomError as exc:
            if exc.enum == ErrorCode.LLM_CONFIG_NOT_CONFIGURED:
                return _failed(resource_ids, "AGENT_NOT_CONFIGURED")
            raise
        elapsed = (self.now() - _aware(run.started_at)).total_seconds()
        timeout = int(min(AGENT_TIMEOUT_SECONDS, int(plan.run_timeout_seconds) - elapsed))
        if timeout < MIN_AGENT_SECONDS:
            return _failed(resource_ids, "AGENT_TIMEOUT")
        token, token_hash = issue_job_token()
        jobs = await self.repository.jobs_for(str(run.id), tenant_id=run.tenant_id)
        job = await self.repository.create_job(
            tenant_id=run.tenant_id, collection_run_id=str(run.id), attempt=len(jobs) + 1,
            state="RUNNING", resource_ids=resource_ids, job_token_hash=token_hash,
            started_at=self.now(),
        )
        llm = resolved.to_payload()
        payload = {
            "job_id": str(job.id), "tenant_id": run.tenant_id, "agent_job_token": token,
            "mgmt_base_url": settings.MGMT_INTERNAL_BASE_URL, "llm": llm,
            "timeout_seconds": timeout,
        }
        sensitive = (token, llm.get("api_key", ""), settings.LION_AGENT_INTERNAL_TOKEN)
        transcript: list = []
        try:
            submitted, body = await self._execute(job, payload, transcript)
        except _AgentFailure as exc:
            await self._finish(job, "FAILED", transcript, sensitive, code=exc.code)
            return _failed(resource_ids, exc.code)
        except asyncio.CancelledError:
            await self._finish(job, "FAILED", transcript, sensitive, code="AGENT_UNAVAILABLE")
            raise
        for field in ("cache_hits", "cache_misses", "iterations"):
            value = body.get(field, 0)
            setattr(submitted, field, value if type(value) is int and value >= 0 else 0)
        profile = body.get("profile_version")
        submitted.profile_version = profile if isinstance(profile, str) and len(profile) <= 255 else None
        try:
            submitted.token_usage = orjson.loads(_redacted_json(body.get("usage", {}), sensitive))
        except (TypeError, ValueError, RecursionError):
            submitted.token_usage = None
        transcript = body.get("transcript", []) if isinstance(body.get("transcript"), list) else []
        await self._finish(submitted, "SUCCEEDED", transcript, sensitive)
        return await self._results(run, submitted, resource_ids)

    async def _execute(self, job, payload, transcript):
        try:
            status, body = await self.client.execute(payload)
        except httpx.TimeoutException:
            raise _AgentFailure("AGENT_TIMEOUT") from None
        except (httpx.RequestError, ValueError, TypeError, RecursionError):
            raise _AgentFailure("AGENT_UNAVAILABLE") from None
        if not isinstance(body, dict):
            raise _AgentFailure("AGENT_UNAVAILABLE")
        if status == 422:
            detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
            if isinstance(detail.get("transcript"), list):
                transcript.extend(detail["transcript"])
            code = detail.get("code")
            raise _AgentFailure(code if isinstance(code, str) and code in AGENT_ERRORS else "AGENT_UNAVAILABLE")
        if status != 200:
            raise _AgentFailure("AGENT_UNAVAILABLE")
        submitted = await self.repository.get_job(str(job.id), tenant_id=job.tenant_id)
        if submitted is None or submitted.state != "SUBMITTED":
            raise _AgentFailure("AGENT_SUBMIT_REJECTED")
        return submitted, body

    async def _results(self, run, job, resource_ids) -> dict[str, NormalizeResult]:
        raw_outputs = await self.repository.load_raw_outputs(run)
        max_rules = await self.rules_limit()
        try:
            documents = orjson.loads(job.documents) if job.documents else []
        except (orjson.JSONDecodeError, TypeError):
            documents = []
        by_resource = {
            d.get("resource_id"): d.get("document")
            for d in documents if isinstance(d, dict)
        }
        results = {}
        for rid in resource_ids:
            document = by_resource.get(rid)
            errors = (["missing document"] if document is None else
                      validate_document(document, resource_id=rid,
                                        observations=observations_for(raw_outputs, rid),
                                        max_rules=max_rules))
            results[rid] = (
                NormalizeResult(rid, "success", document, None) if not errors else
                NormalizeResult(rid, "failed", None, "POLICY_NORMALIZE_FAILED")
            )
        return results

    async def _finish(self, job, state, transcript, sensitive, *, code=None):
        job.state = state
        job.error_code = code
        job.error_message = None
        try:
            job.transcript = _redacted_json(transcript, sensitive)
        except (ValueError, TypeError, RecursionError):
            job.transcript = "[]"
        job.finished_at = self.now()
        await self.repository.save_job(job)


def build_agent_normalizer() -> AgentNormalizer:
    return AgentNormalizer()
