from types import SimpleNamespace

import pytest

from core.maintenance import DEFAULT_TENANT_ID
from services.network_topology.collection import plans, views


def _request(body, tenant_id):
    return SimpleNamespace(
        json=body,
        ctx=SimpleNamespace(
            session={
                "tenant_id": tenant_id,
                "sub": "acc-1",
                "account_info": SimpleNamespace(id="acc-1"),
            }
        ),
    )


def test_internal_caller_must_be_default_tenant():
    with pytest.raises(views.CollectionError) as exc:
        views._require_internal_caller(_request({"plan_id": "p1"}, "tenant-b"))
    assert exc.value.code == "COLLECTION_CALLER_FORBIDDEN"
    views._require_internal_caller(_request({"plan_id": "p1"}, DEFAULT_TENANT_ID))


def test_internal_caller_rejects_tenant_in_body():
    with pytest.raises(views.CollectionError):
        views._require_internal_caller(
            _request({"plan_id": "p1", "tenant_id": "x"}, DEFAULT_TENANT_ID)
        )


def test_routes_registered():
    paths = {
        getattr(route, "path", getattr(route, "uri", ""))
        for route in views.collection_bp._future_routes
    }
    for path in (
        "/collection-plan/get",
        "/collection-plan/save",
        "/collection-run/trigger",
        "/collection-run/cancel",
        "/collection-run/list",
        "/collection-run/detail",
        "/internal/collection/start",
        "/internal/collection/manifest",
        "/internal/collection/dispatch",
        "/internal/collection/snapshot",
        "/internal/collection/status",
    ):
        assert any(item.endswith(path) for item in paths), path


@pytest.mark.asyncio
async def test_collection_summaries_batches_queries(monkeypatch):
    calls = []

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def __await__(self):
            async def done():
                return self.rows

            return done().__await__()

    plan = SimpleNamespace(
        id="p1",
        topology_id="top-1",
        enabled=True,
        schedule_cron="0 0 2 * * *",
        last_run_id="run-1",
        items=[SimpleNamespace(enabled=True)],
    )
    run = SimpleNamespace(
        id="run-1",
        state="SUCCEEDED",
        result="changed",
        finished_at=None,
        error_code=None,
    )

    class PlanModel:
        @classmethod
        def filter(cls, **kwargs):
            calls.append(("plan", kwargs))
            return Query([plan])

    class RunModel:
        @classmethod
        def filter(cls, **kwargs):
            calls.append(("run", kwargs))
            return Query([run])

    monkeypatch.setattr(plans, "TopologyCollectionPlan", PlanModel)
    monkeypatch.setattr(plans, "TopologyCollectionRun", RunModel)
    result = await plans.collection_summaries("tenant-a", ["top-1"])
    assert len(calls) == 2
    assert calls[0][1]["topology_id__in"] == ["top-1"]
    assert calls[1][1]["id__in"] == ["run-1"]
    assert result["top-1"]["last_run"]["id"] == "run-1"
