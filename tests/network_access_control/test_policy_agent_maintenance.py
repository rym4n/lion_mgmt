from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import orjson
import pytest

from libs.error_code.exception import CustomError
from services.network_access_control.collection import agent_maintenance_views as views


def job(**overrides):
    values = dict(id="j1", tenant_id="tenant-b", collection_run_id="cr1", state="SUCCEEDED", attempt=1,
                  resource_ids=["r1"], cache_hits=1, cache_misses=0, iterations=3, token_usage={"input": 4},
                  profile_version="policy-normalizer/1", error_code=None, error_message=None,
                  started_at=None, finished_at=None, ctime=None, job_token_hash="secret",
                  transcript='[{"type":"cache_hit"}]', documents='[{"resource_id":"r1"}]',
                  failed='[{"resource_id":"r2","code":"AGENT_VALIDATION_EXHAUSTED","message":"m"}]')
    values.update(overrides)
    return SimpleNamespace(**values)


def request(body):
    return SimpleNamespace(json=body, ctx=SimpleNamespace(session={"tenant_id": "tenant-a", "sub": "admin1"}))


def data(response):
    return orjson.loads(response.body)["data"]


class Query:
    def __init__(self, rows):
        self.rows, self.fields, self.start, self.size = rows, None, 0, len(rows)

    def order_by(self, *fields): return self
    def offset(self, start): self.start = start; return self
    def limit(self, size): self.size = size; return self
    def values(self, *fields): self.fields = fields; return self
    async def count(self): return len(self.rows)
    async def first(self): return self.rows[0] if self.rows else None

    def __await__(self):
        async def done():
            rows = self.rows[self.start:self.start + self.size]
            return [{k: getattr(r, k) for k in self.fields} for r in rows] if self.fields else rows
        return done().__await__()


@pytest.fixture
def jobs(monkeypatch):
    calls = []
    def install(rows):
        def filtered(**filters):
            calls.append(filters)
            return Query([r for r in rows if all(getattr(r, k, None) == v for k, v in filters.items()
                                                 if k in ("id", "tenant_id", "state"))])
        monkeypatch.setattr(views.NetworkAccessPolicyAgentJob, "filter", staticmethod(filtered))
        return calls
    return install


@pytest.fixture
def maintenance(monkeypatch):
    monkeypatch.setattr(views, "current_maintenance_mode", AsyncMock(return_value=True))


@pytest.mark.asyncio
async def test_requires_maintenance_mode(monkeypatch):
    monkeypatch.setattr(views, "current_maintenance_mode", AsyncMock(return_value=False))
    with pytest.raises(CustomError):
        await views.list_policy_agent_jobs(request({}))


@pytest.mark.asyncio
async def test_list_projects_safe_fields_and_filters_tenant(jobs, maintenance):
    calls = jobs([job(), job(id="j2", tenant_id="tenant-c")])
    result = data(await views.list_policy_agent_jobs(request({"tenant_id": "tenant-b"})))
    assert result["total"] == 1 and result["list"][0]["id"] == "j1"
    assert "job_token_hash" not in result["list"][0] and "transcript" not in result["list"][0]
    assert calls[0]["deleted"] is False


@pytest.mark.asyncio
async def test_detail_includes_transcript_and_failed(jobs, maintenance):
    jobs([job()])
    result = data(await views.detail_policy_agent_job(request({"job_id": "j1"})))
    assert result["transcript"] == [{"type": "cache_hit"}]
    assert result["failed"][0]["resource_id"] == "r2"
    assert "job_token_hash" not in result


@pytest.mark.asyncio
async def test_detail_rejects_nested_tenant_field(jobs, maintenance):
    jobs([job()])
    response = await views.detail_policy_agent_job(request({"job_id": "j1", "x": {"tenant_id": "t"}}))
    assert response.status == 403


@pytest.mark.asyncio
async def test_clear_forwards_to_policy_normalizer(monkeypatch, maintenance):
    seen = {}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, headers=None, json=None):
            seen.update(url=url, json=json)
            return SimpleNamespace(status_code=200, json=lambda: {"removed": 3})
    monkeypatch.setattr(views.httpx, "AsyncClient", Client)
    result = data(await views.clear_policy_normalizer_scripts(request({"tenant_id": "tenant-b"})))
    assert result == {"removed": 3}
    assert seen["url"].endswith("/internal/v1/builtin/policy-normalizer/scripts/clear")
    assert seen["json"] == {"tenant_id": "tenant-b"}


@pytest.mark.asyncio
async def test_clear_maps_agent_failure(monkeypatch, maintenance):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): raise httpx.ConnectError("down")
    monkeypatch.setattr(views.httpx, "AsyncClient", Client)
    response = await views.clear_policy_normalizer_scripts(request({"tenant_id": "tenant-b"}))
    assert response.status == 503 and data(response)["error_code"] == "AGENT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_clear_rejects_unsafe_tenant(maintenance):
    response = await views.clear_policy_normalizer_scripts(request({"tenant_id": "../x"}))
    assert response.status == 400


def test_routes_and_registration():
    from services.app_factory import LICENSED_BLUEPRINT_SPECS
    paths = {route.uri for route in views.policy_agent_maintenance_bp._future_routes}
    assert {"/policy-agent-jobs/list", "/policy-agent-jobs/detail", "/policy-normalizer/scripts/clear"} <= paths
    assert ("services.network_access_control.collection.agent_maintenance_views", "policy_agent_maintenance_bp") in LICENSED_BLUEPRINT_SPECS["network_access_control_bp"]
