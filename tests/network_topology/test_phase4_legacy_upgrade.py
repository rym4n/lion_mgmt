"""Task 4.7 legacy upgrade intent tests.

The repositories below simulate transaction rollback and lock ordering.  They
prove the CAS protocol, but they are not PostgreSQL concurrency tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.network_topology.compatibility_manager import (
    DEFAULT_TOPOLOGY_GRAPH,
    DEFAULT_TOPOLOGY_LAYOUT,
    is_blank_legacy_topology,
    transition_first_v2_publish,
)
from services.network_topology.legacy import (
    LEGACY_CONVERSION_REQUIRED,
    UPGRADE_INTENT_INVALID,
    UPGRADE_PERMISSION_DENIED,
    UPGRADE_REVISION_CONFLICT,
    LegacyUpgradeError,
    UpgradeAuthority,
    UpgradeIntentRequest,
    cancel_upgrade_intent,
    create_upgrade_intent,
    expire_upgrade_intents,
    publish_upgrade_graph,
    validate_manifest_upgrade,
)
from services.network_topology.publishing import (
    PublicationRequest,
    PublishingError,
)


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


class Row(SimpleNamespace):
    async def save(self, **kwargs):
        del kwargs


def _legacy_graph(title="Core switch"):
    graph = deepcopy(DEFAULT_TOPOLOGY_GRAPH)
    graph["nodes"][0]["data"]["title"] = title
    return graph


def _legacy_rows(*, blank=False):
    revision = 1 if blank else 2
    graph = deepcopy(DEFAULT_TOPOLOGY_GRAPH) if blank else _legacy_graph()
    layout = deepcopy(DEFAULT_TOPOLOGY_LAYOUT)
    topology = Row(
        id="topology-1",
        tenant_id="tenant-1",
        name="Legacy topology",
        revision=revision,
        resource_revision=revision,
        publication_revision=0 if blank else 1,
        legacy_write_generation=0 if blank else 1,
        current_graph_revision=revision,
        current_graph_version_row_id=f"legacy-version-{revision}",
        current_snapshot_row_id=None,
        current_analysis_run_row_id=None,
        current_layout_set_row_id=None,
        current_layout_set_revision=0,
        topology_profile_revision=1,
        root_scope_id="scope:organization:root",
        last_published_collection_sequence=0,
        document_schema_version="legacy/v1",
        generation_source="manual",
        graph_hash=None,
        fact_hash=None,
        topology_json=graph,
        layout_json=layout,
        deleted=False,
    )
    version = Row(
        id=topology.current_graph_version_row_id,
        topology_id=topology.id,
        tenant_id=topology.tenant_id,
        version=revision,
        name=topology.name,
        topology_json=deepcopy(graph),
        layout_json=deepcopy(layout),
        change_type="create" if blank else "manual_save",
        document_schema_version="legacy/v1",
        deleted=False,
    )
    return topology, version


class IntentRepository:
    def __init__(self, *, blank=False):
        self.topology, self.version = _legacy_rows(blank=blank)
        self.intents = []
        self.lock_events = []
        self._next_id = 1

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy((self.topology, self.version, self.intents, self._next_id))
        try:
            yield self
        except Exception:
            self.topology, self.version, self.intents, self._next_id = before
            raise

    async def lock_topology(self, tenant_id, topology_id, connection):
        del connection
        self.lock_events.append("topology")
        if (tenant_id, topology_id) == (self.topology.tenant_id, self.topology.id):
            return self.topology
        return None

    async def lock_current_version(self, topology, connection):
        del connection
        self.lock_events.append("legacy_version")
        if self.version.id == topology.current_graph_version_row_id:
            return self.version
        return None

    async def lock_active_intents(self, topology, connection):
        del connection
        self.lock_events.append("active_intents")
        return [row for row in self.intents if row.status == "ACTIVE"]

    async def create_intent(self, values, connection):
        del connection
        row = Row(id=f"intent-{self._next_id}", deleted=False, **values)
        self._next_id += 1
        self.intents.append(row)
        return row

    async def lock_intent(self, tenant_id, topology_id, intent_id, connection):
        del connection
        self.lock_events.extend(("topology", "intent"))
        for row in self.intents:
            if (row.tenant_id, row.topology_row_id, row.id) == (
                tenant_id,
                topology_id,
                intent_id,
            ):
                return self.topology, row
        return self.topology, None

    async def save_intent(self, intent, connection):
        del intent, connection

    async def lock_expirable_intents(self, now, connection):
        del connection
        return [
            row
            for row in self.intents
            if row.status == "ACTIVE" and row.expires_at <= now
        ]


def _authority(*permissions):
    return UpgradeAuthority("tenant-1", "operator-1", frozenset(permissions))


def _intent_request(revision=2):
    return UpgradeIntentRequest(
        "topology-1", revision, NOW + timedelta(minutes=30)
    )


@pytest.mark.asyncio
async def test_upgrade_requires_update_permission_and_exact_legacy_revision():
    repo = IntentRepository()
    with pytest.raises(LegacyUpgradeError, match=UPGRADE_PERMISSION_DENIED):
        await create_upgrade_intent(
            _authority("network_topology:r"), _intent_request(), repo, clock=lambda: NOW
        )
    with pytest.raises(LegacyUpgradeError, match=UPGRADE_REVISION_CONFLICT):
        await create_upgrade_intent(
            _authority("network_topology:u"), _intent_request(1), repo, clock=lambda: NOW
        )
    assert repo.intents == []


@pytest.mark.asyncio
async def test_new_intent_replaces_the_only_active_intent_and_freezes_version():
    repo = IntentRepository()
    first = await create_upgrade_intent(
        _authority("network_topology:u"), _intent_request(), repo, clock=lambda: NOW
    )
    second = await create_upgrade_intent(
        _authority("network_topology:u"), _intent_request(), repo, clock=lambda: NOW
    )
    assert (first.status, first.cancelled_by) == ("CANCELLED", "operator-1")
    assert second.status == "ACTIVE"
    assert second.legacy_graph_version_row_id == repo.version.id
    assert second.expected_legacy_revision == repo.topology.revision
    assert [row.status for row in repo.intents] == ["CANCELLED", "ACTIVE"]


@pytest.mark.asyncio
async def test_expiration_is_terminal_and_a_fresh_intent_can_be_created():
    repo = IntentRepository()
    old = await create_upgrade_intent(
        _authority("network_topology:u"), _intent_request(), repo, clock=lambda: NOW
    )
    count = await expire_upgrade_intents(
        repo, clock=lambda: NOW + timedelta(hours=1)
    )
    fresh = await create_upgrade_intent(
        _authority("network_topology:u"),
        UpgradeIntentRequest("topology-1", 2, NOW + timedelta(hours=2)),
        repo,
        clock=lambda: NOW + timedelta(hours=1),
    )
    assert count == 1
    assert old.status == "EXPIRED"
    assert fresh.status == "ACTIVE"


def test_blank_sentinel_is_exact_and_non_blank_requires_conversion():
    blank_topology, blank_version = _legacy_rows(blank=True)
    nonblank_topology, nonblank_version = _legacy_rows(blank=False)
    assert is_blank_legacy_topology(blank_topology, blank_version) is True
    assert is_blank_legacy_topology(nonblank_topology, nonblank_version) is False
    with pytest.raises(LegacyUpgradeError, match=LEGACY_CONVERSION_REQUIRED):
        validate_manifest_upgrade(nonblank_topology, nonblank_version, None, NOW)


def _facts():
    return {
        "schema_version": "aioe.network-topology.facts/v1",
        "snapshot_id": "snapshot-1",
        "analysis_run_id": "run-1",
        **{name: [] for name in (
            "devices", "device_groups", "network_contexts", "interfaces",
            "segments", "adjacencies", "routes", "routing_relationships",
            "tunnels", "memberships", "gateways", "security_boundaries",
            "wan_connections", "unresolved_references", "provenance_catalog",
        )},
    }


def _graph():
    return {
        "schema_version": "aioe.network-topology.graph/v2",
        "root_scope_id": "scope:organization:root",
        "scopes": [{
            "id": "scope:organization:root",
            "parent_scope_id": None,
            "scope_type": "organization",
            "name": "Organization",
            "summary": {
                "key_device_count": 0,
                "network_segment_count": 0,
                "child_scope_count": 0,
                "cross_scope_relation_count": 0,
            },
            "provenance": [{
                "type": "derived", "rule_id": "profile.root/v1",
                "profile_revision": 1, "member_ids": ["snapshot-1"],
            }],
        }],
        "entities": [],
        "relations": [],
    }


def _profile():
    return {
        "schema_version": "aioe.network-topology.profile/v1",
        "root_scope_seed": "scope:organization:root",
        "grouping_order": ["organization", "site"],
        "key_device_roles": ["firewall", "router"],
        "excluded_asset_types": ["host"],
        "important_tags": ["topology.important"],
        "explicit_asset_exceptions": [],
        "access_device_policy": "collapse",
        "endpoint_policy": "exclude",
        "point_to_point_subnet_policy": "collapse_into_link",
        "default_layers": ["physical", "l3"],
        "max_depth": 8,
        "max_children_per_view": 200,
        "max_collection_span_seconds": 3600,
        "max_observation_age_seconds": 3600,
        "overflow_strategy": "deterministic_bucket",
    }


class PublicationRepository:
    def __init__(self, intent, *, fail_create=False, blank=False):
        self.topology, legacy_version = _legacy_rows(blank=blank)
        self.versions = [legacy_version]
        self.intent = intent
        self.fail_create = fail_create
        self.lock_events = []
        self.run = Row(
            id="run-row-1", state="RUNNING", stage="PUBLISHING",
            lease_owner="worker-1", fencing_token=7,
            expected_current_graph_revision=self.topology.current_graph_revision,
            expected_publication_revision=self.topology.publication_revision,
        )
        self.manifest = Row(
            id="manifest-row-1",
            status="COMMITTED",
            upgrade_intent_row_id=getattr(intent, "id", None),
            upgrade_intent_row=intent,
            committed_snapshot_row_id="snapshot-row-1",
            committed_analysis_run_row_id="run-row-1",
        )
        self.snapshot = Row(
            id="snapshot-row-1", collection_sequence=1, profile_revision=1,
            target_manifest_row_id=self.manifest.id,
            target_manifest_row=self.manifest,
        )
        self.profile = Row(id="profile-row-1", revision=1)

    @asynccontextmanager
    async def transaction(self):
        rows = tuple(
            row for row in (self.topology, self.run, self.intent) if row is not None
        )
        before = [deepcopy(row.__dict__) for row in rows]
        version_count = len(self.versions)
        try:
            yield SimpleNamespace()
        except Exception:
            for row, state in zip(rows, before, strict=True):
                row.__dict__.clear()
                row.__dict__.update(state)
            del self.versions[version_count:]
            raise

    async def publication_base(self, request):
        del request
        return None

    async def lock_publication(self, request, connection):
        del request, connection
        self.lock_events.append("topology")
        return self.topology, self.run, self.snapshot, self.profile

    async def lock_layout_set(self, topology, connection):
        del topology, connection
        return None

    async def lock_upgrade_intent(self, request, connection):
        del request, connection
        self.lock_events.append("intent")
        return self.intent

    async def current_graph(self, topology, connection):
        del topology, connection
        return None

    async def max_version(self, topology, connection):
        del topology, connection
        return max(row.version for row in self.versions)

    async def create_version(self, values, connection):
        del connection
        if self.fail_create:
            raise RuntimeError("version create failed")
        row = Row(id=f"version-{len(self.versions) + 1}", **values)
        self.versions.append(row)
        return row

    async def save_publication(self, topology, run, values, connection):
        del connection
        topology.__dict__.update(values)
        run.state = "SUCCEEDED"

    async def supersede(self, run, reason, connection):
        del connection
        run.state = "SUPERSEDED"
        run.error_code = reason

    async def add_audit(self, values, connection):
        del values, connection

    async def transition_schema(self, request, topology, run, intent, connection, now):
        del run
        await transition_first_v2_publish(
            connection=connection,
            topology=topology,
            legacy_version=self.versions[0],
            expected_revision=request.expected_graph_revision,
            expected_publication_revision=request.expected_publication_revision,
            expected_profile_revision=request.profile_revision,
            analysis_state="SUCCEEDED",
            published_by=request.actor,
            published_at=now,
            upgrade_intent=intent,
        )


def _publication_request(intent_id, *, revision=2, publication_revision=1):
    return PublicationRequest(
        tenant_id="tenant-1", topology_id="topology-1",
        analysis_run_id="run-row-1", snapshot_id="snapshot-row-1",
        profile_revision_id="profile-row-1", lease_owner="worker-1",
        fencing_token=7, collection_sequence=1, expected_graph_revision=revision,
        expected_publication_revision=publication_revision,
        facts=_facts(), graph=_graph(),
        profile=_profile(), actor="worker-1", upgrade_intent_id=intent_id,
        require_non_empty=False,
    )


async def _active_intent():
    repo = IntentRepository()
    return await create_upgrade_intent(
        _authority("network_topology:u"), _intent_request(), repo, clock=lambda: NOW
    )


@pytest.mark.asyncio
async def test_cancel_wins_publish_race_and_old_manifest_run_never_publish():
    intent = await _active_intent()
    lifecycle = IntentRepository()
    lifecycle.topology, lifecycle.version = _legacy_rows(blank=False)
    lifecycle.intents = [intent]
    await cancel_upgrade_intent(
        _authority("network_topology:u"), "topology-1", intent.id,
        "operator cancelled", lifecycle, clock=lambda: NOW,
    )
    publication = PublicationRepository(intent)
    with pytest.raises(PublishingError, match=UPGRADE_INTENT_INVALID):
        await publish_upgrade_graph(
            _publication_request(intent.id), publication, clock=lambda: NOW
        )
    assert publication.topology.document_schema_version == "legacy/v1"
    assert publication.run.state == "RUNNING"


@pytest.mark.asyncio
async def test_publish_wins_cancel_race_and_consumed_intent_is_terminal():
    intent = await _active_intent()
    publication = PublicationRepository(intent)
    await publish_upgrade_graph(
        _publication_request(intent.id), publication, clock=lambda: NOW
    )
    lifecycle = IntentRepository()
    lifecycle.topology = publication.topology
    lifecycle.intents = [intent]
    with pytest.raises(LegacyUpgradeError, match=UPGRADE_INTENT_INVALID):
        await cancel_upgrade_intent(
            _authority("network_topology:u"), "topology-1", intent.id,
            "too late", lifecycle, clock=lambda: NOW,
        )
    assert intent.status == "CONSUMED"


@pytest.mark.asyncio
async def test_manifest_and_analysis_run_cannot_switch_to_replacement_intent():
    old = await _active_intent()
    old.status = "CANCELLED"
    replacement = deepcopy(old)
    replacement.id = "intent-replacement"
    replacement.status = "ACTIVE"
    replacement.cancelled_at = None
    replacement.cancelled_by = None
    publication = PublicationRepository(replacement)
    publication.manifest.upgrade_intent_row_id = old.id
    publication.manifest.upgrade_intent_row = old
    with pytest.raises(PublishingError, match=UPGRADE_INTENT_INVALID):
        await publish_upgrade_graph(
            _publication_request(replacement.id), publication, clock=lambda: NOW
        )
    assert publication.versions[-1].document_schema_version == "legacy/v1"


@pytest.mark.asyncio
async def test_failed_upgrade_rolls_back_and_retry_preserves_legacy_history():
    intent = await _active_intent()
    publication = PublicationRepository(intent, fail_create=True)
    request = _publication_request(intent.id)
    with pytest.raises(RuntimeError, match="version create failed"):
        await publish_upgrade_graph(request, publication, clock=lambda: NOW)
    assert publication.topology.document_schema_version == "legacy/v1"
    assert intent.status == "ACTIVE"
    assert len(publication.versions) == 1

    publication.fail_create = False
    result = await publish_upgrade_graph(request, publication, clock=lambda: NOW)
    assert result.status == "SUCCEEDED"
    assert publication.topology.document_schema_version == "graph/v2"
    assert intent.status == "CONSUMED"
    assert [row.document_schema_version for row in publication.versions] == [
        "legacy/v1", "graph/v2"
    ]
    legacy = publication.versions[0]
    assert legacy.topology_json["nodes"][0]["data"]["title"] == "Core switch"
    assert "provenance" not in legacy.topology_json["nodes"][0]


@pytest.mark.asyncio
async def test_blank_first_publish_needs_no_intent_and_keeps_legacy_version():
    publication = PublicationRepository(None, blank=True)
    result = await publish_upgrade_graph(
        _publication_request(None, revision=1, publication_revision=0),
        publication,
        clock=lambda: NOW,
    )
    assert result.status == "SUCCEEDED"
    assert publication.topology.document_schema_version == "graph/v2"
    assert [row.document_schema_version for row in publication.versions] == [
        "legacy/v1", "graph/v2"
    ]
    assert publication.versions[0].topology_json == DEFAULT_TOPOLOGY_GRAPH


@pytest.mark.asyncio
async def test_v1_edit_after_intent_supersedes_publication_without_consuming_it():
    intent = await _active_intent()
    publication = PublicationRepository(intent)
    publication.topology.current_graph_revision = 3
    publication.topology.publication_revision = 2
    result = await publish_upgrade_graph(
        _publication_request(intent.id), publication, clock=lambda: NOW
    )
    assert result.status == "SUPERSEDED"
    assert publication.run.state == "SUPERSEDED"
    assert intent.status == "ACTIVE"
    assert publication.topology.document_schema_version == "legacy/v1"
