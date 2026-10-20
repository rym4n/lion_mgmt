from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.network_access_control.collection import agent_jobs


def job(**overrides):
    values = dict(id="j1", state="SUCCEEDED", resource_ids=["r1", "r2"], cache_hits=2,
                  cache_misses=0, attempt=1, iterations=0, token_usage={},
                  profile_version="policy-normalizer/1", error_code=None, error_message=None,
                  transcript='[{"type":"cache_hit"}]')
    values.update(overrides)
    return SimpleNamespace(**values)


def test_public_summary_hides_diagnostics():
    assert agent_jobs.policy_agent_summary(job(), maintenance=False) == {
        "state": "SUCCEEDED", "resources": 2, "cache_hits": 2, "cache_misses": 0,
    }


def test_maintenance_summary_includes_transcript():
    result = agent_jobs.policy_agent_summary(job(), maintenance=True)
    assert result["job_id"] == "j1"
    assert result["transcript"] == [{"type": "cache_hit"}]


def test_maintenance_summary_handles_invalid_transcript():
    result = agent_jobs.policy_agent_summary(job(transcript="not-json"), maintenance=True)
    assert result["transcript"] == []


def test_none_job():
    assert agent_jobs.policy_agent_summary(None, maintenance=True) is None


@pytest.mark.asyncio
async def test_latest_job_filters_tenant_and_run_and_orders_attempt(monkeypatch):
    ordered = {}
    query = SimpleNamespace(
        order_by=lambda *fields: (ordered.update(fields=fields) or query),
        first=AsyncMock(return_value=job()),
    )
    seen = {}

    def filtered(**filters):
        seen.update(filters)
        return query

    monkeypatch.setattr(agent_jobs.NetworkAccessPolicyAgentJob, "filter", staticmethod(filtered))
    result = await agent_jobs.latest_job("tenant-a", "run-a")
    assert result.id == "j1"
    assert seen == {"tenant_id": "tenant-a", "collection_run_id": "run-a", "deleted": False}
    assert ordered["fields"] == ("-attempt", "-ctime", "-id")
