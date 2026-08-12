"""Task 4.6 publication tests using a transactional in-memory repository.

These tests prove CAS and rollback semantics, but are not real-database concurrency tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from services.network_topology.publishing import (
    FailureInjector,
    PublicationRequest,
    PublishingError,
    publish_graph,
    sweep_publication_objects,
)
from services.network_topology.versions import build_graph_diff, validate_publication


def _facts():
    return {
        "schema_version": "aioe.network-topology.facts/v1",
        "snapshot_id": "snapshot-1",
        "analysis_run_id": "run-1",
        "devices": [],
        "device_groups": [],
        "network_contexts": [],
        "interfaces": [],
        "segments": [],
        "adjacencies": [],
        "routes": [],
        "routing_relationships": [],
        "tunnels": [],
        "memberships": [],
        "gateways": [],
        "security_boundaries": [],
        "wan_connections": [],
        "unresolved_references": [],
        "provenance_catalog": [],
    }


def _graph(name="Synthetic Organization", *, entity=None, relation=None):
    graph = {
        "schema_version": "aioe.network-topology.graph/v2",
        "root_scope_id": "scope:organization:root",
        "scopes": [
            {
                "id": "scope:organization:root",
                "parent_scope_id": None,
                "scope_type": "organization",
                "name": name,
                "summary": {
                    "key_device_count": int(entity is not None),
                    "network_segment_count": 0,
                    "child_scope_count": 0,
                    "cross_scope_relation_count": 0,
                },
                "provenance": [
                    {
                        "type": "derived",
                        "rule_id": "profile.root/v1",
                        "profile_revision": 1,
                        "member_ids": ["snapshot-1"],
                    }
                ],
            }
        ],
        "entities": [] if entity is None else [entity],
        "relations": [] if relation is None else [relation],
    }
    return graph


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


def _device(identifier="entity:router-a", name="Router A"):
    return {
        "id": identifier,
        "kind": "device",
        "scope_id": "scope:organization:root",
        "scope_memberships": [],
        "name": name,
        "role": "router",
        "representation": "device",
        "importance": "important",
        "importance_reasons": ["role:router"],
        "attributes": {},
        "provenance": [
            {
                "type": "derived",
                "rule_id": "importance/v1",
                "profile_revision": 1,
                "member_ids": ["snapshot-1"],
            }
        ],
    }


@dataclass
class _Object:
    id: str
    kind: str
    status: str = "READY"
    referenced: bool = False
    deleted: bool = False


class MemoryStore:
    def __init__(self, injector=None):
        self.injector = injector or FailureInjector()
        self.objects = []

    async def prepare(self, tenant_id, kind, document):
        del tenant_id, document
        self.injector.hit(f"{kind}_put_before")
        row = _Object(f"object-{len(self.objects) + 1}", kind, "STAGING")
        self.objects.append(row)
        self.injector.hit(f"{kind}_put_after")
        self.injector.hit(f"{kind}_ready_before")
        row.status = "READY"
        self.injector.hit(f"{kind}_ready_after")
        return row

    async def activate(self, row, connection):
        assert row.status == "READY"
        previous = row.status, row.referenced
        if hasattr(connection, "undo"):
            connection.undo.append(lambda: _restore_object(row, previous))
        row.status = "LIVE"
        row.referenced = True

    async def delete(self, row):
        self.injector.hit("retention_delete_before")
        row.deleted = True
        self.injector.hit("retention_delete_after")


class MemoryRepository:
    """Transactional double; it does not prove PostgreSQL lock behavior."""

    def __init__(self):
        self.topology = SimpleNamespace(
            id="topology-1",
            tenant_id="tenant-1",
            name="Topology",
            revision=1,
            publication_revision=0,
            current_graph_revision=0,
            topology_profile_revision=1,
            current_graph_version_row_id=None,
            current_snapshot_row_id=None,
            current_analysis_run_row_id=None,
            current_layout_set_row_id="layout-set-1",
            current_layout_set_revision=1,
            last_published_collection_sequence=0,
            document_schema_version="graph/v2",
            generation_source="playbook",
            graph_hash=None,
            fact_hash=None,
            topology_json={},
            layout_json={},
        )
        self.run = SimpleNamespace(
            id="run-row-1",
            state="RUNNING",
            stage="PUBLISHING",
            lease_owner="worker-1",
            fencing_token=7,
            expected_current_graph_revision=0,
            expected_publication_revision=0,
            target_profile_revision_row_id="profile-row-1",
            fact_graph_payload_object_row_id=None,
            graph_hash=None,
            facts_hash=None,
        )
        self.snapshot = SimpleNamespace(
            id="snapshot-row-1", collection_sequence=1, profile_revision=1
        )
        self.profile = SimpleNamespace(id="profile-row-1", revision=1)
        self.layout_set = SimpleNamespace(
            id="layout-set-1",
            layout_set_revision=1,
            scope_items={"scope:organization:root": {"entity:router-a"}},
            root_layout={"entity:router-a": {"x": 4, "y": 8}},
        )
        self.versions = []
        self.audit = []
        self.lock_events = []
        self.injector = FailureInjector()

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy((self.topology, self.run, self.versions, self.audit))
        connection = SimpleNamespace(undo=[])
        try:
            yield connection
            self.injector.hit("db_commit_before")
        except Exception:
            for undo in reversed(connection.undo):
                undo()
            self.topology, self.run, self.versions, self.audit = before
            raise
        self.injector.hit("db_commit_after")

    async def lock_publication(self, request, connection):
        del connection
        self.lock_events.append("topology")
        return self.topology, self.run, self.snapshot, self.profile

    async def lock_layout_set(self, topology, connection):
        del topology, connection
        self.lock_events.append("layout_set")
        return self.layout_set

    async def lock_upgrade_intent(self, request, connection):
        del request, connection
        self.lock_events.append("upgrade_intent")
        return None

    async def max_version(self, topology, connection):
        del topology, connection
        return max((row.version for row in self.versions), default=0)

    async def current_graph(self, topology, connection):
        del topology, connection
        return deepcopy(self.versions[-1].topology_json) if self.versions else None

    async def publication_base(self, request):
        del request
        return deepcopy(self.versions[-1].topology_json) if self.versions else None

    async def create_version(self, values, connection):
        del connection
        row = SimpleNamespace(id=f"version-{len(self.versions) + 1}", **values)
        self.versions.append(row)
        return row

    async def save_publication(self, topology, run, values, connection):
        del connection
        for key, value in values.items():
            setattr(topology, key, value)
        run.state = "SUCCEEDED"

    async def supersede(self, run, reason, connection):
        del connection
        run.state = "SUPERSEDED"
        run.error_code = reason

    async def add_audit(self, values, connection):
        del connection
        self.audit.append(values)


def _request(
    graph=None,
    *,
    sequence=1,
    expected_graph_revision=0,
    run_id="run-row-1",
    snapshot_id="snapshot-row-1",
):
    return PublicationRequest(
        tenant_id="tenant-1",
        topology_id="topology-1",
        analysis_run_id=run_id,
        snapshot_id=snapshot_id,
        profile_revision_id="profile-row-1",
        lease_owner="worker-1",
        fencing_token=7,
        collection_sequence=sequence,
        expected_graph_revision=expected_graph_revision,
        expected_publication_revision=expected_graph_revision,
        facts=_facts(),
        graph=graph or _graph(),
        profile=_profile(),
        actor="worker-1",
        require_non_empty=False,
    )


def _restore_object(row, previous):
    row.status, row.referenced = previous


def test_validation_rejects_cycles_missing_endpoints_and_empty_graph():
    graph = _graph()
    graph["scopes"].append(
        {
            **deepcopy(graph["scopes"][0]),
            "id": "scope:site:a",
            "parent_scope_id": "scope:site:a",
            "scope_type": "site",
            "name": "A",
        }
    )
    with pytest.raises(PublishingError, match="SCOPE_CYCLE"):
        validate_publication(_facts(), graph, _profile(), 1)
    with pytest.raises(PublishingError, match="GRAPH_EMPTY"):
        validate_publication(_facts(), _graph(), _profile(), 1, require_non_empty=True)


def test_diff_summarizes_semantics_by_scope_and_ignores_layout():
    before = _graph(entity=_device())
    after = deepcopy(before)
    after["entities"][0]["name"] = "Router renamed"
    before["layout"] = {"entity:router-a": {"x": 1, "y": 2}}
    after["layout"] = {"entity:router-a": {"x": 99, "y": 88}}
    diff = build_graph_diff(before, after)
    assert diff["totals"] == {"added": 0, "removed": 0, "modified": 1}
    assert diff["scopes"]["scope:organization:root"]["key_devices"]["modified"] == [
        "entity:router-a"
    ]


@pytest.mark.asyncio
async def test_publish_populates_legacy_and_v2_fields_and_activates_objects():
    repo = MemoryRepository()
    store = MemoryStore()
    result = await publish_graph(_request(_graph(entity=_device())), repo, store, inline_threshold=1)
    version = repo.versions[0]
    assert result.created_version is True
    assert (version.name, version.change_type, version.document_schema_version) == (
        "Topology",
        "publish",
        "graph/v2",
    )
    assert version.topology_json["schema_version"].endswith("graph/v2")
    assert version.layout_json == {"entity:router-a": {"x": 4, "y": 8}}
    assert version.graph_hash and version.facts_hash
    assert version.snapshot_row_id == "snapshot-row-1"
    assert version.analysis_run_row_id == "run-row-1"
    assert version.profile_revision_row_id == "profile-row-1"
    assert version.published_layout_set_row_id == "layout-set-1"
    assert [item.status for item in store.objects] == ["LIVE", "LIVE"]
    assert repo.topology.current_graph_version_row_id == version.id
    assert repo.lock_events == ["topology", "layout_set", "upgrade_intent"]


@pytest.mark.asyncio
async def test_a_to_b_to_a_creates_three_time_versions():
    repo = MemoryRepository()
    store = MemoryStore()
    graph_a = _graph("A")
    graph_b = _graph("B")
    for sequence, graph in enumerate((graph_a, graph_b, graph_a), 1):
        repo.snapshot = SimpleNamespace(
            id=f"snapshot-row-{sequence}", collection_sequence=sequence, profile_revision=1
        )
        repo.run.id = f"run-row-{sequence}"
        repo.run.expected_current_graph_revision = sequence - 1
        repo.run.expected_publication_revision = sequence - 1
        repo.run.state = "RUNNING"
        repo.topology.publication_revision = sequence - 1
        await publish_graph(
            _request(
                graph,
                sequence=sequence,
                expected_graph_revision=sequence - 1,
                run_id=f"run-row-{sequence}",
                snapshot_id=f"snapshot-row-{sequence}",
            ),
            repo,
            store,
        )
    assert [row.version for row in repo.versions] == [1, 2, 3]
    assert repo.versions[0].graph_hash == repo.versions[2].graph_hash


@pytest.mark.asyncio
async def test_same_hash_advances_sequence_and_pointers_without_version():
    repo = MemoryRepository()
    store = MemoryStore()
    graph = _graph("A")
    await publish_graph(_request(graph), repo, store)
    repo.snapshot = SimpleNamespace(id="snapshot-row-2", collection_sequence=2, profile_revision=1)
    repo.run.id = "run-row-2"
    repo.run.state = "RUNNING"
    repo.run.expected_current_graph_revision = 1
    repo.run.expected_publication_revision = 1
    repo.topology.publication_revision = 1
    result = await publish_graph(
        _request(
            graph,
            sequence=2,
            expected_graph_revision=1,
            run_id="run-row-2",
            snapshot_id="snapshot-row-2",
        ),
        repo,
        store,
    )
    assert result.created_version is False
    assert len(repo.versions) == 1
    assert repo.topology.last_published_collection_sequence == 2
    assert repo.topology.current_snapshot_row_id == "snapshot-row-2"
    assert repo.topology.current_analysis_run_row_id == "run-row-2"
    assert len(repo.audit) == 2


@pytest.mark.asyncio
async def test_stale_sequence_is_superseded_and_cannot_overwrite_graph():
    repo = MemoryRepository()
    repo.topology.last_published_collection_sequence = 3
    original = deepcopy(repo.topology.topology_json)
    result = await publish_graph(_request(sequence=2), repo, MemoryStore())
    assert result.status == "SUPERSEDED"
    assert repo.run.state == "SUPERSEDED"
    assert repo.topology.topology_json == original
    assert repo.versions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [
        "facts_put_before",
        "facts_put_after",
        "facts_ready_before",
        "facts_ready_after",
        "graph_put_before",
        "graph_put_after",
        "graph_ready_before",
        "graph_ready_after",
        "db_commit_before",
    ],
)
async def test_faults_before_commit_never_publish_a_partial_version(point):
    injector = FailureInjector({point})
    repo = MemoryRepository()
    repo.injector = injector
    store = MemoryStore(injector)
    with pytest.raises(RuntimeError, match=point):
        await publish_graph(_request(), repo, store, inline_threshold=1)
    assert repo.topology.current_graph_version_row_id is None
    assert repo.versions == []
    assert all(item.status != "LIVE" and not item.referenced for item in store.objects)


@pytest.mark.asyncio
async def test_after_commit_fault_is_retriable_and_does_not_duplicate_version():
    repo = MemoryRepository()
    repo.injector = FailureInjector({"db_commit_after"})
    with pytest.raises(RuntimeError, match="db_commit_after"):
        await publish_graph(_request(), repo, MemoryStore())
    assert len(repo.versions) == 1
    repo.injector = FailureInjector()
    repo.run.state = "RUNNING"
    result = await publish_graph(_request(), repo, MemoryStore())
    assert result.created_version is False
    assert len(repo.versions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["retention_delete_before", "retention_delete_after"])
async def test_retention_faults_never_delete_live_or_referenced_objects(point):
    store = MemoryStore(FailureInjector({point}))
    live = _Object("live", "graph", "LIVE", referenced=True)
    orphan = _Object("ready", "graph", "READY", referenced=False)
    store.objects = [live, orphan]
    with pytest.raises(RuntimeError, match=point):
        await sweep_publication_objects(store.objects, store)
    assert live.deleted is False
    if point == "retention_delete_before":
        assert orphan.deleted is False
    else:
        assert orphan.deleted is True
