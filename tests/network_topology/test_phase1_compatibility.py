from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from tortoise import Tortoise

from models import NetworkTopology, NetworkTopologyVersion
from services.network_topology import compatibility_manager as compatibility
from services.network_topology import contracts, manifest, views
from services.network_topology.canonicalization import canonicalize_json, sha256_uri

TENANT = "tenant-1"
ACTOR = "account-1"
TOPOLOGY_ID = "topology-1"
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self) -> None:
        self.rows = {"topology": [], "version": [], "profile": []}
        self.fail_create: str | None = None
        self.fail_save: str | None = None
        self.transaction_lock = asyncio.Lock()

    def model(self, name: str):
        store = self

        class FakeModel:
            @classmethod
            def filter(cls, **filters):
                return Query(store, name, filters)

            @classmethod
            async def create(cls, **values):
                values.pop("using_db", None)
                if store.fail_create == name:
                    raise RuntimeError(f"{name} write failed")
                row = _created_row(name, values, len(store.rows[name]))
                store.rows[name].append(row)
                return _stored_instance(store, name, row)

        return FakeModel


class Query:
    def __init__(self, store: Store, model: str, filters: dict) -> None:
        self.store = store
        self.model = model
        self.filters = filters

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self

    def limit(self, _value):
        return self

    async def first(self):
        row = next(
            (row for row in self.store.rows[self.model] if _matches(row, self.filters)),
            None,
        )
        return _stored_instance(self.store, self.model, row) if row else None

    async def exists(self) -> bool:
        return any(_matches(row, self.filters) for row in self.store.rows[self.model])

    def __await__(self):
        async def resolve():
            rows = (row for row in self.store.rows[self.model] if _matches(row, self.filters))
            return [_stored_instance(self.store, self.model, row) for row in rows]

        return resolve().__await__()


def _stored_instance(store: Store, name: str, row: dict):
    instance = SimpleNamespace(**deepcopy(row))

    async def save(**kwargs):
        if store.fail_save == name:
            raise RuntimeError(f"{name} save failed")
        _save_instance(store, name, instance, kwargs["update_fields"])

    instance.save = save
    return instance


def _created_row(name: str, values: dict, index: int) -> dict:
    row = deepcopy(values)
    row.setdefault("id", f"{name}-{index + 1}")
    row.setdefault("deleted", False)
    if name == "version":
        topology = row.pop("topology")
        row["topology_id"] = topology.id
    if name == "profile":
        topology = row.pop("topology", row.pop("topology_row", None))
        row["topology_row_id"] = topology.id
    return row


def _matches(row: dict, filters: dict) -> bool:
    return all(row.get(field) == value for field, value in filters.items())


def _save_instance(store: Store, name: str, instance, fields: list[str]) -> None:
    row = next(item for item in store.rows[name] if item["id"] == instance.id)
    for field in fields:
        if field == "utime":
            continue
        value = getattr(instance, field)
        row[field] = deepcopy(value)


def _transaction(store: Store):
    @asynccontextmanager
    async def transaction():
        async with store.transaction_lock:
            snapshot = deepcopy(store.rows)
            try:
                yield object()
            except Exception:
                store.rows = snapshot
                raise

    return transaction


@pytest.fixture
def store(monkeypatch) -> Store:
    value = Store()
    monkeypatch.setattr(compatibility, "NetworkTopology", value.model("topology"))
    monkeypatch.setattr(compatibility, "NetworkTopologyVersion", value.model("version"))
    monkeypatch.setattr(
        compatibility, "TopologyProfileRevision", value.model("profile")
    )
    monkeypatch.setattr(compatibility, "in_transaction", _transaction(value))
    return value


def _topology(**changes):
    values = {
        "id": TOPOLOGY_ID,
        "tenant_id": TENANT,
        "deleted": False,
        "topology_json": deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH),
        "layout_json": deepcopy(compatibility.DEFAULT_TOPOLOGY_LAYOUT),
        "revision": 1,
        "document_schema_version": compatibility.LEGACY_SCHEMA_VERSION,
        "generation_source": compatibility.MANUAL_GENERATION_SOURCE,
        "root_scope_id": compatibility.DEFAULT_ROOT_SCOPE_ID,
        "topology_profile_revision": 1,
        "resource_revision": 1,
        "publication_revision": 0,
        "legacy_write_generation": 0,
        "current_graph_revision": 1,
        "current_graph_version_row_id": "version-1",
        **changes,
    }
    row = SimpleNamespace(**values)
    row.saved = []

    async def save(**kwargs):
        row.saved.append(kwargs)

    row.save = save
    return row


def _version(**changes):
    values = {
        "id": "version-1",
        "tenant_id": TENANT,
        "topology_id": TOPOLOGY_ID,
        "version": 1,
        "topology_json": deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH),
        "layout_json": deepcopy(compatibility.DEFAULT_TOPOLOGY_LAYOUT),
        "document_schema_version": compatibility.LEGACY_SCHEMA_VERSION,
        "change_type": "create",
        **changes,
    }
    return SimpleNamespace(**values)


def _intent(**changes):
    values = {
        "id": "intent-1",
        "tenant_id": TENANT,
        "topology_row_id": TOPOLOGY_ID,
        "legacy_graph_version_row_id": "version-1",
        "expected_legacy_revision": 2,
        "status": "ACTIVE",
        "deleted": False,
        "expires_at": NOW + timedelta(minutes=5),
        "consumed_at": None,
        "consumed_by": None,
        "cancelled_at": None,
        **changes,
    }
    row = SimpleNamespace(**values)
    row.saved = []

    async def save(**kwargs):
        row.saved.append(kwargs)

    row.save = save
    return row


def test_default_blank_reuses_legacy_graph_layout_and_rfc8785_hashes() -> None:
    assert compatibility.DEFAULT_TOPOLOGY_GRAPH == views._DEFAULT_TOPOLOGY_GRAPH
    assert compatibility.DEFAULT_TOPOLOGY_LAYOUT == views._DEFAULT_TOPOLOGY_LAYOUT
    assert compatibility.BLANK_GRAPH_HASH == sha256_uri(
        canonicalize_json(views._DEFAULT_TOPOLOGY_GRAPH)
    )
    assert compatibility.BLANK_LAYOUT_HASH == sha256_uri(
        canonicalize_json(views._DEFAULT_TOPOLOGY_LAYOUT)
    )


@pytest.mark.asyncio
async def test_create_is_one_transaction_with_profile_root_and_version(store) -> None:
    result = await compatibility.create_compatible_topology(
        tenant_id=TENANT,
        name="Core topology",
        created_by=ACTOR,
    )

    assert len(store.rows["topology"]) == 1
    assert len(store.rows["version"]) == 1
    assert len(store.rows["profile"]) == 1
    topology = store.rows["topology"][0]
    version = store.rows["version"][0]
    profile = store.rows["profile"][0]
    default_profile = contracts.load_registry(compatibility.CAPABILITY_REGISTRY_URI)[
        "default_profile"
    ]
    assert result.is_blank is True
    assert topology["document_schema_version"] == "legacy/v1"
    assert topology["generation_source"] == "manual"
    assert topology["root_scope_id"] == default_profile["root_scope_seed"]
    assert topology["topology_profile_revision"] == 1
    assert topology["resource_revision"] == 1
    assert topology["publication_revision"] == 0
    assert topology["current_graph_revision"] == 1
    assert topology["current_graph_version_row_id"] == version["id"]
    assert result.topology.current_graph_version_row_id == result.version.id
    assert not hasattr(result.topology, "current_graph_version_row")
    assert version["document_schema_version"] == "legacy/v1"
    assert profile["revision"] == profile["resource_revision"] == 1
    assert profile["profile_json"] == canonicalize_json(default_profile).decode()
    assert profile["profile_hash"] == sha256_uri(canonicalize_json(default_profile))
    assert profile["created_by"] == ACTOR


async def _ensure_stored_topology(store: Store):
    async with compatibility.in_transaction() as connection:
        topology = await compatibility.NetworkTopology.filter(
            id=TOPOLOGY_ID, tenant_id=TENANT, deleted=False
        ).using_db(connection).select_for_update().first()
        return await compatibility.ensure_compatibility_seeds(
            connection=connection, topology=topology, created_by=ACTOR
        )


@pytest.mark.asyncio
async def test_legacy_topology_is_lazily_seeded_and_idempotent(store) -> None:
    store.rows["topology"].append(
        {"id": TOPOLOGY_ID, "tenant_id": TENANT, "deleted": False,
         "root_scope_id": None, "topology_profile_revision": None}
    )
    first = await _ensure_stored_topology(store)
    second = await _ensure_stored_topology(store)
    assert first.id == second.id
    assert len(store.rows["profile"]) == 1
    assert store.rows["topology"][0]["root_scope_id"] == compatibility.DEFAULT_ROOT_SCOPE_ID
    assert store.rows["topology"][0]["topology_profile_revision"] == 1


@pytest.mark.asyncio
async def test_concurrent_legacy_access_creates_one_profile(store) -> None:
    store.rows["topology"].append(
        {"id": TOPOLOGY_ID, "tenant_id": TENANT, "deleted": False,
         "root_scope_id": None, "topology_profile_revision": None}
    )
    rows = await asyncio.gather(*(_ensure_stored_topology(store) for _ in range(8)))
    assert len({row.id for row in rows}) == 1
    assert len(store.rows["profile"]) == 1


@pytest.mark.asyncio
async def test_existing_profile_is_unchanged(store) -> None:
    store.rows["topology"].append(
        {"id": TOPOLOGY_ID, "tenant_id": TENANT, "deleted": False,
         "root_scope_id": compatibility.DEFAULT_ROOT_SCOPE_ID,
         "topology_profile_revision": 2}
    )
    store.rows["profile"].extend([
        {"id": f"existing-profile-{revision}", "tenant_id": TENANT,
         "deleted": False, "topology_row_id": TOPOLOGY_ID, "revision": revision}
        for revision in (1, 2)])
    before = deepcopy(store.rows)
    profile = await _ensure_stored_topology(store)
    assert profile.id == "existing-profile-1"
    assert store.rows == before


@pytest.mark.asyncio
async def test_lazy_seed_can_be_read_for_manifest_issuance(store, monkeypatch) -> None:
    store.rows["topology"].append(
        {"id": TOPOLOGY_ID, "tenant_id": TENANT, "deleted": False,
         "root_scope_id": None, "topology_profile_revision": None}
    )
    await _ensure_stored_topology(store)
    monkeypatch.setattr(manifest, "TopologyProfileRevision", store.model("profile"))
    topology = _stored_instance(store, "topology", store.rows["topology"][0])
    authority = manifest.ManifestAuthority(TENANT, TOPOLOGY_ID, ACTOR, "playbook-1", "run-1")
    profile_row, profile = await manifest._current_profile(object(), authority, topology, 1)
    assert profile_row.id == "profile-1"
    assert profile["root_scope_seed"] == topology.root_scope_id


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_model", ("version", "profile"))
async def test_create_related_failure_rolls_back_all_rows(store, failed_model) -> None:
    store.fail_create = failed_model
    with pytest.raises(
        compatibility.CompatibilityError,
        match=compatibility.TOPOLOGY_CREATE_FAILED,
    ):
        await compatibility.create_compatible_topology(
            tenant_id=TENANT,
            name="Rollback topology",
            created_by=ACTOR,
        )
    assert store.rows == {"topology": [], "version": [], "profile": []}


@pytest.mark.asyncio
async def test_initial_version_pointer_failure_rolls_back_all_rows(store) -> None:
    store.fail_save = "topology"
    with pytest.raises(
        compatibility.CompatibilityError,
        match=compatibility.TOPOLOGY_CREATE_FAILED,
    ):
        await compatibility.create_compatible_topology(
            tenant_id=TENANT,
            name="Pointer rollback topology",
            created_by=ACTOR,
        )
    assert store.rows == {"topology": [], "version": [], "profile": []}


@pytest.mark.asyncio
async def test_duplicate_name_returns_fixed_conflict(store) -> None:
    store.rows["topology"].append(
        {"id": "existing", "tenant_id": TENANT, "name": "Existing", "deleted": False}
    )
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.create_compatible_topology(
            tenant_id=TENANT,
            name="Existing",
            created_by=ACTOR,
        )
    assert raised.value.code == compatibility.TOPOLOGY_NAME_CONFLICT


@pytest.mark.asyncio
async def test_custom_create_advances_cas_and_is_not_blank(store) -> None:
    graph = deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH)
    graph["nodes"][0]["data"]["title"] = "Configured"
    result = await compatibility.create_compatible_topology(
        tenant_id=TENANT,
        name="Configured topology",
        created_by=ACTOR,
        topology_json=graph,
        layout_json=compatibility.DEFAULT_TOPOLOGY_LAYOUT,
    )
    topology = store.rows["topology"][0]
    assert result.is_blank is False
    assert topology["resource_revision"] == 2
    assert topology["publication_revision"] == 1
    assert topology["legacy_write_generation"] == 1


@pytest.mark.asyncio
async def test_explicit_default_payload_is_still_a_non_blank_create(store) -> None:
    result = await compatibility.create_compatible_topology(
        tenant_id=TENANT,
        name="Explicit graph topology",
        created_by=ACTOR,
        topology_json=deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH),
        layout_json=deepcopy(compatibility.DEFAULT_TOPOLOGY_LAYOUT),
    )
    topology = store.rows["topology"][0]
    assert result.is_blank is False
    assert topology["resource_revision"] == 2
    assert topology["publication_revision"] == 1
    assert topology["legacy_write_generation"] == 1


@pytest.mark.asyncio
async def test_invalid_explicit_payload_has_fixed_create_error(store) -> None:
    graph = deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH)
    graph["nodes"][0]["position"]["x"] = float("nan")
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.create_compatible_topology(
            tenant_id=TENANT,
            name="Invalid graph topology",
            created_by=ACTOR,
            topology_json=graph,
            layout_json=deepcopy(compatibility.DEFAULT_TOPOLOGY_LAYOUT),
        )
    assert raised.value.code == compatibility.TOPOLOGY_CREATE_INVALID
    assert store.rows == {"topology": [], "version": [], "profile": []}


@pytest.mark.asyncio
async def test_registry_failure_has_fixed_internal_create_error(
    store, monkeypatch
) -> None:
    def fail_registry(_uri):
        raise RuntimeError("sensitive registry detail")

    monkeypatch.setattr(compatibility.contracts, "load_registry", fail_registry)
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.create_compatible_topology(
            tenant_id=TENANT,
            name="Registry failure topology",
            created_by=ACTOR,
        )
    assert raised.value.code == compatibility.TOPOLOGY_CREATE_FAILED
    assert str(raised.value) == compatibility.TOPOLOGY_CREATE_FAILED
    assert store.rows == {"topology": [], "version": [], "profile": []}


@pytest.mark.parametrize(
    ("topology_changes", "version_changes"),
    (
        ({"revision": 2}, {}),
        ({"resource_revision": 2}, {}),
        ({"publication_revision": 1}, {}),
        ({"current_graph_revision": 2}, {}),
        ({"current_graph_version_row_id": "other-version"}, {}),
        ({"root_scope_id": "scope:other"}, {}),
        ({"topology_json": {"nodes": [], "edges": []}}, {}),
        ({"layout_json": {"node-1": {"x": 1.0, "y": 0.0}}}, {}),
        ({}, {"version": 2}),
        ({}, {"id": ""}),
        ({}, {"topology_id": "other-topology"}),
        ({}, {"tenant_id": "other-tenant"}),
        ({}, {"change_type": "manual_save"}),
    ),
)
def test_blank_sentinel_is_strict(topology_changes, version_changes) -> None:
    assert not compatibility.is_blank_legacy_topology(
        _topology(**topology_changes), _version(**version_changes)
    )


def test_blank_sentinel_requires_default_version_row() -> None:
    assert compatibility.is_blank_legacy_topology(_topology(), _version())
    assert not compatibility.is_blank_legacy_topology(_topology())


def test_legacy_write_guard_and_revision_are_fail_closed() -> None:
    topology = _topology()
    compatibility.require_legacy_write_allowed(topology)
    fields = compatibility.apply_legacy_write_revision(topology)
    assert fields == (
        "resource_revision",
        "publication_revision",
        "legacy_write_generation",
    )
    assert (topology.resource_revision, topology.publication_revision) == (2, 1)
    assert topology.legacy_write_generation == 1

    for schema in (compatibility.V2_SCHEMA_VERSION, "graph/v3"):
        with pytest.raises(compatibility.CompatibilityError) as raised:
            compatibility.require_legacy_write_allowed(
                _topology(document_schema_version=schema)
            )
        assert raised.value.code == compatibility.TOPOLOGY_LEGACY_WRITE_REJECTED

    for schema, source in (
        (None, compatibility.PLAYBOOK_GENERATION_SOURCE),
        (compatibility.LEGACY_SCHEMA_VERSION, None),
        (compatibility.LEGACY_SCHEMA_VERSION, compatibility.PLAYBOOK_GENERATION_SOURCE),
    ):
        with pytest.raises(compatibility.CompatibilityError) as raised:
            compatibility.require_legacy_write_allowed(
                _topology(document_schema_version=schema, generation_source=source)
            )
        assert raised.value.code == compatibility.TOPOLOGY_LEGACY_WRITE_REJECTED

    unversioned = _topology(document_schema_version=None, generation_source=None)
    fields = compatibility.apply_legacy_write_revision(unversioned)
    assert fields[-2:] == ("document_schema_version", "generation_source")
    assert unversioned.document_schema_version == compatibility.LEGACY_SCHEMA_VERSION
    assert unversioned.generation_source == compatibility.MANUAL_GENERATION_SOURCE


def test_bind_current_legacy_version_only_moves_pointer() -> None:
    topology = _topology(revision=2, resource_revision=3, publication_revision=2)
    version = _version(topology_id=TOPOLOGY_ID, version=2)
    before_revisions = (
        topology.revision,
        topology.resource_revision,
        topology.publication_revision,
        topology.legacy_write_generation,
    )
    fields = compatibility.bind_current_legacy_version(topology, version)
    assert fields == ("current_graph_version_row_id", "current_graph_revision")
    assert topology.current_graph_version_row_id == version.id
    assert not hasattr(topology, "current_graph_version_row")
    assert topology.current_graph_revision == 2
    assert before_revisions == (
        topology.revision,
        topology.resource_revision,
        topology.publication_revision,
        topology.legacy_write_generation,
    )


def test_bind_current_legacy_version_rejects_wrong_revision_or_topology() -> None:
    topology = _topology(revision=2)
    for version in (
        _version(topology_id=TOPOLOGY_ID, version=1),
        _version(topology_id="other-topology", version=2),
        _version(
            topology_id=TOPOLOGY_ID, version=2, document_schema_version="graph/v2"
        ),
    ):
        with pytest.raises(compatibility.CompatibilityError) as raised:
            compatibility.bind_current_legacy_version(topology, version)
        assert raised.value.code == compatibility.TOPOLOGY_LEGACY_VERSION_INVALID


def test_bind_uses_tortoise_fk_source_field_not_relation_conversion() -> None:
    Tortoise.init_models(["models"], "models")
    topology = NetworkTopology(
        id=TOPOLOGY_ID,
        tenant_id=TENANT,
        name="Topology",
        topology_json=deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH),
        revision=1,
        document_schema_version=compatibility.LEGACY_SCHEMA_VERSION,
        generation_source=compatibility.MANUAL_GENERATION_SOURCE,
    )
    version = NetworkTopologyVersion(
        id="version-1",
        tenant_id=TENANT,
        topology_id=TOPOLOGY_ID,
        version=1,
        name="Topology",
        topology_json=deepcopy(compatibility.DEFAULT_TOPOLOGY_GRAPH),
        change_type="create",
        change_summary="创建拓扑",
        document_schema_version=compatibility.LEGACY_SCHEMA_VERSION,
    )
    relation_field = topology._meta.fields_map["current_graph_version_row"]
    source_field = topology._meta.fields_map["current_graph_version_row_id"]
    assert relation_field.to_db_value(version, topology) != version.id

    fields = compatibility.bind_current_legacy_version(topology, version)

    assert fields == ("current_graph_version_row_id", "current_graph_revision")
    assert topology.current_graph_version_row_id == version.id
    assert source_field.to_db_value(
        topology.current_graph_version_row_id, topology
    ) == (version.id)


def test_capabilities_never_offer_legacy_write_for_v2_or_unknown() -> None:
    assert compatibility.compatibility_capabilities(_topology()) == {
        "legacy_write": True,
        "v2_read": False,
    }
    assert compatibility.compatibility_capabilities(
        _topology(document_schema_version="graph/v2", generation_source="playbook")
    ) == {"legacy_write": False, "v2_read": True}
    assert compatibility.compatibility_capabilities(
        _topology(document_schema_version="graph/v3")
    ) == {"legacy_write": False, "v2_read": False}
    assert compatibility.compatibility_capabilities(
        _topology(document_schema_version=None)
    ) == {"legacy_write": False, "v2_read": False}
    assert compatibility.compatibility_capabilities(
        _topology(document_schema_version=None, generation_source=None)
    ) == {"legacy_write": True, "v2_read": False}
    assert compatibility.compatibility_capabilities(
        _topology(document_schema_version="legacy/v1", generation_source=None)
    ) == {"legacy_write": False, "v2_read": False}
    assert compatibility.compatibility_capabilities(
        {"document_schema_version": "graph/v2", "generation_source": "playbook"}
    ) == {"legacy_write": False, "v2_read": True}


def test_publish_transition_declares_internal_locking_boundary() -> None:
    doc = compatibility.transition_first_v2_publish.__doc__ or ""
    assert "caller-held transaction" in doc
    assert "locked topology rows" in doc


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ("FAILED", "CANCELLED", "SUPERSEDED"))
async def test_failed_analysis_never_switches_schema(state) -> None:
    topology = _topology()
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.transition_first_v2_publish(
            connection=object(),
            topology=topology,
            legacy_version=_version(),
            expected_revision=1,
            expected_publication_revision=0,
            expected_profile_revision=1,
            analysis_state=state,
            published_by="worker-1",
            published_at=NOW,
        )
    assert raised.value.code == compatibility.TOPOLOGY_PUBLISH_STATE_INVALID
    assert topology.document_schema_version == "legacy/v1"
    assert topology.generation_source == "manual"
    assert topology.saved == []


@pytest.mark.asyncio
async def test_blank_first_v2_publish_switches_schema_without_intent() -> None:
    topology = _topology()
    fields = await compatibility.transition_first_v2_publish(
        connection=object(),
        topology=topology,
        legacy_version=_version(),
        expected_revision=1,
        expected_publication_revision=0,
        expected_profile_revision=1,
        analysis_state="SUCCEEDED",
        published_by="worker-1",
        published_at=NOW,
    )
    assert fields == ("document_schema_version", "generation_source")
    assert topology.document_schema_version == "graph/v2"
    assert topology.generation_source == "playbook"
    assert topology.saved[0]["update_fields"] == [
        "document_schema_version",
        "generation_source",
        "utime",
    ]


@pytest.mark.asyncio
async def test_non_blank_publish_requires_and_consumes_upgrade_intent() -> None:
    topology = _topology(
        revision=2,
        resource_revision=2,
        publication_revision=1,
        current_graph_revision=2,
    )
    version = _version(version=2, change_type="manual_save")
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.transition_first_v2_publish(
            connection=object(),
            topology=topology,
            legacy_version=version,
            expected_revision=2,
            expected_publication_revision=1,
            expected_profile_revision=1,
            analysis_state="SUCCEEDED",
            published_by="worker-1",
            published_at=NOW,
        )
    assert raised.value.code == compatibility.LEGACY_CONVERSION_REQUIRED

    intent = _intent()
    await compatibility.transition_first_v2_publish(
        connection=object(),
        topology=topology,
        legacy_version=version,
        expected_revision=2,
        expected_publication_revision=1,
        expected_profile_revision=1,
        analysis_state="SUCCEEDED",
        upgrade_intent=intent,
        published_by="worker-1",
        published_at=NOW,
    )
    assert (intent.status, intent.consumed_by, intent.consumed_at) == (
        "CONSUMED",
        "worker-1",
        NOW,
    )
    assert intent.saved[0]["update_fields"] == [
        "status",
        "consumed_at",
        "consumed_by",
        "utime",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent_changes",
    (
        {"legacy_graph_version_row_id": None},
        {"legacy_graph_version_row_id": "other-version"},
        {"consumed_at": NOW - timedelta(seconds=1)},
        {"cancelled_at": NOW - timedelta(seconds=1)},
        {"expires_at": NOW},
    ),
)
async def test_upgrade_intent_must_bind_exact_unconsumed_version(
    intent_changes,
) -> None:
    topology = _topology(
        revision=2,
        resource_revision=2,
        publication_revision=1,
        current_graph_revision=2,
    )
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.transition_first_v2_publish(
            connection=object(),
            topology=topology,
            legacy_version=_version(version=2, change_type="manual_save"),
            expected_revision=2,
            expected_publication_revision=1,
            expected_profile_revision=1,
            analysis_state="SUCCEEDED",
            upgrade_intent=_intent(**intent_changes),
            published_by="worker-1",
            published_at=NOW,
        )
    assert raised.value.code == compatibility.TOPOLOGY_UPGRADE_INTENT_INVALID
    assert topology.saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topology_changes", "version_changes"),
    (
        ({"current_graph_version_row_id": "other-version"}, {}),
        ({"current_graph_revision": 1}, {}),
        ({}, {"id": "other-version"}),
        ({}, {"topology_id": "other-topology"}),
        ({}, {"tenant_id": "other-tenant"}),
    ),
)
async def test_non_blank_publish_requires_exact_current_version_binding(
    topology_changes, version_changes
) -> None:
    topology_values = {
        "revision": 2,
        "resource_revision": 2,
        "publication_revision": 1,
        "current_graph_revision": 2,
        **topology_changes,
    }
    topology = _topology(**topology_values)
    version = _version(version=2, change_type="manual_save", **version_changes)
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.transition_first_v2_publish(
            connection=object(),
            topology=topology,
            legacy_version=version,
            expected_revision=2,
            expected_publication_revision=1,
            expected_profile_revision=1,
            analysis_state="SUCCEEDED",
            upgrade_intent=_intent(),
            published_by="worker-1",
            published_at=NOW,
        )
    assert raised.value.code == compatibility.TOPOLOGY_PUBLICATION_CAS_MISMATCH
    assert topology.saved == []


@pytest.mark.asyncio
async def test_publish_cas_rejection_does_not_mutate_rows() -> None:
    topology = _topology()
    with pytest.raises(compatibility.CompatibilityError) as raised:
        await compatibility.transition_first_v2_publish(
            connection=object(),
            topology=topology,
            legacy_version=_version(),
            expected_revision=1,
            expected_publication_revision=99,
            expected_profile_revision=1,
            analysis_state="SUCCEEDED",
            published_by="worker-1",
            published_at=NOW,
        )
    assert raised.value.code == compatibility.TOPOLOGY_PUBLICATION_CAS_MISMATCH
    assert topology.document_schema_version == "legacy/v1"
    assert topology.saved == []
