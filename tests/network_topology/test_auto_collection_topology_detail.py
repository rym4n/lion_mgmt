import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.network_topology import views
from tests.network_topology.test_auto_collection_agent_maintenance import model


def request():
    return SimpleNamespace(json={"id": "top1"}, ctx=SimpleNamespace(session={"tenant_id": "t1", "sub": "account1"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [None, "graph/v2"])
async def test_detail_contains_current_profile_revisions_and_collection_summary(monkeypatch, schema):
    topology = SimpleNamespace(id="top1", tenant_id="t1", document_schema_version=schema,
        topology_json={}, resource_revision=8, topology_profile_revision=3,
        source_config="PRIVATE_CONFIG", json={"id": "top1", "revision": 5})
    monkeypatch.setattr(views, "_tenant_topology", AsyncMock(return_value=topology))
    monkeypatch.setattr(views, "_v2_view_disabled", lambda row: False)
    monkeypatch.setattr(views, "_topology_payload", lambda row: row.json.copy())
    monkeypatch.setattr(views, "enrich_v2_payload", lambda payload, graph: payload)
    profile = SimpleNamespace(tenant_id="t1", topology_row_id="top1", revision=3,
        profile_json='{"root_scope_seed":"root","max_depth":8}', created_by="PRIVATE_ACTOR", profile_hash="PRIVATE_HASH")
    calls, _ = model(monkeypatch, views, "TopologyProfileRevision", [profile])
    summary = {"enabled": True, "last_run": {"id": "cr1", "state": "SUCCEEDED"}}
    summaries = AsyncMock(return_value={"top1": summary})
    monkeypatch.setattr(views.collection_plans, "collection_summaries", summaries)
    response = await inspect.unwrap(views.get_topology)(request())
    result = orjson.loads(response.body)["data"]
    assert result["resource_revision"] == 8 and result["topology_profile_revision"] == 3
    assert result["collection"] == summary
    summaries.assert_awaited_once_with("t1", ["top1"])
    if schema:
        assert result["profile"] == {"root_scope_seed": "root", "max_depth": 8}
        assert calls == [{"tenant_id": "t1", "topology_row_id": "top1", "revision": 3, "deleted": False}]
    else:
        assert result["profile"] is None and calls == []
    assert b"PRIVATE" not in response.body


@pytest.mark.asyncio
async def test_detail_denied_before_profile_or_collection_queries(monkeypatch):
    from libs.security import authorization
    async def denied(*args, **kwargs):
        raise CustomError(ErrorCode.FORBIDDEN)
    monkeypatch.setattr(authorization, "check_permissions", denied)
    monkeypatch.setattr(views, "_tenant_topology", AsyncMock(side_effect=AssertionError("must not read")))
    with pytest.raises(CustomError):
        await views.get_topology(request())


@pytest.mark.asyncio
async def test_missing_tenant_topology_does_not_read_profile_or_collection(monkeypatch):
    monkeypatch.setattr(views, "_tenant_topology", AsyncMock(return_value=None))
    summaries = AsyncMock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(views.collection_plans, "collection_summaries", summaries)
    response = await inspect.unwrap(views.get_topology)(request())
    assert response.status == 404
    summaries.assert_not_awaited()


@pytest.mark.asyncio
async def test_v2_detail_does_not_load_other_tenant_profile(monkeypatch):
    topology = SimpleNamespace(id="top1", tenant_id="t1", document_schema_version="graph/v2",
        topology_json={}, resource_revision=2, topology_profile_revision=3, json={"id": "top1"})
    monkeypatch.setattr(views, "_tenant_topology", AsyncMock(return_value=topology))
    monkeypatch.setattr(views, "_v2_view_disabled", lambda row: False)
    monkeypatch.setattr(views, "_topology_payload", lambda row: row.json.copy())
    monkeypatch.setattr(views, "enrich_v2_payload", lambda payload, graph: payload)
    model(monkeypatch, views, "TopologyProfileRevision", [SimpleNamespace(
        tenant_id="foreign", topology_row_id="top1", revision=3, profile_json='{"private":"SECRET"}')])
    monkeypatch.setattr(views.collection_plans, "collection_summaries", AsyncMock(return_value={}))
    response = await inspect.unwrap(views.get_topology)(request())
    payload = orjson.loads(response.body)["data"]
    assert payload["profile"] is None and payload["collection"] is None
    assert b"SECRET" not in response.body
