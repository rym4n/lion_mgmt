"""Focused HTTP adapter tests for explicit legacy-to-v2 upgrades."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import orjson
import pytest

from services.network_topology import upgrade_views
from services.network_topology.legacy import (
    UPGRADE_REVISION_CONFLICT,
    LegacyUpgradeError,
)

NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _enable_v2_ingress(monkeypatch):
    monkeypatch.setattr(upgrade_views.settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)


def _request(body=None, tenant_id="tenant-1"):
    return SimpleNamespace(
        json=_payload() if body is None else body,
        ctx=SimpleNamespace(session={"tenant_id": tenant_id, "sub": "operator-1"}),
    )


def _payload():
    return {
        "id": "topology-1",
        "expected_legacy_revision": 2,
        "confirm_non_empty_legacy_conversion": True,
    }


def _legacy_rows():
    topology = SimpleNamespace(
        id="topology-1",
        tenant_id="tenant-1",
        revision=2,
        current_graph_revision=2,
        current_graph_version_row_id="version-2",
        document_schema_version="legacy/v1",
        generation_source="manual",
    )
    version = SimpleNamespace(
        id="version-2",
        topology_id="topology-1",
        tenant_id="tenant-1",
        version=2,
        document_schema_version="legacy/v1",
    )
    return topology, version


class IntentRepository:
    def __init__(self):
        self.topology, self.version = _legacy_rows()
        self.intents = []
        self.lookups = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def lock_topology(self, tenant_id, topology_id, connection):
        del connection
        self.lookups.append((tenant_id, topology_id))
        if (tenant_id, topology_id) == (self.topology.tenant_id, self.topology.id):
            return self.topology
        return None

    async def lock_current_version(self, topology, connection):
        del topology, connection
        return self.version

    async def lock_active_intents(self, topology, connection):
        del topology, connection
        return [row for row in self.intents if row.status == "ACTIVE"]

    async def create_intent(self, values, connection):
        del connection
        intent = SimpleNamespace(
            id=f"intent-{len(self.intents) + 1}", deleted=False, **values
        )
        self.intents.append(intent)
        return intent

    async def save_intent(self, intent, connection):
        del intent, connection


@pytest.mark.asyncio
async def test_route_checks_topology_update_permission(monkeypatch):
    from libs.security import authorization
    from services.network_topology import views

    checked = []

    async def check_permissions(_request, *permissions, refresh=False):
        checked.append((permissions, refresh))
        return True

    async def adapter(_request):
        return "ok"

    monkeypatch.setattr(authorization, "check_permissions", check_permissions)
    monkeypatch.setattr(views.upgrade_views, "upgrade_to_v2", adapter)

    assert await views.upgrade_to_v2(_request()) == "ok"
    assert checked == [(("network_topology:u",), True)]


@pytest.mark.asyncio
async def test_adapter_rejects_unknown_request_fields_before_domain_call(monkeypatch):
    called = False

    async def unexpected(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(upgrade_views, "create_upgrade_intent", unexpected)
    payload = {**_payload(), "unknown": True}

    response = await upgrade_views.upgrade_to_v2(_request(payload), clock=lambda: NOW)

    assert response.status == 400
    assert called is False


@pytest.mark.asyncio
async def test_adapter_maps_expected_legacy_revision_conflict_to_409(monkeypatch):
    async def conflict(*args, **kwargs):
        raise LegacyUpgradeError(UPGRADE_REVISION_CONFLICT)

    monkeypatch.setattr(upgrade_views, "create_upgrade_intent", conflict)

    response = await upgrade_views.upgrade_to_v2(_request(), clock=lambda: NOW)

    assert response.status == 409
    assert "legacy" in orjson.loads(response.body)["message"]


@pytest.mark.asyncio
async def test_adapter_replaces_active_intent_and_returns_frozen_response():
    repository = IntentRepository()

    first = await upgrade_views.upgrade_to_v2(
        _request(), repository=repository, clock=lambda: NOW
    )
    second = await upgrade_views.upgrade_to_v2(
        _request(), repository=repository, clock=lambda: NOW
    )

    first_body = orjson.loads(first.body)
    second_body = orjson.loads(second.body)
    assert first.status == second.status == 200
    assert first_body["data"]["status"] == "ACTIVE"
    assert second_body["data"] == {
        "upgrade_intent_id": "intent-2",
        "status": "ACTIVE",
        "expected_legacy_revision": 2,
        "expires_at": "2026-07-30T08:30:00Z",
    }
    assert [intent.status for intent in repository.intents] == ["CANCELLED", "ACTIVE"]


@pytest.mark.asyncio
async def test_adapter_scopes_upgrade_lookup_to_session_tenant():
    repository = IntentRepository()

    response = await upgrade_views.upgrade_to_v2(
        _request(tenant_id="tenant-2"), repository=repository, clock=lambda: NOW
    )

    assert response.status == 404
    assert repository.lookups == [("tenant-2", "topology-1")]
    assert repository.intents == []


@pytest.mark.asyncio
async def test_adapter_obeys_dynamic_v2_ingress_kill_switch(monkeypatch):
    monkeypatch.setattr(upgrade_views.settings, "TOPOLOGY_V2_INGRESS_ENABLED", False)

    response = await upgrade_views.upgrade_to_v2(_request(), clock=lambda: NOW)

    assert response.status == 503
