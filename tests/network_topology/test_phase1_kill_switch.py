from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import orjson
import pytest

import settings
from services.network_topology import compatibility_manager as compatibility
from services.network_topology import manifest, views
from tests.network_topology.test_phase1_compatibility import _topology, _version


def test_legacy_sync_remains_enabled_by_default():
    assert settings.TOPOLOGY_LEGACY_SYNC_ENABLED is True


def test_publish_transaction_fails_closed_before_opening_transaction(monkeypatch):
    opened = False

    def transaction():
        nonlocal opened
        opened = True
        raise AssertionError("disabled publish must not open a transaction")

    monkeypatch.setattr(settings, "TOPOLOGY_V2_PUBLISH_ENABLED", False)
    monkeypatch.setattr(compatibility, "in_transaction", transaction)

    with pytest.raises(compatibility.CompatibilityError) as raised:
        asyncio.run(
            compatibility.publish_first_v2_transaction(
                tenant_id="tenant-1",
                topology_id="topology-1",
                legacy_version_id="version-1",
                expected_revision=1,
                expected_publication_revision=0,
                expected_profile_revision=1,
                analysis_state="SUCCEEDED",
                published_by="worker-1",
                published_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            )
        )

    assert raised.value.code == compatibility.TOPOLOGY_PUBLISH_DISABLED
    assert opened is False


def test_manifest_ingress_service_guard_is_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", False)
    with pytest.raises(manifest.ManifestError) as raised:
        manifest._require_ingress()
    assert raised.value.code == manifest.MANIFEST_INGRESS_DISABLED


def test_manifest_ingress_service_guard_reads_runtime_settings(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)
    assert manifest._require_ingress() is None

    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", False)
    with pytest.raises(manifest.ManifestError) as raised:
        manifest._require_ingress()
    assert raised.value.code == manifest.MANIFEST_INGRESS_DISABLED


def test_view_capabilities_read_runtime_settings(monkeypatch):
    topology = SimpleNamespace(
        document_schema_version=compatibility.LEGACY_SCHEMA_VERSION,
        generation_source=compatibility.MANUAL_GENERATION_SOURCE,
    )
    monkeypatch.setattr(settings, "TOPOLOGY_V2_VIEW_ENABLED", False)
    assert views._capabilities(topology)["view"] is False

    monkeypatch.setattr(settings, "TOPOLOGY_V2_VIEW_ENABLED", True)
    assert views._capabilities(topology)["view"] is True


def test_manifest_ingress_guard_runs_before_request_parsing(monkeypatch):
    parsed = False

    def parse(_request):
        nonlocal parsed
        parsed = True
        raise AssertionError("disabled ingress must not parse the request")

    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", False)
    monkeypatch.setattr(views, "_manifest_request_data", parse)

    response = asyncio.run(
        inspect.unwrap(views.issue_topology_manifest)(
            SimpleNamespace(
                ctx=SimpleNamespace(
                    session={"tenant_id": "tenant-1", "sub": "user-1"}
                )
            )
        )
    )

    payload = orjson.loads(response.body)
    assert response.status == 503
    assert payload["data"]["error_code"] == manifest.MANIFEST_INGRESS_DISABLED
    assert parsed is False


def test_snapshot_ingress_guard_runs_before_request_parsing(monkeypatch):
    parsed = False

    async def allow(*_args):
        return True

    def parse(_request):
        nonlocal parsed
        parsed = True
        raise AssertionError("disabled ingress must not parse the request")

    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", False)
    monkeypatch.setattr(views, "check_permissions", allow)
    monkeypatch.setattr(views, "_request_data", parse)

    response = asyncio.run(views._sync_v2_topology_snapshot(SimpleNamespace()))

    assert response.status == 503
    assert "入口未启用" in orjson.loads(response.body)["message"]
    assert parsed is False


@pytest.mark.parametrize(
    "schema,source",
    (
        (compatibility.V2_SCHEMA_VERSION, compatibility.PLAYBOOK_GENERATION_SOURCE),
        ("future/v3", "future-worker"),
    ),
)
def test_view_guard_fails_closed_for_v2_and_unknown_runtime_schema(
    monkeypatch, schema, source
):
    topology = SimpleNamespace(
        document_schema_version=schema,
        generation_source=source,
    )
    monkeypatch.setattr(settings, "TOPOLOGY_V2_VIEW_ENABLED", False)
    assert views._v2_view_disabled(topology) is True

    monkeypatch.setattr(settings, "TOPOLOGY_V2_VIEW_ENABLED", True)
    assert views._v2_view_disabled(topology) is False


def test_legacy_sync_guard_runs_before_topology_read(monkeypatch):
    read_topology = False

    async def allow(*_args):
        return True

    async def topology(*_args, **_kwargs):
        nonlocal read_topology
        read_topology = True

    request = SimpleNamespace(
        json={"id": "topology-1", "config": "hostname core"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "user-1"}),
    )
    monkeypatch.setattr(views, "check_permissions", allow)
    monkeypatch.setattr(views, "_tenant_topology", topology)
    monkeypatch.setattr(settings, "TOPOLOGY_LEGACY_SYNC_ENABLED", False)

    response = asyncio.run(inspect.unwrap(views.sync_topology_config)(request))

    assert response.status == 503
    assert "入口已关闭" in orjson.loads(response.body)["message"]
    assert read_topology is False


class _LockedQuery:
    def __init__(self, row, event, events):
        self.row = row
        self.event = event
        self.events = events

    def using_db(self, connection):
        assert connection == "publish-connection"
        return self

    def select_for_update(self):
        self.events.append(self.event)
        return self

    async def first(self):
        return self.row


def _locked_model(row, event, events):
    return SimpleNamespace(
        filter=lambda **_filters: _LockedQuery(row, event, events)
    )


@pytest.mark.parametrize("state", ("FAILED", "CANCELLED", "SUPERSEDED"))
def test_publish_transaction_locks_rows_and_terminal_failure_keeps_legacy(
    monkeypatch, state
):
    events = []
    topology = _topology()
    version = _version()

    @asynccontextmanager
    async def transaction():
        events.append("transaction")
        yield "publish-connection"

    monkeypatch.setattr(settings, "TOPOLOGY_V2_PUBLISH_ENABLED", True)
    monkeypatch.setattr(compatibility, "in_transaction", transaction)
    monkeypatch.setattr(
        compatibility,
        "NetworkTopology",
        _locked_model(topology, "topology-lock", events),
    )
    monkeypatch.setattr(
        compatibility,
        "NetworkTopologyVersion",
        _locked_model(version, "version-lock", events),
    )
    before = deepcopy((topology.document_schema_version, topology.generation_source))

    with pytest.raises(compatibility.CompatibilityError) as raised:
        asyncio.run(
            compatibility.publish_first_v2_transaction(
                tenant_id="tenant-1",
                topology_id="topology-1",
                legacy_version_id="version-1",
                expected_revision=1,
                expected_publication_revision=0,
                expected_profile_revision=1,
                analysis_state=state,
                published_by="worker-1",
                published_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            )
        )

    assert raised.value.code == compatibility.TOPOLOGY_PUBLISH_STATE_INVALID
    assert events == ["transaction", "topology-lock", "version-lock"]
    assert (topology.document_schema_version, topology.generation_source) == before
    assert topology.saved == []


def test_successful_publish_save_failure_restores_legacy_pair():
    topology = _topology()

    async def fail_save(**_kwargs):
        raise RuntimeError("database write failed")

    topology.save = fail_save
    with pytest.raises(compatibility.CompatibilityError) as raised:
        asyncio.run(
            compatibility.transition_first_v2_publish(
                connection="publish-connection",
                topology=topology,
                legacy_version=_version(),
                expected_revision=1,
                expected_publication_revision=0,
                expected_profile_revision=1,
                analysis_state="SUCCEEDED",
                published_by="worker-1",
                published_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            )
        )

    assert raised.value.code == compatibility.TOPOLOGY_PUBLISH_FAILED
    assert topology.document_schema_version == compatibility.LEGACY_SCHEMA_VERSION
    assert topology.generation_source == compatibility.MANUAL_GENERATION_SOURCE


def test_enabled_publish_transaction_switches_pair_under_locks(monkeypatch):
    events = []
    topology = _topology()

    @asynccontextmanager
    async def transaction():
        events.append("transaction")
        yield "publish-connection"

    monkeypatch.setattr(settings, "TOPOLOGY_V2_PUBLISH_ENABLED", True)
    monkeypatch.setattr(compatibility, "in_transaction", transaction)
    monkeypatch.setattr(
        compatibility,
        "NetworkTopology",
        _locked_model(topology, "topology-lock", events),
    )
    monkeypatch.setattr(
        compatibility,
        "NetworkTopologyVersion",
        _locked_model(_version(), "version-lock", events),
    )

    fields = asyncio.run(
        compatibility.publish_first_v2_transaction(
            tenant_id="tenant-1",
            topology_id="topology-1",
            legacy_version_id="version-1",
            expected_revision=1,
            expected_publication_revision=0,
            expected_profile_revision=1,
            analysis_state="SUCCEEDED",
            published_by="worker-1",
            published_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        )
    )

    assert fields == ("document_schema_version", "generation_source")
    assert events == ["transaction", "topology-lock", "version-lock"]
    assert topology.document_schema_version == compatibility.V2_SCHEMA_VERSION
    assert topology.generation_source == compatibility.PLAYBOOK_GENERATION_SOURCE
    assert topology.saved[0]["using_db"] == "publish-connection"
