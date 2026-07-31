from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
from types import SimpleNamespace

import orjson
import pytest

from services.network_topology import compatibility_manager as compatibility
from services.network_topology import contracts, manifest
from tests.network_topology.test_phase1_compatibility import (
    Store,
    _stored_instance,
    _transaction,
)
from tests.network_topology.test_phase2_manifest import _Query

class _Transaction:
    async def __aenter__(self):
        return "connection-1"

    async def __aexit__(self, error_type, error, traceback):
        return False


class _TopologyRow(SimpleNamespace):
    def __init__(self, **values):
        super().__init__(**values)
        self.saved = []

    async def save(self, **kwargs):
        self.saved.append(kwargs)

    @property
    def json(self):
        return {
            "id": self.id,
            "name": self.name,
            "revision": self.revision,
            "document_schema_version": self.document_schema_version,
            "generation_source": self.generation_source,
            "source_hash": self.source_hash,
            "resource_revision": self.resource_revision,
            "publication_revision": self.publication_revision,
            "legacy_write_generation": self.legacy_write_generation,
        }


def _request(body):
    session = {"tenant_id": "tenant-1", "sub": "account-1"}
    return SimpleNamespace(json=body, ctx=SimpleNamespace(session=session))


def _row(schema="legacy/v1", source="manual", source_hash=None):
    return _TopologyRow(
        id="topology-1",
        tenant_id="tenant-1",
        name="总部网络",
        revision=1,
        topology_json={"nodes": [{"id": "node-1"}], "edges": []},
        layout_json={"node-1": {"x": 0.0, "y": 0.0}},
        source_hash=source_hash,
        document_schema_version=schema,
        generation_source=source,
        resource_revision=1,
        publication_revision=0,
        legacy_write_generation=0,
        current_graph_revision=1,
        current_graph_version_row_id="version-1",
    )


def _response_message(response):
    return orjson.loads(response.body)["message"]


def _layout_payload():
    return {
        "id": "topology-1",
        "revision": 1,
        "layout_json": {"node-1": {"x": 5, "y": 8}},
    }


async def _create_shape_responses(views):
    omitted = await inspect.unwrap(views.create_topology)(_request({"name": "省略图"}))
    explicit = await inspect.unwrap(views.create_topology)(
        _request(
            {
                "name": "显式图",
                "topology_json": views._DEFAULT_TOPOLOGY_GRAPH,
                "layout_json": views._DEFAULT_TOPOLOGY_LAYOUT,
            }
        )
    )
    manual = await views._save_graph(
        _request(
            {
                "name": "手工显式图",
                "topology_json": views._DEFAULT_TOPOLOGY_GRAPH,
                "layout_json": views._DEFAULT_TOPOLOGY_LAYOUT,
            }
        )
    )
    return omitted, explicit, manual


def test_strict_topology_switches_accept_only_explicit_booleans(monkeypatch):
    import settings

    monkeypatch.delenv("TEST_TOPOLOGY_SWITCH", raising=False)
    assert settings._strict_env_bool("TEST_TOPOLOGY_SWITCH", False) is False
    assert settings._strict_env_bool("TEST_TOPOLOGY_SWITCH", True) is True

    for raw, expected in (("true", True), (" FALSE ", False)):
        monkeypatch.setenv("TEST_TOPOLOGY_SWITCH", raw)
        assert settings._strict_env_bool("TEST_TOPOLOGY_SWITCH", False) is expected

    monkeypatch.setenv("TEST_TOPOLOGY_SWITCH", "1")
    try:
        settings._strict_env_bool("TEST_TOPOLOGY_SWITCH", False)
    except ValueError as error:
        assert str(error) == "TEST_TOPOLOGY_SWITCH must be true or false"
    else:
        raise AssertionError("numeric boolean must fail closed")


def test_v2_update_and_manual_save_reject_legacy_graph_writes(monkeypatch):
    from services.network_topology import views

    row = _row("graph/v2", "playbook")

    async def topology(*_args, **_kwargs):
        return row

    async def allow(*_args):
        return True

    monkeypatch.setattr(views, "_tenant_topology", topology)
    monkeypatch.setattr(views, "check_permissions", allow)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())

    update = asyncio.run(
        inspect.unwrap(views.update_topology)(
            _request({"id": "topology-1", "revision": 1, "name": "新名称"})
        )
    )
    manual = asyncio.run(
        views._save_graph(
            _request(
                {
                    "id": "topology-1",
                    "revision": 1,
                    "name": "新名称",
                    "topology_json": row.topology_json,
                    "layout_json": row.layout_json,
                }
            )
        )
    )

    assert (update.status, manual.status) == (409, 409)
    assert "v2 拓扑不允许" in _response_message(update)
    assert row.saved == []

    row = _row(None, "playbook")
    invalid_pair = asyncio.run(
        inspect.unwrap(views.update_topology)(
            _request({"id": "topology-1", "revision": 1, "name": "新名称"})
        )
    )
    assert invalid_pair.status == 409
    assert row.saved == []


def test_create_shape_preserves_blank_and_explicit_default_boundary(monkeypatch):
    from services.network_topology import views

    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        blank = kwargs["topology_json"] is None
        topology = _row()
        topology.id = f"topology-{len(calls)}"
        topology.name = kwargs["name"]
        topology.resource_revision = 1 if blank else 2
        topology.publication_revision = 0 if blank else 1
        topology.legacy_write_generation = 0 if blank else 1
        return SimpleNamespace(topology=topology, is_blank=blank)

    async def allow(*_args):
        return True

    monkeypatch.setattr(views.compatibility, "create_compatible_topology", create)
    monkeypatch.setattr(views, "check_permissions", allow)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    omitted, explicit, manual = asyncio.run(_create_shape_responses(views))

    assert calls[0]["topology_json"] is None
    assert all(call["topology_json"] is not None for call in calls[1:])
    assert (
        omitted["resource_revision"],
        omitted["publication_revision"],
        omitted["legacy_write_generation"],
    ) == (1, 0, 0)
    for response in (explicit, manual):
        assert (
            response["resource_revision"],
            response["publication_revision"],
            response["legacy_write_generation"],
        ) == (2, 1, 1)


def test_layout_rejects_v2_and_advances_legacy_cas_and_pointer(monkeypatch):
    from services.network_topology import views

    row = _row("graph/v2", "playbook")

    async def topology(*_args, **_kwargs):
        return row

    class FakeVersion:
        @classmethod
        async def create(cls, **kwargs):
            return SimpleNamespace(
                id="version-2", topology_id=kwargs["topology"].id, **kwargs
            )

    monkeypatch.setattr(views, "_tenant_topology", topology)
    monkeypatch.setattr(views, "NetworkTopologyVersion", FakeVersion)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())
    monkeypatch.setattr(views, "response_ok", lambda data: data)
    payload = _layout_payload()

    rejected = asyncio.run(inspect.unwrap(views.update_layout)(_request(payload)))
    assert rejected.status == 409
    assert row.saved == []

    row = _row()
    row.resource_revision = 5
    row.publication_revision = 7
    row.legacy_write_generation = 9
    updated = asyncio.run(inspect.unwrap(views.update_layout)(_request(payload)))

    assert updated["revision"] == 2
    assert row.layout_json == {"node-1": {"x": 5.0, "y": 8.0}}
    assert (
        row.resource_revision,
        row.publication_revision,
        row.legacy_write_generation,
    ) == (6, 8, 10)
    assert (row.current_graph_revision, row.current_graph_version_row_id) == (
        2,
        "version-2",
    )
    assert row.saved[1]["update_fields"] == [
        "current_graph_version_row_id",
        "current_graph_revision",
        "utime",
    ]


def test_config_sync_shape_selects_exact_secondary_permission(monkeypatch):
    from services.network_topology import views

    permissions = []

    async def allow(_request, permission):
        permissions.append(permission)
        return True

    config_hash = hashlib.sha256(b"hostname core").hexdigest()
    row = _row(source_hash=config_hash)

    async def topology(*_args, **_kwargs):
        return row

    monkeypatch.setattr(views, "check_permissions", allow)
    monkeypatch.setattr(views, "_tenant_topology", topology)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    mixed = asyncio.run(
        inspect.unwrap(views.sync_topology_config)(
            _request(
                {
                    "id": "topology-1",
                    "config": "hostname core",
                    "snapshot": {"schema_version": views._SNAPSHOT_SCHEMA},
                }
            )
        )
    )
    legacy = asyncio.run(
        inspect.unwrap(views.sync_topology_config)(
            _request({"id": "topology-1", "config": "hostname core"})
        )
    )

    assert mixed.status == 400
    assert "不能同时提交" in _response_message(mixed)
    assert permissions == ["network_topology:u"]
    assert legacy["changed"] is False
    assert legacy["deprecated"] is True


def test_capabilities_use_global_switches(monkeypatch):
    from services.network_topology import views

    monkeypatch.setattr(views, "TOPOLOGY_V2_INGRESS_ENABLED", False)
    monkeypatch.setattr(views, "TOPOLOGY_V2_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(views, "TOPOLOGY_V2_PUBLISH_ENABLED", False)
    monkeypatch.setattr(views, "TOPOLOGY_V2_VIEW_ENABLED", False)
    monkeypatch.setattr(views, "TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED", False)

    legacy = views._capabilities(_row())
    v2 = views._capabilities(_row("graph/v2", "playbook"))

    assert legacy == {
        "legacy_write": True,
        "v2_read": False,
        "ingress": False,
        "analysis": False,
        "publish": False,
        "view": False,
        "presentation_write": False,
    }
    assert v2["legacy_write"] is False
    assert v2["v2_read"] is True
    assert all(
        not v2[name] for name in ("ingress", "analysis", "publish", "view")
    )


def test_snapshot_sync_checks_sync_permission_and_ingress_gate(monkeypatch):
    from services.network_topology import views

    permissions = []
    async def allow(_request, permission):
        permissions.append(permission)
        return True

    async def topology(*_args, **_kwargs):
        return _row()

    monkeypatch.setattr(views, "check_permissions", allow)
    monkeypatch.setattr(views, "_tenant_topology", topology)
    monkeypatch.setattr(views, "TOPOLOGY_V2_INGRESS_ENABLED", False)

    response = asyncio.run(
        inspect.unwrap(views.sync_topology_config)(
            _request(
                {
                    "id": "topology-1",
                    "snapshot": {"schema_version": views._SNAPSHOT_SCHEMA},
                }
            )
        )
    )

    assert permissions == ["network_topology:sync"]
    assert response.status == 503
    assert "入口未启用" in _response_message(response)


@pytest.fixture
def compatibility_store(monkeypatch):
    store = Store()
    monkeypatch.setattr(compatibility, "NetworkTopology", store.model("topology"))
    monkeypatch.setattr(
        compatibility, "NetworkTopologyVersion", store.model("version")
    )
    monkeypatch.setattr(
        compatibility, "TopologyProfileRevision", store.model("profile")
    )
    monkeypatch.setattr(compatibility, "in_transaction", _transaction(store))
    return store


def test_create_http_duplicate_name_rolls_back_without_related_rows(
    compatibility_store,
):
    from services.network_topology import views

    asyncio.run(
        compatibility.create_compatible_topology(
            tenant_id="tenant-1", name="重复拓扑", created_by="account-1"
        )
    )
    before = deepcopy(compatibility_store.rows)
    response = asyncio.run(
        inspect.unwrap(views.create_topology)(_request({"name": "重复拓扑"}))
    )

    assert response.status == 400
    assert "名称已存在" in _response_message(response)
    assert compatibility_store.rows == before
    assert tuple(map(len, compatibility_store.rows.values())) == (1, 1, 1)


class _ManifestRows:
    def __init__(self):
        self.rows = []

    def filter(self, **filters):
        return _Query(self.rows, filters)

    async def create(self, **values):
        values.pop("using_db", None)
        self.rows.append(SimpleNamespace(**values))
        return self.rows[-1]


def _manifest_payload(topology_id):
    return {
        "id": topology_id,
        "profile_revision": 1,
        "resolved_resource_ids": ["resource-1"],
        "selection_provenance": {
            "playbook_id": "playbook-1",
            "run_id": "run-1",
            "selector_hash": f"sha256:{'0' * 64}",
        },
    }


def test_created_blank_topology_issues_manifest(compatibility_store, monkeypatch):
    result = asyncio.run(
        compatibility.create_compatible_topology(
            tenant_id="tenant-1", name="可立即采集", created_by="account-1"
        )
    )
    topology = _stored_instance(
        compatibility_store, "topology", compatibility_store.rows["topology"][0]
    )
    manifest_rows = _ManifestRows()
    resource = SimpleNamespace(id="resource-1", asset_model_key="network_device")
    expected = contracts.load_schema(manifest.MANIFEST_RESPONSE_SCHEMA)["examples"][0][
        "data"
    ]["target_manifest"]["expected_resources"]

    monkeypatch.setattr(manifest, "NetworkTopology", compatibility_store.model("topology"))
    monkeypatch.setattr(
        manifest, "NetworkTopologyVersion", compatibility_store.model("version")
    )
    monkeypatch.setattr(
        manifest, "TopologyProfileRevision", compatibility_store.model("profile")
    )
    monkeypatch.setattr(
        manifest,
        "AssetInstance",
        SimpleNamespace(filter=lambda **filters: _Query([resource], filters)),
    )
    monkeypatch.setattr(manifest, "TopologyCollectionManifest", manifest_rows)
    monkeypatch.setattr(manifest, "in_transaction", _transaction(compatibility_store))
    monkeypatch.setattr(manifest, "TOPOLOGY_V2_INGRESS_ENABLED", True)
    monkeypatch.setattr(manifest, "_expected_resources", lambda _rows: expected)

    authority = manifest.ManifestAuthority(
        "tenant-1", topology.id, "account-1", "playbook-1", "run-1"
    )
    issued = asyncio.run(
        manifest.issue_manifest(
            authority,
            _manifest_payload(topology.id),
            clock=lambda: datetime(2026, 7, 30, tzinfo=timezone.utc),
            id_factory=lambda: "manifest-created-topology",
        )
    )

    assert result.profile_revision.id == compatibility_store.rows["profile"][0]["id"]
    assert issued.target_manifest["base_graph_revision"] == 1
    assert issued.target_manifest["base_publication_revision"] == 0
    assert issued.target_manifest["manifest_id"] == "manifest-created-topology"
    assert len(manifest_rows.rows) == 1
