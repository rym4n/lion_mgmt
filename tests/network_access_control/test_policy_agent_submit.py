from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from services.network_access_control.collection import agent_jobs, agent_views
from tests.network_access_control.policy_agent_fixtures import RAW_OUTPUTS, TARGETS, document, policy


def job(resource_ids=("r1",)):
    return SimpleNamespace(id="j1", tenant_id="t1", collection_run_id="cr1",
                           resource_ids=list(resource_ids), state="RUNNING", job_token_hash="h")


def request(body):
    return SimpleNamespace(json=body, headers={"X-Internal-Token": "x"})


@pytest.fixture
def wired(monkeypatch):
    def setup(current_job):
        monkeypatch.setattr(agent_views, "authenticate_job", AsyncMock(return_value=current_job))
        monkeypatch.setattr(agent_views.agent_jobs, "load_context", AsyncMock(return_value=(RAW_OUTPUTS, TARGETS)))
        monkeypatch.setattr(agent_views, "max_rules_per_device", AsyncMock(return_value=20000))
        store = AsyncMock(return_value=True)
        monkeypatch.setattr(agent_views.agent_jobs, "store_submission", store)
        return store
    return setup


def body(documents, failed=()):
    return {"job_id": "j1", "agent_job_token": "tok", "documents": documents, "failed": list(failed)}


@pytest.mark.asyncio
async def test_submit_accepts_valid_document(wired):
    store = wired(job())
    response = await agent_views.policy_agent_facts_submit(request(body([{"resource_id": "r1", "document": document()}])))
    assert response.status == 200 and orjson.loads(response.body)["data"] == {"accepted": True}
    store.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_rejects_fabricated_raw_text(wired):
    store = wired(job())
    fake = document(policies=[policy(raw_text="rule name evil source-address 6.6.6.6 action permit")])
    response = await agent_views.policy_agent_facts_submit(request(body([{"resource_id": "r1", "document": fake}])))
    assert response.status == 422
    data = orjson.loads(response.body)["data"]
    assert data["error_code"] == "AGENT_SUBMISSION_INVALID"
    assert data["errors"][0]["resource_id"] == "r1"
    store.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("documents,failed", [
    ([], []),
    ([{"resource_id": "r1", "document": document()}], []),
    ([{"resource_id": "r1", "document": document()}],
     [{"resource_id": "r1", "code": "AGENT_VALIDATION_EXHAUSTED", "message": "x"},
      {"resource_id": "r2", "code": "AGENT_VALIDATION_EXHAUSTED", "message": "x"}]),
    ([{"resource_id": "r9", "document": document("r9")}],
     [{"resource_id": "r2", "code": "AGENT_VALIDATION_EXHAUSTED", "message": "x"}]),
])
async def test_submit_requires_exact_resource_coverage(wired, documents, failed):
    store = wired(job(("r1", "r2")))
    response = await agent_views.policy_agent_facts_submit(request(body(documents, failed)))
    assert response.status == 422
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_accepts_partial_with_failed_entries(wired):
    store = wired(job(("r1", "r2")))
    failed = [{"resource_id": "r2", "code": "AGENT_VALIDATION_EXHAUSTED",
               "message": {"password": "abc123"}}]
    response = await agent_views.policy_agent_facts_submit(
        request(body([{"resource_id": "r1", "document": document()}], failed)))
    assert response.status == 200
    stored_failed = store.await_args.args[2]
    assert stored_failed[0]["resource_id"] == "r2" and "abc123" not in stored_failed[0]["message"]


@pytest.mark.asyncio
async def test_submit_rejects_malformed_failed_code(wired):
    store = wired(job(("r1", "r2")))
    failed = [{"resource_id": "r2", "code": "bad code!", "message": "x"}]
    response = await agent_views.policy_agent_facts_submit(
        request(body([{"resource_id": "r1", "document": document()}], failed)))
    assert response.status == 422
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_submission_is_forbidden(wired):
    store = wired(job())
    store.return_value = False
    response = await agent_views.policy_agent_facts_submit(request(body([{"resource_id": "r1", "document": document()}])))
    assert response.status == 403
    assert orjson.loads(response.body)["data"]["error_code"] == "AGENT_JOB_FORBIDDEN"
