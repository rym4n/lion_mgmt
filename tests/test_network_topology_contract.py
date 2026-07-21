# coding=utf-8
import ast
import asyncio
from datetime import datetime, timezone
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest
from tortoise import fields
from tortoise.fields.relational import ForeignKeyFieldInstance

from models.base import EncryptedTextField
from models.network_topology import NetworkTopology, NetworkTopologyVersion


ROOT = Path(__file__).resolve().parents[1]


def _request(body=None, *, tenant_id="tenant-1"):
    return SimpleNamespace(
        json={} if body is None else body,
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


def _route_contracts():
    source = (ROOT / "services/network_topology/views.py").read_text()
    contracts = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = None
        permissions = ()
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if (
                isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "post"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                route = decorator.args[0].value
            if (
                isinstance(decorator.func, ast.Name)
                and decorator.func.id == "requires_permission"
            ):
                permissions = tuple(
                    item.value
                    for item in decorator.args
                    if isinstance(item, ast.Constant)
                )
        if route:
            contracts[route] = (node.name, permissions)
    return contracts


def test_network_topology_exposes_data_crud_and_external_config_sync_only():
    assert _route_contracts() == {
        "/manual-save": (
            "save_manual_topology",
            ("network_topology:c", "network_topology:u"),
        ),
        "/create": ("create_topology", ("network_topology:c",)),
        "/update": ("update_topology", ("network_topology:u",)),
        "/config-sync": ("sync_topology_config", ("network_topology:u",)),
        "/list": ("list_topology", ("network_topology:r",)),
        "/detail": ("get_topology", ("network_topology:r",)),
        "/versions": ("list_topology_versions", ("network_topology:r",)),
        "/version-detail": (
            "get_topology_version_detail",
            ("network_topology:r",),
        ),
        "/layout": ("update_layout", ("network_topology:u",)),
        "/delete": ("delete_topology", ("network_topology:d",)),
    }


def test_topology_write_paths_append_versions_and_list_counts_real_snapshots():
    from services.network_topology import views

    functions = {
        views._save_graph: 'change_type="manual_save"',
        inspect.unwrap(views.create_topology): 'change_type="create"',
        inspect.unwrap(views.update_topology): 'change_type="update"',
        inspect.unwrap(views.sync_topology_config): 'change_type="config_sync"',
        inspect.unwrap(views.update_layout): 'change_type="layout"',
    }
    for function, change_type in functions.items():
        source = inspect.getsource(function)
        assert "async with in_transaction() as connection" in source
        assert "await _append_topology_version(" in source
        assert change_type in source

    list_source = inspect.getsource(inspect.unwrap(views.list_topology))
    assert "version_count=Count(" in list_source
    assert '"version_count"' in list_source
    assert 'Count("versions"' not in list_source
    assert "topology_id__in=topology_ids" in list_source
    assert "tenant_id=tenant_id" in list_source


def test_network_topology_model_keeps_graph_sync_fields_without_automation_state():
    fields_map = NetworkTopology._meta.fields_map

    assert isinstance(fields_map["source_config"], EncryptedTextField)
    assert isinstance(fields_map["topology_json"], fields.JSONField)
    assert isinstance(fields_map["layout_json"], fields.JSONField)
    assert isinstance(fields_map["source_hash"], fields.CharField)
    assert isinstance(fields_map["revision"], fields.IntField)
    assert isinstance(fields_map["last_sync_at"], fields.DatetimeField)
    assert {
        "action_sources",
        "refresh_interval",
        "enabled",
        "executor_id",
        "last_run_ids",
        "refresh_status",
        "last_refresh_at",
        "next_refresh_at",
        "refresh_started_at",
        "refresh_token",
        "refresh_error",
    }.isdisjoint(fields_map)

    version_fields = NetworkTopologyVersion._meta.fields_map
    assert isinstance(version_fields["topology"], ForeignKeyFieldInstance)
    assert isinstance(version_fields["version"], fields.IntField)
    assert isinstance(version_fields["topology_json"], fields.JSONField)
    assert isinstance(version_fields["layout_json"], fields.JSONField)
    assert isinstance(version_fields["change_type"], fields.CharField)
    assert isinstance(version_fields["change_summary"], fields.TextField)
    assert "source_config" not in version_fields


def test_external_automation_migration_removes_runtime_state_and_rolls_back_exact_defaults():
    source = (
        ROOT / "migrations/models/82_20260720160000_externalize_network_automation.py"
    ).read_text()

    assert 'RENAME COLUMN "last_refresh_at" TO "last_sync_at"' in source
    assert 'DROP COLUMN "action_sources"' in source
    assert 'ADD COLUMN "enabled" BOOL NOT NULL DEFAULT TRUE' in source
    assert (
        "ADD COLUMN \"refresh_status\" VARCHAR(32) NOT NULL DEFAULT 'pending'" in source
    )


def test_topology_version_migration_backfills_only_current_non_deleted_snapshot():
    source = (
        ROOT / "migrations/models/84_20260721120000_network_topology_versions.py"
    ).read_text()

    assert 'CREATE TABLE IF NOT EXISTS "network_topology_version"' in source
    assert 'ON CONFLICT ("topology_id", "version") DO NOTHING' in source
    assert 'WHERE NOT "deleted"' in source
    assert "'baseline'" in source
    assert '"source_config"' not in source


def test_network_topology_json_never_leaks_collected_config():
    topology = NetworkTopology(
        id="topology-1",
        tenant_id="tenant-1",
        name="总部网络",
        source_config="plaintext-secret-device-config",
        topology_json={"nodes": [{"id": "core"}], "edges": []},
        layout_json={"core": {"x": 1, "y": 2}},
        source_hash="hash-1",
        revision=3,
        last_sync_at=datetime(2026, 7, 20, 1, 2, 3, tzinfo=timezone.utc),
        ctime=datetime(2026, 7, 20, 1, 2, 3, tzinfo=timezone.utc),
        utime=datetime(2026, 7, 20, 1, 2, 4, tzinfo=timezone.utc),
    )

    payload = topology.json

    assert "source_config" not in payload
    assert "plaintext-secret-device-config" not in repr(payload)
    assert payload["node_count"] == 1
    assert payload["edge_count"] == 0
    assert payload["revision"] == 3
    assert payload["last_sync_at"].endswith("09:02:03")


def test_network_topology_version_json_is_a_read_only_graph_snapshot():
    version = NetworkTopologyVersion(
        id="version-1",
        tenant_id="tenant-1",
        version=3,
        name="总部网络",
        topology_json={"nodes": [{"id": "core"}], "edges": []},
        layout_json={"core": {"x": 1, "y": 2}},
        source_hash="hash-1",
        change_type="manual_save",
        change_summary="修改节点 1 个",
        ctime=datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc),
        utime=datetime(2026, 7, 21, 1, 2, 3, tzinfo=timezone.utc),
    )
    version.topology_id = "topology-1"

    payload = version.json

    assert payload["version_id"] == "version-1"
    assert payload["topology_id"] == "topology-1"
    assert payload["version"] == 3
    assert payload["node_count"] == 1
    assert payload["edge_count"] == 0
    assert "source_config" not in payload


def test_graph_and_layout_normalization_reject_invalid_or_incomplete_data():
    from services.network_topology import views

    graph = {
        "nodes": [
            {"id": " core ", "position": {"x": 1, "y": 2}},
            {"id": "edge", "position": {"x": 3, "y": 4}},
        ],
        "edges": [{"id": " uplink ", "source": " core ", "target": "edge"}],
        "ignored": "not persisted",
    }
    normalized_graph, normalized_layout = views._normalize_graph(
        graph,
        {"core": {"x": 10, "y": 20}, "edge": {"x": 30, "y": 40}},
    )

    assert normalized_layout == {
        "core": {"x": 10.0, "y": 20.0},
        "edge": {"x": 30.0, "y": 40.0},
    }
    assert [node["id"] for node in normalized_graph["nodes"]] == ["core", "edge"]
    assert normalized_graph["edges"] == [
        {"id": "uplink", "source": "core", "target": "edge"}
    ]

    invalid_cases = [
        ({"nodes": [{"id": "same"}, {"id": "same"}], "edges": []}, None),
        (
            {
                "nodes": [{"id": "core"}],
                "edges": [{"id": "missing", "source": "core", "target": "edge"}],
            },
            None,
        ),
        (
            {"nodes": [{"id": "core"}, {"id": "edge"}], "edges": []},
            {"core": {"x": 1, "y": 2}},
        ),
        (
            {"nodes": [{"id": "core"}], "edges": []},
            {"core": None},
        ),
        (
            {"nodes": [{"id": "core"}], "edges": []},
            {"core": {"x": float("nan"), "y": 2}},
        ),
        (
            {
                "nodes": [{"id": "core"}, {"id": "edge"}],
                "edges": [
                    {"id": "forward", "source": "core", "target": "edge"},
                    {"id": "reverse", "source": "edge", "target": "core"},
                ],
            },
            None,
        ),
    ]
    for invalid_graph, invalid_layout in invalid_cases:
        with pytest.raises(ValueError):
            views._normalize_graph(invalid_graph, invalid_layout)


def test_topology_change_summary_distinguishes_graph_and_layout_changes():
    from services.network_topology import views

    before = {
        "name": "旧名称",
        "topology_json": {
            "nodes": [
                {"id": "n1", "position": {"x": 0, "y": 0}, "data": {"title": "旧"}},
                {"id": "n2", "data": {"title": "删除"}},
                {"id": "n4", "data": {"title": "保留"}},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n4", "data": {"port": "1"}},
                {"id": "e2", "source": "n2", "target": "n1"},
            ],
        },
        "layout_json": {"n1": {"x": 0, "y": 0}, "n4": {"x": 2, "y": 2}},
        "source_hash": "old-hash",
    }
    after = {
        "name": "新名称",
        "topology_json": {
            "nodes": [
                {"id": "n1", "position": {"x": 1, "y": 1}, "data": {"title": "新"}},
                {"id": "n3", "data": {"title": "新增"}},
                {"id": "n4", "data": {"title": "保留"}},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n4", "data": {"port": "2"}},
                {"id": "e3", "source": "n1", "target": "n3"},
            ],
        },
        "layout_json": {"n1": {"x": 1, "y": 1}, "n4": {"x": 2, "y": 2}},
        "source_hash": "new-hash",
    }

    summary = views._build_change_summary(before, after, change_type="config_sync")

    for expected in (
        "名称变更",
        "新增节点 1 个",
        "删除节点 1 个",
        "修改节点 1 个",
        "新增连线 1 个",
        "删除连线 1 个",
        "修改连线 1 个",
        "调整 1 个节点布局",
        "设备配置已同步",
    ):
        assert expected in summary
    assert (
        views._build_change_summary(after, after, change_type="manual_save")
        == "手动保存拓扑"
    )


class _ExistsQuery:
    def __init__(self, exists=False):
        self.exists_result = exists

    def exclude(self, **_kwargs):
        return self

    def using_db(self, _connection):
        return self

    async def exists(self):
        return self.exists_result


class _TopologyRow:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.saved = []

    async def save(self, **kwargs):
        self.saved.append(kwargs)

    @property
    def json(self):
        topology = self.topology_json or {}
        return {
            "id": self.id,
            "name": self.name,
            "topology_json": topology,
            "layout_json": self.layout_json,
            "node_count": len(topology.get("nodes", [])),
            "edge_count": len(topology.get("edges", [])),
            "revision": self.revision,
            "source_hash": self.source_hash,
            "last_sync_at": self.last_sync_at,
        }


class _Transaction:
    async def __aenter__(self):
        return "connection-1"

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_create_topology_defaults_to_one_blank_data_resource(monkeypatch):
    from services.network_topology import views

    created = []
    versions = []
    filters = []

    class FakeNetworkTopology:
        @classmethod
        def filter(cls, **kwargs):
            filters.append(kwargs)
            return _ExistsQuery()

        @classmethod
        async def create(cls, using_db=None, **kwargs):
            created.append(kwargs)
            return _TopologyRow(id="topology-1", **kwargs)

    class FakeNetworkTopologyVersion:
        @classmethod
        async def create(cls, using_db=None, **kwargs):
            versions.append({"using_db": using_db, **kwargs})
            return SimpleNamespace(**kwargs)

    monkeypatch.setattr(views, "NetworkTopology", FakeNetworkTopology)
    monkeypatch.setattr(views, "NetworkTopologyVersion", FakeNetworkTopologyVersion)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    result = asyncio.run(
        inspect.unwrap(views.create_topology)(
            _request(
                {
                    "name": " 总部网络 ",
                    "action_sources": [{"action_id": "obsolete"}],
                    "refresh_interval": 60,
                    "enabled": True,
                }
            )
        )
    )

    assert result["id"] == "topology-1"
    assert filters == [{"name": "总部网络", "deleted": False, "tenant_id": "tenant-1"}]
    assert created[0]["name"] == "总部网络"
    assert created[0]["source_config"] == ""
    assert created[0]["revision"] == 1
    assert created[0]["topology_json"]["nodes"][0]["id"] == "node-1"
    assert created[0]["topology_json"]["nodes"][0]["data"]["title"] == "新节点"
    assert created[0]["layout_json"] == {"node-1": {"x": 0.0, "y": 0.0}}
    assert versions[0]["using_db"] == "connection-1"
    assert versions[0]["version"] == 1
    assert versions[0]["change_type"] == "create"
    assert versions[0]["change_summary"] == "新增节点 1 个"
    assert "source_config" not in versions[0]

    custom_result = asyncio.run(
        inspect.unwrap(views.create_topology)(
            _request(
                {
                    "name": "分支网络",
                    "topology_json": {
                        "nodes": [{"id": "branch", "position": {"x": 7, "y": 9}}],
                        "edges": [],
                    },
                }
            )
        )
    )
    assert custom_result["topology_json"]["nodes"][0]["id"] == "branch"
    assert created[1]["layout_json"] == {"branch": {"x": 7.0, "y": 9.0}}

    empty_response = asyncio.run(
        inspect.unwrap(views.create_topology)(
            _request(
                {
                    "name": "空拓扑",
                    "topology_json": {"nodes": [], "edges": []},
                }
            )
        )
    )
    assert empty_response.status == 400
    assert "拓扑图至少需要一个节点" in orjson.loads(empty_response.body)["message"]
    assert len(created) == 2
    assert len(versions) == 2


def test_update_topology_rejects_zero_nodes(monkeypatch):
    from services.network_topology import views

    row = _TopologyRow(
        id="topology-1",
        name="总部网络",
        source_config="",
        source_hash=None,
        topology_json={"nodes": [{"id": "core"}], "edges": []},
        layout_json={"core": {"x": 0, "y": 0}},
        revision=1,
        last_sync_at=None,
        tenant_id="tenant-1",
    )

    async def tenant_topology(*_args, **_kwargs):
        return row

    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())

    response = asyncio.run(
        inspect.unwrap(views.update_topology)(
            _request(
                {
                    "id": "topology-1",
                    "revision": 1,
                    "topology_json": {"nodes": [], "edges": []},
                    "layout_json": {},
                }
            )
        )
    )

    assert response.status == 400
    assert "拓扑图至少需要一个节点" in orjson.loads(response.body)["message"]
    assert row.saved == []


def test_manual_save_rejects_zero_nodes_without_changing_config_sync_normalization():
    from services.network_topology import views

    response = asyncio.run(
        views._save_graph(
            _request(
                {
                    "name": "空拓扑",
                    "topology_json": {"nodes": [], "edges": []},
                    "layout_json": {},
                }
            )
        )
    )

    assert response.status == 400
    assert "拓扑图至少需要一个节点" in orjson.loads(response.body)["message"]
    graph, layout = views._normalize_graph({"nodes": [], "edges": []}, {})
    assert graph == {"nodes": [], "edges": []}
    assert layout == {}


def test_config_sync_is_idempotent_for_same_config_and_scoped_to_tenant(monkeypatch):
    from services.network_topology import views

    config = {"devices": [{"hostname": "core"}]}
    canonical = orjson.dumps(config, option=orjson.OPT_SORT_KEYS)
    row = _TopologyRow(
        id="topology-1",
        name="总部网络",
        source_config="stored",
        source_hash=hashlib.sha256(canonical).hexdigest(),
        topology_json={"nodes": [], "edges": []},
        layout_json={},
        revision=4,
        last_sync_at=None,
    )
    lookups = []

    async def tenant_topology(topology_id, tenant_id, **kwargs):
        lookups.append((topology_id, tenant_id, kwargs))
        return row

    async def unexpected_analysis(*_args, **_kwargs):
        raise AssertionError("unchanged config must not be analyzed")

    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "analyze_topology", unexpected_analysis)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    result = asyncio.run(
        inspect.unwrap(views.sync_topology_config)(
            _request({"id": "topology-1", "config": config})
        )
    )

    assert result["changed"] is False
    assert result["revision"] == 4
    assert lookups == [("topology-1", "tenant-1", {})]
    assert row.saved == []


def test_config_sync_analyzes_then_commits_graph_with_revision_cas(monkeypatch):
    from services.network_topology import views

    row = _TopologyRow(
        id="topology-1",
        name="总部网络",
        source_config="",
        source_hash=None,
        topology_json={"nodes": [], "edges": []},
        layout_json={},
        revision=2,
        last_sync_at=None,
        tenant_id="tenant-1",
    )
    lookups = []
    analyzed = []
    versions = []

    async def tenant_topology(topology_id, tenant_id, **kwargs):
        lookups.append((topology_id, tenant_id, kwargs))
        return row

    async def analyze(config_text, *, tenant_id):
        analyzed.append((config_text, tenant_id))
        return {"nodes": [{"id": "core"}], "edges": []}

    class FakeNetworkTopologyVersion:
        @classmethod
        async def create(cls, using_db=None, **kwargs):
            versions.append({"using_db": using_db, **kwargs})
            return SimpleNamespace(**kwargs)

    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "NetworkTopologyVersion", FakeNetworkTopologyVersion)
    monkeypatch.setattr(views, "analyze_topology", analyze)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    result = asyncio.run(
        inspect.unwrap(views.sync_topology_config)(
            _request(
                {
                    "id": "topology-1",
                    "revision": 2,
                    "config": "hostname core",
                }
            )
        )
    )

    assert result["changed"] is True
    assert result["revision"] == 3
    assert analyzed == [("hostname core", "tenant-1")]
    assert lookups == [
        ("topology-1", "tenant-1", {}),
        (
            "topology-1",
            "tenant-1",
            {"connection": "connection-1", "lock": True},
        ),
    ]
    assert row.source_config == "hostname core"
    assert row.topology_json["nodes"][0]["id"] == "core"
    assert row.saved[0]["using_db"] == "connection-1"
    assert "source_config" in row.saved[0]["update_fields"]
    assert versions[0]["using_db"] == "connection-1"
    assert versions[0]["version"] == 3
    assert versions[0]["change_type"] == "config_sync"
    assert versions[0]["change_summary"] == "新增节点 1 个；设备配置已同步"
    assert "source_config" not in versions[0]


def test_topology_version_routes_validate_parent_and_scope_queries_to_tenant(
    monkeypatch,
):
    from services.network_topology import views

    events = []
    version_detail = {
        "id": "version-2",
        "version_id": "version-2",
        "topology_id": "topology-1",
        "version": 2,
        "name": "总部网络",
        "topology_json": {"nodes": [{"id": "core"}], "edges": []},
        "layout_json": {"core": {"x": 1, "y": 2}},
        "source_hash": "hash-2",
        "change_type": "manual_save",
        "change_summary": "修改节点 1 个",
        "node_count": 1,
        "edge_count": 0,
        "ctime": "2026-07-21 09:00:00",
    }

    async def tenant_topology(topology_id, tenant_id, **_kwargs):
        events.append(("parent", topology_id, tenant_id))
        return SimpleNamespace(id=topology_id)

    class FakeVersionQuery:
        def annotate(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        async def values(self, *_args):
            events.append(("values",))
            return [
                {
                    "id": "version-2",
                    "topology_id": "topology-1",
                    "version": 2,
                    "change_type": "manual_save",
                    "change_summary": "修改节点 1 个",
                    "node_count": 1,
                    "edge_count": 0,
                    "ctime": datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
                }
            ]

        async def first(self):
            events.append(("first",))
            return SimpleNamespace(json=version_detail)

    class FakeNetworkTopologyVersion:
        @classmethod
        def filter(cls, **kwargs):
            events.append(("versions", kwargs))
            return FakeVersionQuery()

    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "NetworkTopologyVersion", FakeNetworkTopologyVersion)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    versions = asyncio.run(
        inspect.unwrap(views.list_topology_versions)(_request({"id": "topology-1"}))
    )
    detail = asyncio.run(
        inspect.unwrap(views.get_topology_version_detail)(
            _request({"id": "topology-1", "version_id": "version-2"})
        )
    )

    assert versions[0]["version_id"] == "version-2"
    assert versions[0]["version"] == 2
    assert "topology_json" not in versions[0]
    assert detail == version_detail
    version_queries = [event[1] for event in events if event[0] == "versions"]
    assert version_queries == [
        {
            "topology_id": "topology-1",
            "deleted": False,
            "tenant_id": "tenant-1",
        },
        {
            "id": "version-2",
            "topology_id": "topology-1",
            "deleted": False,
            "tenant_id": "tenant-1",
        },
    ]
    assert events.index(("parent", "topology-1", "tenant-1")) < next(
        index for index, event in enumerate(events) if event[0] == "versions"
    )
    assert "source_config" not in detail


def test_topology_version_routes_do_not_query_versions_for_foreign_parent(monkeypatch):
    from services.network_topology import views

    async def missing_parent(*_args, **_kwargs):
        return None

    class UnexpectedVersionQuery:
        @classmethod
        def filter(cls, **_kwargs):
            raise AssertionError(
                "version rows must not be queried before parent validation"
            )

    monkeypatch.setattr(views, "_tenant_topology", missing_parent)
    monkeypatch.setattr(views, "NetworkTopologyVersion", UnexpectedVersionQuery)

    response = asyncio.run(
        inspect.unwrap(views.get_topology_version_detail)(
            _request(
                {"id": "foreign-topology", "version_id": "foreign-version"},
                tenant_id="tenant-1",
            )
        )
    )

    assert response.status == 404
    assert "拓扑数据不存在" in orjson.loads(response.body)["message"]


def _source_aggregate(result):
    return {
        "schema_version": 1,
        "sources": [
            {
                "action_id": "action-1",
                "resources": [{"resource_id": "resource-1", "result": result}],
            }
        ],
    }


def test_topology_analysis_uses_stable_ids_and_only_explicit_connection_evidence():
    from services.network_topology.analyzer import _parse_topology

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    node_evidence = "hostname core-switch and edge-switch management 10.0.0.2"
    edge_evidence = "show lldp neighbors: Gi0/1 -> edge"
    topology = _parse_topology(
        orjson.dumps(
            {
                "nodes": [
                    {
                        "id": "model-core-a",
                        "serial": "SERIAL-CORE",
                        "policies": ["允许管理网段"],
                        "source_refs": [source_ref],
                        "evidence": [node_evidence],
                    },
                    {
                        "id": "model-core-b",
                        "serial": "SERIAL-CORE",
                        "policies": ["限制访客 VLAN"],
                        "source_refs": [source_ref],
                        "evidence": [node_evidence],
                    },
                    {
                        "id": "model-edge",
                        "management_ip": "10.0.0.2/24",
                        "source_refs": [source_ref],
                        "evidence": [node_evidence],
                    },
                ],
                "edges": [
                    {"source": "model-core-a", "target": "model-edge"},
                    {
                        "source": "model-core-b",
                        "target": "model-edge",
                        "port": "Gi0/1",
                        "evidence_type": "lldp",
                        "evidence": [edge_evidence],
                        "source_refs": [source_ref],
                    },
                ],
            }
        ).decode(),
        source_aggregate=_source_aggregate(f"{node_evidence}\n{edge_evidence}"),
    )

    assert [node["id"] for node in topology["nodes"]] == [
        "10.0.0.2",
        "SERIAL-CORE",
    ]
    core = next(node for node in topology["nodes"] if node["id"] == "SERIAL-CORE")
    assert core["policies"] == ["允许管理网段", "限制访客 VLAN"]
    assert len(topology["edges"]) == 1
    assert topology["edges"][0]["evidence"] == [edge_evidence]


def test_topology_analysis_binds_plain_external_config_without_legacy_source_wrapper():
    from services.network_topology.analyzer import _parse_topology

    node_evidence = "hostname core-switch serial CORE-001"
    edge_evidence = "show lldp neighbors: Gi0/1 -> edge-switch"
    topology = _parse_topology(
        orjson.dumps(
            {
                "nodes": [
                    {
                        "id": "core",
                        "serial": "CORE-001",
                        "evidence": [node_evidence],
                    },
                    {
                        "id": "edge",
                        "serial": "EDGE-001",
                        "evidence": [edge_evidence],
                    },
                ],
                "edges": [
                    {
                        "source": "core",
                        "target": "edge",
                        "port": "Gi0/1",
                        "evidence_type": "lldp",
                        "evidence": [edge_evidence],
                    }
                ],
            }
        ).decode(),
        source_aggregate=f"{node_evidence}\n{edge_evidence}",
    )

    external_ref = {
        "action_id": "external-sync",
        "resource_id": "external-config",
    }
    assert [node["id"] for node in topology["nodes"]] == [
        "CORE-001",
        "EDGE-001",
    ]
    assert all(node["source_refs"] == [external_ref] for node in topology["nodes"])
    assert len(topology["edges"]) == 1
    assert topology["edges"][0]["source_refs"] == [external_ref]


def test_topology_nodes_and_edges_require_bound_locatable_evidence():
    from services.network_topology.analyzer import _parse_topology

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    node_evidence = "hostname real-switch serial REAL-001"
    edge_evidence = "show lldp neighbors: Gi0/4 connects real-switch to edge-switch"
    topology = _parse_topology(
        orjson.dumps(
            {
                "nodes": [
                    {
                        "id": "real",
                        "serial": "REAL-001",
                        "source_refs": [source_ref],
                        "evidence": [node_evidence],
                    },
                    {
                        "id": "edge",
                        "serial": "EDGE-001",
                        "source_refs": [source_ref],
                        "evidence": [edge_evidence],
                    },
                    {
                        "id": "phantom",
                        "serial": "PHANTOM-999",
                        "source_refs": [source_ref],
                        "evidence": ["not present in collected output"],
                    },
                ],
                "edges": [
                    {
                        "source": "real",
                        "target": "edge",
                        "port": "Gi0/4",
                        "evidence_type": "lldp",
                        "source_refs": [source_ref],
                        "evidence": [edge_evidence],
                    },
                    {
                        "source": "real",
                        "target": "edge",
                        "port": "Gi0/5",
                        "evidence_type": "lldp",
                        "source_refs": [
                            {"action_id": "unknown", "resource_id": "resource-1"}
                        ],
                        "evidence": [edge_evidence],
                    },
                ],
            }
        ).decode(),
        source_aggregate=_source_aggregate(f"{node_evidence}\n{edge_evidence}"),
    )

    assert [node["id"] for node in topology["nodes"]] == ["EDGE-001", "REAL-001"]
    assert len(topology["edges"]) == 1
    assert topology["edges"][0]["port"] == "Gi0/4"


def test_topology_rejects_generic_edge_evidence_and_keeps_network_lines():
    from services.network_topology.analyzer import _parse_topology

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    concrete = "show lldp neighbors: Gi0/10 -> edge-switch-01"
    topology = _parse_topology(
        orjson.dumps(
            {
                "nodes": [
                    {
                        "id": "core",
                        "serial": "CORE-001",
                        "source_refs": [source_ref],
                        "evidence": [concrete],
                    },
                    {
                        "id": "edge",
                        "serial": "EDGE-001",
                        "source_refs": [source_ref],
                        "evidence": [concrete],
                    },
                ],
                "edges": [
                    {
                        "source": "core",
                        "target": "edge",
                        "port": "generic",
                        "evidence_type": "lldp",
                        "source_refs": [source_ref],
                        "evidence": ["lldp neighbor"],
                    },
                    {
                        "source": "core",
                        "target": "edge",
                        "port": "Gi0/10",
                        "evidence_type": "lldp",
                        "source_refs": [source_ref],
                        "evidence": [concrete],
                    },
                ],
            }
        ).decode(),
        source_aggregate=_source_aggregate(f"lldp neighbor\n{concrete}"),
    )

    assert [edge["port"] for edge in topology["edges"]] == ["Gi0/10"]


def test_topology_policies_and_evidence_redact_network_credentials():
    from services.network_topology.analyzer import _parse_topology

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    evidence = "lldp neighbor Gi0/1 password edge-password"
    private_key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "private-key-material\n"
        "-----END RSA PRIVATE KEY-----"
    )
    source_result = "\n".join(
        [
            evidence,
            "password admin-password",
            "snmp-server community public-community RO",
            private_key,
        ]
    )
    topology = _parse_topology(
        orjson.dumps(
            {
                "nodes": [
                    {
                        "id": "core",
                        "serial": "CORE-001",
                        "policies": [
                            "password admin-password",
                            "snmp-server community public-community RO",
                            private_key,
                        ],
                        "source_refs": [source_ref],
                        "evidence": [evidence],
                    },
                    {
                        "id": "edge",
                        "serial": "EDGE-001",
                        "source_refs": [source_ref],
                        "evidence": [evidence],
                    },
                ],
                "edges": [
                    {
                        "source": "core",
                        "target": "edge",
                        "evidence_type": "lldp",
                        "source_refs": [source_ref],
                        "evidence": [evidence],
                    }
                ],
            }
        ).decode(),
        source_aggregate=_source_aggregate(source_result),
    )
    serialized = orjson.dumps(topology).decode()

    for secret in (
        "admin-password",
        "public-community",
        "private-key-material",
        "edge-password",
    ):
        assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert "[REDACTED PRIVATE KEY]" in serialized
