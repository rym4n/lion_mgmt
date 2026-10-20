from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from services.action_collection.errors import CollectionError
from services.network_access_control.collection import agent_jobs, agent_views
from tests.network_access_control.policy_agent_fixtures import RAW_OUTPUTS, TARGETS


def test_build_input_uses_raw_action_output_shape():
    resources = agent_jobs.build_agent_input(RAW_OUTPUTS, TARGETS, ["r1"])
    assert [r["resource_id"] for r in resources] == ["r1"]
    obs = resources[0]["observations"][0]
    assert resources[0]["resource_name"] == "FW-1"
    assert obs["observation_id"] == "r1:i1" and obs["kind"] == "raw.action_output"
    assert set(obs["data"]) == {"app_name", "action_func", "app_version", "output"}


def test_build_input_skips_resources_without_output():
    assert agent_jobs.build_agent_input(RAW_OUTPUTS, TARGETS, ["r9"]) == []


def request(body, headers=None):
    return SimpleNamespace(json=body, headers=headers or {"X-Internal-Token": "x"})


@pytest.mark.asyncio
async def test_input_endpoint_returns_job_resources_schema_and_limit(monkeypatch):
    job = SimpleNamespace(id="j1", tenant_id="t1", collection_run_id="cr1", resource_ids=["r2"])
    authenticate = AsyncMock(return_value=job)
    monkeypatch.setattr(agent_views, "authenticate_job", authenticate)
    monkeypatch.setattr(agent_views.agent_jobs, "load_context", AsyncMock(return_value=(RAW_OUTPUTS, TARGETS)))
    monkeypatch.setattr(agent_views, "max_rules_per_device", AsyncMock(return_value=20000))
    response = await agent_views.policy_agent_job_input(request({"job_id": "j1", "agent_job_token": "t"}))
    data = orjson.loads(response.body)["data"]
    assert [r["resource_id"] for r in data["resources"]] == ["r2"]
    assert data["document_schema"]["$id"].endswith("aioe.network-policy.normalized/v1")
    assert data["max_rules_per_device"] == 20000
    authenticate.assert_awaited_once_with({"X-Internal-Token": "x"},
                                          {"job_id": "j1", "agent_job_token": "t"},
                                          job_model=agent_views.NetworkAccessPolicyAgentJob,
                                          timeout_seconds=1800)


@pytest.mark.asyncio
async def test_input_endpoint_forbidden_without_valid_job(monkeypatch):
    monkeypatch.setattr(agent_views, "authenticate_job",
                        AsyncMock(side_effect=CollectionError("AGENT_JOB_FORBIDDEN", 403)))
    response = await agent_views.policy_agent_job_input(request({"job_id": "j1", "agent_job_token": "t"}))
    assert response.status == 403
    assert orjson.loads(response.body)["data"]["error_code"] == "AGENT_JOB_FORBIDDEN"


@pytest.mark.asyncio
async def test_input_endpoint_rejects_non_object_body(monkeypatch):
    response = await agent_views.policy_agent_job_input(request(["x"]))
    assert response.status == 403


def test_security_middleware_whitelists_only_agent_prefixes():
    from pathlib import Path
    text = Path("core/middlewares/security.py").read_text(encoding="utf-8")
    assert '"/network-access-control/internal/policy-agent-jobs/"' in text
    assert '"/network-access-control/internal/policy-agent-facts/"' in text
    assert "/network-access-control/internal/policy-collection" not in text


def test_blueprint_registered():
    from services.app_factory import LICENSED_BLUEPRINT_SPECS
    assert ("services.network_access_control.collection.agent_views", "policy_agent_internal_bp") in \
        LICENSED_BLUEPRINT_SPECS["network_access_control_bp"]
