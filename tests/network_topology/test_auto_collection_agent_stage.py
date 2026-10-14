from types import SimpleNamespace

import httpx
import orjson
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.network_topology.agent_stage import AgentStage, NormalizerClient, build_agent_stage
from services.network_topology.analysis_runs import classify_error, PUBLIC_RUN_ERRORS
from services.network_topology.worker import PipelineFailure
from tests.network_topology.auto_collection_fakes import FakeAgentJobRepository, raw_collection


class FakeClient:
    def __init__(self, status=200, body=None, repo=None, error=None):
        self.status, self.body, self.repo, self.error = status, body if body is not None else {}, repo, error
        self.payloads = []

    async def execute(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        if self.repo is not None:
            self.repo.mark_submitted(payload["job_id"])
        return self.status, self.body


async def llm(tenant_id, *, purpose="chat", config_id=None):
    assert tenant_id == "t1" and purpose == "chat"
    return SimpleNamespace(to_payload=lambda: {"model": "m", "base_url": "u", "api_key": "private-key"})


def context():
    collection, ids = raw_collection({"r1": [{"code": 200, "data": {}}]})
    run = SimpleNamespace(id="ar-1", tenant_id="t1", topology_row_id="topo-1", agent_observation_ids=ids)
    return run, collection


@pytest.mark.asyncio
async def test_no_pending_observations_is_noop():
    client = FakeClient(error=AssertionError("must not execute"))
    assert await AgentStage(client=client).run(SimpleNamespace(agent_observation_ids=[]), {}, None) == ()


@pytest.mark.asyncio
async def test_success_records_summary_and_reentry_does_not_execute_again():
    run, collection = context()
    repo = FakeAgentJobRepository(collection)
    body = dict(cache_hits=1, cache_misses=2, iterations=3, usage={"total_tokens": 7},
                profile_version="topology-normalizer/1", transcript=[{"event": "done"}])
    client = FakeClient(body=body, repo=repo)
    stage = AgentStage(client=client, repository=repo, llm_resolver=llm)
    assert await stage.run(run, collection, None) == ()
    job = repo.jobs[0]
    assert (job.state, job.cache_hits, job.cache_misses, job.iterations) == ("SUCCEEDED", 1, 2, 3)
    assert job.token_usage == body["usage"] and orjson.loads(job.transcript) == body["transcript"]
    assert job.resource_ids == ["r1"] and job.collection_run_id == "run-1" and job.attempt == 1
    assert job.started_at.tzinfo is not None and job.finished_at >= job.started_at
    assert client.payloads[0]["timeout_seconds"] == 1800
    assert len(client.payloads[0]["agent_job_token"]) >= 43
    assert job.job_token_hash != client.payloads[0]["agent_job_token"]
    await stage.run(run, collection, None)
    assert len(client.payloads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,error,code", [
    (200, {}, None, "AGENT_SUBMIT_REJECTED"),
    (422, {"detail": {"code": "AGENT_TIMEOUT", "message": "private-key", "transcript": []}}, None, "AGENT_TIMEOUT"),
    (422, {"detail": {"code": "AGENT_VALIDATION_EXHAUSTED"}}, None, "AGENT_VALIDATION_EXHAUSTED"),
    (422, {"detail": {"code": "untrusted-secret-code"}}, None, "AGENT_UNAVAILABLE"),
    (422, {"detail": {"code": "AGENT_INPUT_UNAVAILABLE"}}, None, "AGENT_INPUT_UNAVAILABLE"),
    (503, {}, None, "AGENT_UNAVAILABLE"),
    (200, {}, httpx.ConnectError("private-key"), "AGENT_UNAVAILABLE"),
    (200, {}, httpx.ReadTimeout("private-key"), "AGENT_TIMEOUT"),
    (200, [], None, "AGENT_UNAVAILABLE"),
])
async def test_failures_are_stable_terminal_jobs_and_retryable(status, body, error, code):
    run, collection = context()
    repo = FakeAgentJobRepository()
    client = FakeClient(status, body, error=error)
    stage = AgentStage(client=client, repository=repo, llm_resolver=llm)
    with pytest.raises(PipelineFailure) as caught:
        await stage.run(run, collection, None)
    assert caught.value.error_code == code and str(caught.value) == code
    job = repo.jobs[0]
    assert job.state == "FAILED" and job.error_code == code and job.finished_at is not None
    assert "private-key" not in str(vars(job)) and "untrusted-secret-code" not in str(vars(job))
    assert classify_error(code).retryable is (code != "AGENT_INPUT_UNAVAILABLE")
    assert code in PUBLIC_RUN_ERRORS
    with pytest.raises(PipelineFailure):
        await stage.run(run, collection, None)
    assert [j.attempt for j in repo.jobs] == [1, 2]


@pytest.mark.asyncio
async def test_missing_model_is_not_retryable_and_creates_no_job():
    async def missing(*args, **kwargs):
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)
    run, collection = context()
    repo = FakeAgentJobRepository()
    with pytest.raises(PipelineFailure) as caught:
        await AgentStage(client=FakeClient(), repository=repo, llm_resolver=missing).run(run, collection, None)
    assert caught.value.error_code == "AGENT_MODEL_NOT_CONFIGURED"
    assert not classify_error(caught.value.error_code).retryable
    assert caught.value.error_code in PUBLIC_RUN_ERRORS
    assert repo.jobs == []


@pytest.mark.asyncio
async def test_load_results_revalidates_persisted_documents():
    run, collection = context()
    repo = FakeAgentJobRepository(collection)
    stage = AgentStage(client=FakeClient(repo=repo), repository=repo, llm_resolver=llm)
    await stage.run(run, collection, None)
    repo.jobs[0].documents = '[{"resource_id":"outside-job","document":{}}]'
    with pytest.raises(PipelineFailure) as caught:
        await stage.load_results(run)
    assert caught.value.error_code == "EVIDENCE_INVALID"
    for invalid in ("invalid-json", "null", "{}", '"hello"'):
        repo.jobs[0].documents = invalid
        with pytest.raises(PipelineFailure, match="EVIDENCE_INVALID"):
            await stage.load_results(run)


@pytest.mark.asyncio
async def test_normalizer_client_protocol(monkeypatch):
    observed = {}
    class HttpClient:
        def __init__(self, *, timeout):
            observed["timeout"] = timeout
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            observed.update(url=url, **kwargs)
            return httpx.Response(200, json={"submitted": True})
    monkeypatch.setattr(httpx, "AsyncClient", HttpClient)
    payload = {"timeout_seconds": 1800, "job_id": "j"}
    assert await NormalizerClient(endpoint="http://agent/", token="internal-token").execute(payload) == (200, {"submitted": True})
    assert observed == dict(timeout=1860, url="http://agent/internal/v1/builtin/topology-normalizer/execute",
                            headers={"X-Internal-Token": "internal-token"}, json=payload)
    assert isinstance(build_agent_stage(), AgentStage)


@pytest.mark.asyncio
async def test_load_results_converts_valid_candidates_and_rejects_incomplete_documents():
    from tests.network_topology.test_auto_collection_agent_jobs import collection as valid_collection, doc
    collection = valid_collection()
    run = SimpleNamespace(id="ar-1", tenant_id="t1", topology_row_id="topo-1", agent_observation_ids=["obs-1"])
    repo = FakeAgentJobRepository(collection)
    stage = AgentStage(client=FakeClient(repo=repo), repository=repo, llm_resolver=llm)
    await stage.run(run, collection, None)
    repo.jobs[0].documents = orjson.dumps([{"resource_id": "r1", "chunk_id": "r1:raw:v1", "document": doc()}]).decode()
    results = await stage.load_results(run)
    assert len(results) == 1 and results[0].facts["devices"][0]["id"] == "device:r1"
    repo.jobs[0].documents = "[]"
    with pytest.raises(PipelineFailure, match="EVIDENCE_INVALID"):
        await stage.load_results(run)


@pytest.mark.asyncio
async def test_cancellation_revokes_job_and_propagates():
    import asyncio
    run, collection = context()
    repo = FakeAgentJobRepository()
    stage = AgentStage(client=FakeClient(error=asyncio.CancelledError()), repository=repo, llm_resolver=llm)
    with pytest.raises(asyncio.CancelledError):
        await stage.run(run, collection, None)
    assert repo.jobs[0].state == "FAILED" and repo.jobs[0].finished_at is not None


@pytest.mark.asyncio
async def test_transcript_cannot_persist_outbound_credentials():
    run, collection = context()
    repo = FakeAgentJobRepository()
    class EchoClient(FakeClient):
        async def execute(self, payload):
            repo.mark_submitted(payload["job_id"])
            return 200, {"transcript": [{"content": "echo " + payload["agent_job_token"]}]}
    await AgentStage(client=EchoClient(), repository=repo, llm_resolver=llm).run(run, collection, None)
    assert "private-key" not in repo.jobs[0].transcript
    assert "[REDACTED]" in repo.jobs[0].transcript


def malformed_transcript(kind):
    if kind == "huge-integer":
        return [{"value": 2 ** 100}]
    value = []
    for _ in range(600):
        value = [value]
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["huge-integer", "deep-json"])
@pytest.mark.parametrize("status", [200, 422])
async def test_malformed_transcript_persists_failed_job_with_detached_rows(kind, status):
    from copy import copy

    class DetachedRepository(FakeAgentJobRepository):
        async def create_job(self, **values):
            return copy(await super().create_job(**values))

        async def jobs_for(self, analysis_run_id, *, tenant_id):
            return [copy(job) for job in await super().jobs_for(analysis_run_id, tenant_id=tenant_id)]

        async def get_job(self, job_id, *, tenant_id):
            return copy(await super().get_job(job_id, tenant_id=tenant_id))

        async def save_job(self, job):
            index = next(i for i, stored in enumerate(self.jobs) if stored.id == job.id)
            self.jobs[index] = copy(job)

    run, collection = context()
    repo = DetachedRepository()
    transcript = malformed_transcript(kind)
    body = {"transcript": transcript} if status == 200 else {
        "detail": {"code": "AGENT_UNAVAILABLE", "transcript": transcript},
    }
    stage = AgentStage(client=FakeClient(status, body, repo=repo), repository=repo, llm_resolver=llm)
    with pytest.raises(PipelineFailure, match="^AGENT_UNAVAILABLE$"):
        await stage.run(run, collection, None)
    persisted = repo.jobs[0]
    assert persisted.state == "FAILED"
    assert persisted.error_code == "AGENT_UNAVAILABLE"
    assert persisted.finished_at is not None
    assert orjson.loads(persisted.transcript) == []
