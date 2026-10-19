from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.network_topology.canonicalization import canonicalize_and_hash_manifest
from services.network_topology.collection import manifest_issue
from services.network_topology.collection.targets import Target


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeout_seconds", "expires_at"),
    [
        (300, "2026-10-19T10:05:00Z"),
        (3600, "2026-10-19T11:00:00Z"),
        (7200, "2026-10-19T12:00:00Z"),
    ],
)
async def test_action_manifest_hash_covers_plan_timeout(
    monkeypatch, timeout_seconds, expires_at
):
    """Changing the default manifest TTL must produce a valid new hash."""
    now = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
    connection = object()

    @asynccontextmanager
    async def transaction():
        yield connection

    plan = SimpleNamespace(
        id="plan-1", tenant_id="tenant-1", topology_id="topology-1",
        run_timeout_seconds=timeout_seconds,
    )
    run = SimpleNamespace(id="run-1", state="PENDING", save=AsyncMock())

    class RunQuery:
        def using_db(self, _connection):
            return self

        def select_for_update(self):
            return self

        async def first(self):
            return run

    monkeypatch.setattr(manifest_issue, "in_transaction", transaction)
    monkeypatch.setattr(
        manifest_issue, "TopologyCollectionRun",
        SimpleNamespace(filter=lambda **_filters: RunQuery()),
    )
    topology = SimpleNamespace(
        topology_profile_revision=1, current_graph_revision=8,
        publication_revision=8,
    )
    monkeypatch.setattr(
        manifest_issue.manifest, "_locked_topology", AsyncMock(return_value=topology)
    )
    monkeypatch.setattr(
        manifest_issue.manifest, "_current_profile",
        AsyncMock(return_value=(SimpleNamespace(revision=1), {})),
    )
    monkeypatch.setattr(
        manifest_issue.manifest, "_upgrade_intent", AsyncMock(return_value=(None, now))
    )
    monkeypatch.setattr(
        manifest_issue.manifest, "_next_sequence", AsyncMock(return_value=1)
    )
    persist = AsyncMock()
    monkeypatch.setattr(manifest_issue.manifest, "_persist_manifest", persist)
    target = Target(
        item_id="item-1", app_name="device-app", app_version="v1", main="main.py",
        action_func="collect", action_name="Collect", action_input={},
        resource_id="resource-1", resource_name="Device", host="10.0.0.1",
        asset_instance_id=None,
    )

    result = await manifest_issue.issue_action_manifest(plan, run, [target], now=now)

    assert result["issued_at"] == "2026-10-19T10:00:00Z"
    assert result["expires_at"] == expires_at
    # Keep the real canonicalizer: it rejects a hash computed for another TTL.
    canonicalize_and_hash_manifest(result)
    assert persist.await_args.args[-2] == result
    assert run.state == "MANIFEST_FROZEN"
    assert run.manifest_id == result["manifest_id"]
    assert run.device_total == 1
    assert run.frozen_targets[0]["resource_id"] == "resource-1"
