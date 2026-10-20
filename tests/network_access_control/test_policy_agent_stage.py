from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.network_access_control.collection.agent_stage import AgentNormalizer
from tests.network_access_control.policy_agent_fakes import FakeNormalizerClient, FakePolicyJobRepository
from tests.network_access_control.policy_agent_fixtures import RAW_OUTPUTS, document, policy

NOW = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


async def llm(tenant_id, *, purpose="chat", config_id=None):
    return SimpleNamespace(to_payload=lambda: {"model": "m", "base_url": "u", "api_key": "private-key"})


def context(started_ago=60):
    plan = SimpleNamespace(id="p1", tenant_id="t1", run_timeout_seconds=3600)
    run = SimpleNamespace(id="cr1", tenant_id="t1", plan_id="p1", started_at=NOW - timedelta(seconds=started_ago))
    return plan, run


def normalizer(client, repo, resolver=llm):
    return AgentNormalizer(client=client, repository=repo, llm_resolver=resolver,
                           rules_limit=AsyncMock(return_value=20000), now=lambda: NOW)


@pytest.mark.asyncio
async def test_success_returns_validated_document_and_partial_failure():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    client = FakeNormalizerClient(repo=repo, documents=[{"resource_id": "r1", "document": document()}],
                                  failed=[{"resource_id": "r2", "code": "AGENT_VALIDATION_EXHAUSTED", "message": "x"}])
    plan, run = context()
    results = await normalizer(client, repo)(plan, run, ["r1", "r2"])
    assert results["r1"].status == "success" and results["r1"].document["resource_id"] == "r1"
    assert results["r2"].status == "failed" and results["r2"].error_code == "POLICY_NORMALIZE_FAILED"
    job = repo.jobs[0]
    assert job.state == "SUCCEEDED" and job.cache_hits == 1 and job.resource_ids == ["r1", "r2"]
    assert "[REDACTED]" in job.transcript and client.payloads[0]["agent_job_token"] not in job.transcript
    assert client.payloads[0]["timeout_seconds"] == 1800


@pytest.mark.asyncio
async def test_second_validation_marks_fabricated_document_failed():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    fake = document(policies=[policy(raw_text="rule name evil source-address 6.6.6.6")])
    client = FakeNormalizerClient(repo=repo, documents=[{"resource_id": "r1", "document": fake}])
    plan, run = context()
    results = await normalizer(client, repo)(plan, run, ["r1"])
    assert results["r1"].status == "failed" and results["r1"].error_code == "POLICY_NORMALIZE_FAILED"
    assert results["r1"].document is None


@pytest.mark.asyncio
async def test_missing_document_is_failed():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    client = FakeNormalizerClient(repo=repo, documents=[{"resource_id": "r1", "document": document()}])
    plan, run = context()
    results = await normalizer(client, repo)(plan, run, ["r1", "r2"])
    assert results["r2"].error_code == "POLICY_NORMALIZE_FAILED"


@pytest.mark.asyncio
async def test_unconfigured_llm_fails_every_resource_without_job():
    async def missing(*args, **kwargs):
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    client = FakeNormalizerClient(error=AssertionError("must not call"))
    plan, run = context()
    results = await normalizer(client, repo, missing)(plan, run, ["r1", "r2"])
    assert {r.error_code for r in results.values()} == {"AGENT_NOT_CONFIGURED"} and repo.jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("client,code", [
    (FakeNormalizerClient(error=httpx.ReadTimeout("t")), "AGENT_TIMEOUT"),
    (FakeNormalizerClient(error=httpx.ConnectError("c")), "AGENT_UNAVAILABLE"),
    (FakeNormalizerClient(status=422, body={"detail": {"code": "AGENT_VALIDATION_EXHAUSTED", "transcript": []}}), "AGENT_VALIDATION_EXHAUSTED"),
    (FakeNormalizerClient(status=422, body={"detail": {"code": "WHATEVER"}}), "AGENT_UNAVAILABLE"),
    (FakeNormalizerClient(status=500, body={}), "AGENT_UNAVAILABLE"),
])
async def test_agent_level_failures_fail_all_resources(client, code):
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    plan, run = context()
    results = await normalizer(client, repo)(plan, run, ["r1", "r2"])
    assert {r.error_code for r in results.values()} == {code}
    assert repo.jobs[0].state == "FAILED" and repo.jobs[0].error_code == code


@pytest.mark.asyncio
async def test_200_without_submission_is_submit_rejected():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    client = FakeNormalizerClient()
    plan, run = context()
    results = await normalizer(client, repo)(plan, run, ["r1"])
    assert results["r1"].error_code == "AGENT_SUBMIT_REJECTED"


@pytest.mark.asyncio
async def test_insufficient_remaining_time_skips_agent():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    client = FakeNormalizerClient(error=AssertionError("must not call"))
    plan, run = context(started_ago=3560)
    results = await normalizer(client, repo)(plan, run, ["r1"])
    assert results["r1"].error_code == "AGENT_TIMEOUT" and repo.jobs == []


@pytest.mark.asyncio
async def test_remaining_time_caps_timeout():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    client = FakeNormalizerClient(repo=repo, documents=[{"resource_id": "r1", "document": document()}])
    plan, run = context(started_ago=3000)
    await normalizer(client, repo)(plan, run, ["r1"])
    assert client.payloads[0]["timeout_seconds"] == 600


@pytest.mark.asyncio
async def test_empty_resource_list_is_noop():
    client = FakeNormalizerClient(error=AssertionError("must not call"))
    plan, run = context()
    assert await normalizer(client, FakePolicyJobRepository(RAW_OUTPUTS))(plan, run, []) == {}


@pytest.mark.asyncio
async def test_attempt_increments_per_run():
    repo = FakePolicyJobRepository(RAW_OUTPUTS)
    plan, run = context()
    for _ in range(2):
        client = FakeNormalizerClient(repo=repo, documents=[{"resource_id": "r1", "document": document()}])
        await normalizer(client, repo)(plan, run, ["r1"])
    assert [j.attempt for j in repo.jobs] == [1, 2]


def test_default_normalizer_is_agent_normalizer():
    from services.network_access_control.collection import pipeline

    assert isinstance(pipeline.default_normalizer(), AgentNormalizer)
