from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import orjson
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.network_topology.collection import runs


def job(**overrides):
    values = dict(id="j1", tenant_id="tenant-b", analysis_run_id="ar1", topology_id="top1",
                  collection_run_id="cr1", state="SUCCEEDED", resource_ids=["r1"], cache_hits=1,
                  cache_misses=0, attempt=2, iterations=3, token_usage={"input": 4},
                  profile_version="p1", error_code=None, error_message=None,
                  transcript='[{"type":"cache_hit"}]', documents='[{"resource_id":"r1"}]',
                  job_token_hash="secret", started_at=None, finished_at=None, ctime=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def request(body):
    return SimpleNamespace(json=body, ctx=SimpleNamespace(session={"tenant_id": "tenant-a", "sub": "admin1"}))


def data(response):
    return orjson.loads(response.body)["data"]


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.fields = None
        self.start = 0
        self.size = len(rows)

    def order_by(self, *fields):
        self.rows = sorted(self.rows, key=lambda row: row.attempt, reverse=True)
        return self

    def offset(self, start):
        self.start = start
        return self

    def limit(self, size):
        self.size = size
        return self

    def values(self, *fields):
        self.fields = fields
        return self

    async def count(self):
        return len(self.rows)

    async def first(self):
        return self.rows[0] if self.rows else None

    def __await__(self):
        async def done():
            rows = self.rows[self.start:self.start + self.size]
            return [{key: getattr(row, key) for key in self.fields} for row in rows] if self.fields else rows
        return done().__await__()


def model(monkeypatch, module, name, rows):
    calls, queries = [], []
    def filtered(**filters):
        calls.append(filters)
        query = Query([row for row in rows if all(
            key == "deleted" or getattr(row, key) == value for key, value in filters.items()
        )])
        queries.append(query)
        return query
    monkeypatch.setattr(module, name, SimpleNamespace(filter=filtered))
    return calls, queries


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["list_agent_jobs", "detail_agent_job", "rerun_agent_job", "clear_agent_scripts"])
async def test_maintenance_endpoints_require_maintenance_mode(monkeypatch, handler):
    from services.network_topology import agent_maintenance_views as views
    monkeypatch.setattr(views, "current_maintenance_mode", AsyncMock(return_value=False))
    with pytest.raises(CustomError) as exc:
        await getattr(views, handler)(request([]))
    assert exc.value.enum == ErrorCode.FORBIDDEN


def test_detail_hides_diagnostics_outside_maintenance():
    summary = runs.agent_summary(job(), maintenance=False)
    assert summary == {"state": "SUCCEEDED", "resources": 1, "cache_hits": 1, "cache_misses": 0}
    detailed = runs.agent_summary(job(), maintenance=True)
    assert detailed["transcript"] == [{"type": "cache_hit"}]
    assert detailed["job_id"] == "j1" and detailed["token_usage"] == {"input": 4}
    assert "documents" not in detailed and "job_token_hash" not in detailed


@pytest.mark.asyncio
async def test_run_detail_loads_latest_tenant_scoped_agent_job(monkeypatch):
    calls, _ = model(monkeypatch, runs, "TopologyAgentJob", [job(tenant_id="tenant-a"), job(id="j2", tenant_id="tenant-a", attempt=3)])
    repo = SimpleNamespace(get_run=AsyncMock(return_value=SimpleNamespace(id="cr1", analysis_run_id="ar1")))
    result = await runs.run_detail("tenant-a", "cr1", maintenance=True, repository=repo)
    assert result["agent"]["job_id"] == "j2"
    assert calls == [{"tenant_id": "tenant-a", "analysis_run_id": "ar1", "deleted": False}]
    repo.get_run.return_value.analysis_run_id = None
    assert (await runs.run_detail("tenant-a", "cr1", repository=repo))["agent"] is None


@pytest.fixture
def views(monkeypatch):
    from services.network_topology import agent_maintenance_views
    monkeypatch.setattr(agent_maintenance_views, "current_maintenance_mode", AsyncMock(return_value=True))
    return agent_maintenance_views


@pytest.mark.asyncio
async def test_list_is_cross_tenant_paginated_and_excludes_sensitive_fields(monkeypatch, views):
    calls, queries = model(monkeypatch, views, "TopologyAgentJob", [job(), job(id="j2", attempt=3)])
    result = data(await views.list_agent_jobs(request({"page": 2, "page_size": 1})))
    assert "items" not in result
    assert result["total"] == 2 and len(result["list"]) == 1
    assert result["list"][0]["tenant_id"] == "tenant-b"
    assert calls == [{"deleted": False}]
    assert not {"documents", "transcript", "job_token_hash"} & set(queries[0].fields)
    await views.list_agent_jobs(request({"tenant_id": "tenant-b", "state": "SUCCEEDED"}))
    assert calls[-1] == {"deleted": False, "tenant_id": "tenant-b", "state": "SUCCEEDED"}


@pytest.mark.asyncio
async def test_maintenance_detail_decodes_encrypted_json_without_token_hash(monkeypatch, views):
    calls, _ = model(monkeypatch, views, "TopologyAgentJob", [job()])
    result = data(await views.detail_agent_job(request({"job_id": "j1"})))
    assert result["transcript"] == [{"type": "cache_hit"}]
    assert result["documents"] == [{"resource_id": "r1"}]
    assert "job_token_hash" not in result
    assert calls == [{"id": "j1", "deleted": False}]


@pytest.mark.asyncio
async def test_rerun_uses_job_analysis_tenant_snapshot_current_profile_and_admin_actor(monkeypatch, views):
    model(monkeypatch, views, "TopologyAgentJob", [job()])
    analysis = SimpleNamespace(id="ar1", tenant_id="tenant-b", topology_row_id="top1", snapshot_row_id="snap-row1")
    analysis_calls, _ = model(monkeypatch, views, "TopologyAnalysisRun", [analysis])
    snapshot_calls, _ = model(monkeypatch, views, "TopologyCollectionSnapshot", [
        SimpleNamespace(id="snap-row1", tenant_id="tenant-b", topology_row_id="top1", snapshot_id="snap1")])
    topology = SimpleNamespace(id="top1", tenant_id="tenant-b", topology_profile_revision=7,
                               current_graph_revision=8, publication_revision=9)
    topology_calls, _ = model(monkeypatch, views, "NetworkTopology", [topology])
    reanalyze = AsyncMock(return_value={"sync_id": "new1"})
    monkeypatch.setattr(views.analysis_runs, "reanalyze_snapshot", reanalyze)
    response = await views.rerun_agent_job(request({"job_id": "j1"}))
    assert data(response)["sync_id"] == "new1"
    assert data(response)["publish_as_current"] is False
    assert analysis_calls == [{"id": "ar1", "tenant_id": "tenant-b", "deleted": False}]
    assert snapshot_calls == [{"id": "snap-row1", "tenant_id": "tenant-b", "topology_row_id": "top1", "deleted": False}]
    assert topology_calls == [{"id": "top1", "tenant_id": "tenant-b", "deleted": False}]
    reanalyze.assert_awaited_once_with("tenant-b", "snap1", topology_id="top1", target_profile_revision=7,
                                    publish_as_current=False, expected_current_graph_revision=8,
                                    expected_publication_revision=9, actor="admin1")


@pytest.mark.asyncio
async def test_clear_scripts_targets_explicit_tenant_and_internal_token(monkeypatch, views):
    captured = []
    async def post(self, url, **kwargs):
        captured.append((url, kwargs))
        return httpx.Response(200, json={"removed": 2})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(views.settings, "LION_AGENT_ENDPOINT", "http://agent:8000/")
    monkeypatch.setattr(views.settings, "LION_AGENT_INTERNAL_TOKEN", "internal-secret")
    assert data(await views.clear_agent_scripts(request({"tenant_id": "tenant-b"}))) == {"removed": 2}
    assert captured == [("http://agent:8000/internal/v1/builtin/topology-normalizer/scripts/clear",
                         {"json": {"tenant_id": "tenant-b"}, "headers": {"X-Internal-Token": "internal-secret"}})]


@pytest.mark.asyncio
async def test_clear_scripts_hides_upstream_errors(monkeypatch, views):
    async def post(*args, **kwargs):
        return httpx.Response(500, json={"detail": "sensitive raw error"})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    response = await views.clear_agent_scripts(request({"tenant_id": "tenant-b"}))
    assert response.status == 503
    assert b"sensitive" not in response.body


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"page": 0}, {"page_size": True}, {"page_size": 101}, {"state": "invalid"}, {"tenant_id": []}])
async def test_list_rejects_invalid_filters(views, body):
    assert (await views.list_agent_jobs(request(body))).status == 400


@pytest.mark.asyncio
async def test_job_not_found_is_safe(monkeypatch, views):
    model(monkeypatch, views, "TopologyAgentJob", [])
    response = await views.detail_agent_job(request({"job_id": "missing"}))
    assert response.status == 404


def test_maintenance_routes_and_blueprint_registration(views):
    from services.app_factory import LICENSED_BLUEPRINT_SPECS
    paths = {route.uri for route in views.agent_maintenance_bp._future_routes}
    assert {"/agent-jobs/list", "/agent-jobs/detail", "/agent-jobs/rerun", "/agent-scripts/clear"} <= paths
    assert ("services.network_topology.agent_maintenance_views", "agent_maintenance_bp") in LICENSED_BLUEPRINT_SPECS["topology_bp"]


@pytest.mark.asyncio
async def test_rerun_stale_cas_conflict_is_returned_safely(monkeypatch, views):
    model(monkeypatch, views, "TopologyAgentJob", [job()])
    model(monkeypatch, views, "TopologyAnalysisRun", [SimpleNamespace(
        id="ar1", tenant_id="tenant-b", topology_row_id="top1", snapshot_row_id="sr1")])
    model(monkeypatch, views, "TopologyCollectionSnapshot", [SimpleNamespace(
        id="sr1", tenant_id="tenant-b", topology_row_id="top1", snapshot_id="snap1")])
    model(monkeypatch, views, "NetworkTopology", [SimpleNamespace(
        id="top1", tenant_id="tenant-b", topology_profile_revision=7,
        current_graph_revision=8, publication_revision=9)])
    monkeypatch.setattr(views.analysis_runs, "reanalyze_snapshot", AsyncMock(
        side_effect=views.analysis_runs.AnalysisRunError(views.analysis_runs.RUN_CAS_MISMATCH)))
    response = await views.rerun_agent_job(request({"job_id": "j1"}))
    assert response.status == 409
    assert data(response)["error_code"] == "UPLOAD_CONFLICT"
