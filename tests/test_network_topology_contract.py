# coding=utf-8
import ast
import asyncio
from datetime import datetime, timezone
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from tortoise import fields

from models.base import EncryptedTextField
from models.network_topology import NetworkTopology


ROOT = Path(__file__).resolve().parents[1]


def _request(body=None, *, tenant_id="tenant-1", account=None):
    return SimpleNamespace(
        json=body or {},
        ctx=SimpleNamespace(
            session={
                "tenant_id": tenant_id,
                "account_info": account,
            }
        ),
    )


def _route_contracts():
    source_path = ROOT / "services/network_topology/views.py"
    tree = ast.parse(source_path.read_text())
    contracts = {}
    for node in tree.body:
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
                    argument.value
                    for argument in decorator.args
                    if isinstance(argument, ast.Constant)
                )
        if route:
            contracts[route] = (node.name, permissions)
    return contracts


def test_network_topology_routes_use_platform_crud_permissions():
    contracts = _route_contracts()

    assert contracts == {
        "/source-options": (
            "source_options",
            ("network_topology:c", "network_topology:u"),
        ),
        "/parse": ("parse_topology", ("network_topology:r",)),
        "/save": ("save_topology", ("network_topology:u",)),
        "/create": ("create_topology", ("network_topology:c",)),
        "/update": ("update_topology", ("network_topology:u",)),
        "/list": ("list_topology", ("network_topology:r",)),
        "/detail": ("get_topology", ("network_topology:r",)),
        "/definition": (
            "get_topology_definition",
            ("network_topology:u",),
        ),
        "/refresh": ("refresh_topology", ("network_topology:u",)),
        "/layout": ("update_layout", ("network_topology:u",)),
        "/delete": ("delete_topology", ("network_topology:d",)),
    }
    assert (
        "network_topology:w"
        not in (ROOT / "services/network_topology/views.py").read_text()
    )


def test_network_topology_model_has_automation_and_encrypted_source_fields():
    fields_map = NetworkTopology._meta.fields_map

    assert isinstance(fields_map["source_config"], EncryptedTextField)
    assert fields_map["source_config"].default == ""
    assert isinstance(fields_map["action_sources"], fields.JSONField)
    assert fields_map["action_sources"].default is list
    assert isinstance(fields_map["refresh_interval"], fields.IntField)
    assert fields_map["refresh_interval"].default == 3600
    assert isinstance(fields_map["enabled"], fields.BooleanField)
    assert fields_map["enabled"].default is True
    assert isinstance(fields_map["executor_id"], fields.CharField)
    assert fields_map["executor_id"].null is True
    assert isinstance(fields_map["source_hash"], fields.CharField)
    assert fields_map["source_hash"].null is True
    assert isinstance(fields_map["revision"], fields.IntField)
    assert fields_map["revision"].default == 1
    assert isinstance(fields_map["last_run_ids"], fields.JSONField)
    assert fields_map["last_run_ids"].default is list
    assert isinstance(fields_map["refresh_status"], fields.CharField)
    assert fields_map["refresh_status"].default == "pending"
    for field_name in (
        "last_refresh_at",
        "next_refresh_at",
        "refresh_started_at",
    ):
        assert isinstance(fields_map[field_name], fields.DatetimeField)
        assert fields_map[field_name].null is True
    assert isinstance(fields_map["refresh_error"], fields.TextField)
    assert fields_map["refresh_error"].null is True
    assert isinstance(fields_map["refresh_token"], fields.CharField)
    assert fields_map["refresh_token"].null is True


def test_network_topology_json_never_leaks_source_config_and_formats_dates():
    topology = NetworkTopology(
        id="topology-1",
        tenant_id="tenant-1",
        name="总部网络",
        source_config="plaintext-secret-device-config",
        topology_json={"nodes": [{"id": "core"}], "edges": []},
        action_sources=[{"action_id": "action-1"}],
        ctime=datetime(2026, 7, 19, 10, 11, 12, 345678, tzinfo=timezone.utc),
        utime=datetime(2026, 7, 19, 10, 12, 13, 999999, tzinfo=timezone.utc),
    )

    payload = topology.json

    assert "source_config" not in payload
    assert "action_sources" not in payload
    assert "plaintext-secret-device-config" not in repr(payload)
    assert payload["ctime"] == "2026-07-19 18:11:12"
    assert payload["utime"] == "2026-07-19 18:12:13"
    assert payload["node_count"] == 1
    assert payload["edge_count"] == 0


def test_detail_and_definition_separate_graph_data_from_editable_source_metadata(
    monkeypatch,
):
    from services.network_topology import views

    action_sources = [
        {
            "action_id": "action-1",
            "resource_ids": ["resource-1"],
            "action_input": {"scope": "all"},
            "tool_name": "network-device-app",
            "tool_version": "1.0.0",
            "tool_label": "网络设备应用",
            "action_name": "获取运行配置",
            "action_func": "show_running_config",
            "resource_names": ["核心交换机"],
        }
    ]
    topology = _TopologyRow(
        id="topology-1",
        name="总部网络",
        refresh_interval=600,
        enabled=True,
        revision=4,
        action_sources=action_sources,
        source_hash="source-hash-1",
        payload={
            "id": "topology-1",
            "name": "总部网络",
            "topology_json": {"nodes": [{"id": "core"}], "edges": []},
            "layout_json": {"core": {"x": 10, "y": 20}},
            "action_sources": action_sources,
            "last_run_ids": ["run-1"],
            "refresh_status": "success",
            "ctime": "2026-07-19 18:11:12",
            "utime": "2026-07-19 18:12:13",
        },
    )
    lookups = []

    async def tenant_topology(topology_id, tenant_id, **kwargs):
        lookups.append((topology_id, tenant_id, kwargs))
        return topology

    async def definition_secret_keys(sources, tenant_id):
        assert sources == action_sources
        assert tenant_id == "tenant-1"
        return {}

    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "_definition_secret_keys", definition_secret_keys)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    detail = asyncio.run(
        inspect.unwrap(views.get_topology)(_request({"id": "topology-1"}))
    )
    definition = asyncio.run(
        inspect.unwrap(views.get_topology_definition)(_request({"id": "topology-1"}))
    )

    assert detail["topology_json"] == {
        "nodes": [{"id": "core"}],
        "edges": [],
    }
    assert detail["layout_json"] == {"core": {"x": 10, "y": 20}}
    assert detail["source_hash"] == "source-hash-1"
    for definition_only_field in (
        "source_config",
        "action_sources",
        "last_run_ids",
    ):
        assert definition_only_field not in detail

    assert definition == {
        "id": "topology-1",
        "name": "总部网络",
        "refresh_interval": 600,
        "enabled": True,
        "revision": 4,
        "action_sources": action_sources,
    }
    assert "source_config" not in definition
    assert "topology_json" not in definition
    assert "layout_json" not in definition
    assert "last_run_ids" not in definition
    assert len(lookups) == 2
    detail_fields = set(lookups[0][2]["fields"])
    definition_fields = set(lookups[1][2]["fields"])
    assert {
        "topology_json",
        "layout_json",
        "source_hash",
        "ctime",
        "utime",
    } <= detail_fields
    assert "source_config" not in detail_fields
    assert "action_sources" not in detail_fields
    assert definition_fields == {
        "id",
        "name",
        "refresh_interval",
        "enabled",
        "revision",
        "action_sources",
    }


def test_layout_normalization_requires_exact_nodes_and_finite_coordinates():
    from services.network_topology import views

    topology_json = {
        "nodes": [{"id": "core"}, {"id": "edge"}],
        "edges": [],
    }

    assert views._normalize_layout(
        {
            "core": {"x": 10, "y": -20.5},
            "edge": {"x": 0.25, "y": 30},
        },
        topology_json,
    ) == {
        "core": {"x": 10.0, "y": -20.5},
        "edge": {"x": 0.25, "y": 30.0},
    }

    invalid_layouts = [
        {"core": {"x": 1, "y": 2}},
        {
            "core": {"x": 1, "y": 2},
            "edge": {"x": 3, "y": 4},
            "stale-node": {"x": 5, "y": 6},
        },
        {"core": {"x": True, "y": 2}, "edge": {"x": 3, "y": 4}},
        {"core": {"x": float("nan"), "y": 2}, "edge": {"x": 3, "y": 4}},
        {"core": {"x": 1, "y": 2}, "edge": {"x": float("inf"), "y": 4}},
        {
            "core": {"x": views._MAX_LAYOUT_COORDINATE + 1, "y": 2},
            "edge": {"x": 3, "y": 4},
        },
        {"core": [1, 2], "edge": {"x": 3, "y": 4}},
    ]
    for layout in invalid_layouts:
        with pytest.raises(ValueError):
            views._normalize_layout(layout, topology_json)


def test_layout_update_locks_tenant_row_and_rejects_stale_source_hash(monkeypatch):
    from services.network_topology import views

    topology = _MutableTopology()
    topology.source_hash = "current-source-hash"
    topology.topology_json = {
        "nodes": [{"id": "core"}, {"id": "edge"}],
        "edges": [],
    }
    topology.layout_json = {
        "core": {"x": 1.0, "y": 2.0},
        "edge": {"x": 3.0, "y": 4.0},
    }
    lookups = []

    async def tenant_topology(topology_id, tenant_id, **kwargs):
        lookups.append((topology_id, tenant_id, kwargs))
        return topology

    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())
    monkeypatch.setattr(views, "response_ok", lambda data: data)
    monkeypatch.setattr(
        views,
        "_err",
        lambda message, status=400: {"message": message, "status": status},
    )

    stale_result = asyncio.run(
        inspect.unwrap(views.update_layout)(
            _request(
                {
                    "id": "topology-1",
                    "source_hash": "stale-source-hash",
                    "layout_json": {
                        "core": {"x": 100, "y": 200},
                        "edge": {"x": 300, "y": 400},
                    },
                }
            )
        )
    )

    assert stale_result == {
        "message": "拓扑已自动刷新，请重新打开后保存布局",
        "status": 409,
    }
    assert topology.layout_json == {
        "core": {"x": 1.0, "y": 2.0},
        "edge": {"x": 3.0, "y": 4.0},
    }
    assert topology.saved is None

    saved_result = asyncio.run(
        inspect.unwrap(views.update_layout)(
            _request(
                {
                    "id": "topology-1",
                    "source_hash": "current-source-hash",
                    "layout_json": {
                        "core": {"x": 100, "y": 200.5},
                        "edge": {"x": -300, "y": 400},
                    },
                }
            )
        )
    )

    assert saved_result == {
        "id": "topology-1",
        "source_hash": "current-source-hash",
    }
    assert topology.layout_json == {
        "core": {"x": 100.0, "y": 200.5},
        "edge": {"x": -300.0, "y": 400.0},
    }
    assert topology.saved == {
        "using_db": "connection-1",
        "update_fields": ["layout_json", "utime"],
    }
    assert lookups == [
        (
            "topology-1",
            "tenant-1",
            {"connection": "connection-1", "lock": True},
        ),
        (
            "topology-1",
            "tenant-1",
            {"connection": "connection-1", "lock": True},
        ),
    ]


class _ListQuery:
    def __init__(self, items):
        self.items = items
        self.filter_calls = []
        self.ordering = None
        self.offset_value = None
        self.limit_value = None
        self.annotations = {}
        self.value_fields = ()

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        return self

    async def count(self):
        return len(self.items)

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def annotate(self, **kwargs):
        self.annotations.update(kwargs)
        return self

    async def values(self, *fields):
        self.value_fields = fields
        return [
            {field: item.payload.get(field) for field in fields} for item in self.items
        ]


class _TopologyRow(SimpleNamespace):
    def __getattr__(self, name):
        payload = vars(self).get("payload", {})
        if name in payload:
            return payload[name]
        if name in {"last_refresh_at", "next_refresh_at", "refresh_error"}:
            return None
        raise AttributeError(name)

    @property
    def json(self):
        return dict(self.payload)


def test_network_topology_list_applies_name_filter_and_returns_safe_rows(monkeypatch):
    from services.network_topology import views

    row = _TopologyRow(
        payload={
            "id": "topology-1",
            "name": "总部核心网",
            "topology_json": {"nodes": [], "edges": []},
            "layout_json": {},
            "action_sources": [{"action_id": "action-1"}],
            "last_run_ids": ["run-1"],
            "source_count": 1,
            "node_count": 3,
            "edge_count": 2,
            "refresh_interval": 3600,
            "enabled": True,
            "revision": 1,
            "refresh_status": "success",
            "last_refresh_at": "2026-07-19 18:10:11",
            "next_refresh_at": "2026-07-19 19:10:11",
            "refresh_error": None,
            "ctime": "2026-07-19 18:11:12",
            "utime": "2026-07-19 18:12:13",
        }
    )
    query = _ListQuery([row])
    initial_filters = []

    class FakeNetworkTopology:
        @classmethod
        def filter(cls, **kwargs):
            initial_filters.append(kwargs)
            return query

    monkeypatch.setattr(views, "NetworkTopology", FakeNetworkTopology)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    result = asyncio.run(
        inspect.unwrap(views.list_topology)(
            _request({"name": "  核心  ", "page": 2, "page_size": 10})
        )
    )

    assert initial_filters == [{"deleted": False, "tenant_id": "tenant-1"}]
    assert query.filter_calls == [{"name__icontains": "核心"}]
    assert query.ordering == ("-utime", "-ctime")
    assert query.offset_value == 10
    assert query.limit_value == 10
    assert set(query.annotations) == {"source_count", "node_count", "edge_count"}
    assert "source_config" not in query.value_fields
    assert "topology_json" not in query.value_fields
    assert "layout_json" not in query.value_fields
    assert "action_sources" not in query.value_fields
    assert "last_run_ids" not in query.value_fields
    assert result["total"] == 1
    assert len(result["items"]) == 1
    safe_row = result["items"][0]
    assert safe_row["id"] == "topology-1"
    assert safe_row["name"] == "总部核心网"
    assert safe_row["source_count"] == 1
    assert safe_row["node_count"] == 3
    assert safe_row["edge_count"] == 2
    assert safe_row["last_refresh_at"] == "2026-07-19 18:10:11"
    assert safe_row["ctime"] == "2026-07-19 18:11:12"
    assert safe_row["utime"] == "2026-07-19 18:12:13"
    for raw_field in (
        "source_config",
        "topology_json",
        "layout_json",
        "action_sources",
        "last_run_ids",
    ):
        assert raw_field not in safe_row


class _ExistsQuery:
    def __init__(self):
        self.excluded = []

    def exclude(self, **kwargs):
        self.excluded.append(kwargs)
        return self

    async def exists(self):
        return False


class _Transaction:
    async def __aenter__(self):
        return "connection-1"

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _MutableTopology:
    def __init__(self):
        self.id = "topology-1"
        self.name = "旧名称"
        self.action_sources = [{"action_id": "old-action"}]
        self.refresh_interval = 3600
        self.enabled = True
        self.executor_id = "old-account"
        self.revision = 3
        self.refresh_status = "success"
        self.next_refresh_at = None
        self.refresh_started_at = None
        self.refresh_token = None
        self.refresh_error = None
        self.saved = None

    async def save(self, **kwargs):
        self.saved = kwargs

    @property
    def json(self):
        return {
            "id": self.id,
            "name": self.name,
            "action_sources": self.action_sources,
            "revision": self.revision,
            "refresh_status": self.refresh_status,
        }


def test_network_topology_create_ignores_manual_config_and_starts_from_actions(
    monkeypatch,
):
    from services.network_topology import views

    duplicate_query = _ExistsQuery()
    create_calls = []

    class FakeNetworkTopology:
        @classmethod
        def filter(cls, **_kwargs):
            return duplicate_query

        @classmethod
        async def create(cls, **kwargs):
            create_calls.append(kwargs)
            return _TopologyRow(payload={"id": "topology-1", "name": kwargs["name"]})

    async def validate_sources(sources, tenant_id, account):
        assert sources == [{"action_id": "action-1"}]
        assert tenant_id == "tenant-1"
        assert account.id == "account-1"
        return [{"action_id": "action-1", "resource_ids": [], "action_input": {}}]

    monkeypatch.setattr(views, "NetworkTopology", FakeNetworkTopology)
    monkeypatch.setattr(views, "validate_action_sources", validate_sources)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    result = asyncio.run(
        inspect.unwrap(views.create_topology)(
            _request(
                {
                    "name": "总部网络",
                    "action_sources": [{"action_id": "action-1"}],
                    "refresh_interval": 600,
                    "enabled": True,
                    "source_config": "malicious pasted configuration",
                    "topology_json": {"nodes": [{"id": "manual"}], "edges": []},
                },
                account=SimpleNamespace(id="account-1"),
            )
        )
    )

    assert result == {"id": "topology-1", "name": "总部网络"}
    assert len(create_calls) == 1
    created = create_calls[0]
    assert created["source_config"] == ""
    assert created["topology_json"] == {"nodes": [], "edges": []}
    assert created["layout_json"] == {}
    assert created["action_sources"] == [
        {"action_id": "action-1", "resource_ids": [], "action_input": {}}
    ]
    assert created["executor_id"] == "account-1"
    assert created["refresh_status"] == "pending"
    assert created["tenant_id"] == "tenant-1"


def test_network_topology_update_mutates_existing_row_without_creating_duplicate(
    monkeypatch,
):
    from services.network_topology import views

    record = _MutableTopology()
    duplicate_query = _ExistsQuery()
    create_calls = []
    filter_calls = []

    class FakeNetworkTopology:
        @classmethod
        def filter(cls, **kwargs):
            filter_calls.append(kwargs)
            return duplicate_query

        @classmethod
        async def create(cls, **kwargs):
            create_calls.append(kwargs)
            raise AssertionError("update must not create a new topology row")

    async def validate_sources(
        sources,
        tenant_id,
        account,
        *,
        existing_action_sources=None,
    ):
        assert sources == [{"action_id": "new-action"}]
        assert tenant_id == "tenant-1"
        assert account.id == "account-1"
        assert existing_action_sources == [{"action_id": "old-action"}]
        return [{"action_id": "new-action", "application": "network-app"}]

    async def tenant_topology(topology_id, tenant_id, **kwargs):
        assert topology_id == "topology-1"
        assert tenant_id == "tenant-1"
        assert kwargs == {"connection": "connection-1", "lock": True}
        return record

    monkeypatch.setattr(views, "NetworkTopology", FakeNetworkTopology)
    monkeypatch.setattr(views, "validate_action_sources", validate_sources)
    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "in_transaction", lambda: _Transaction())
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    result = asyncio.run(
        inspect.unwrap(views.update_topology)(
            _request(
                {
                    "id": "topology-1",
                    "name": "新名称",
                    "action_sources": [{"action_id": "new-action"}],
                    "refresh_interval": 600,
                    "enabled": True,
                    "revision": 3,
                },
                account=SimpleNamespace(id="account-1"),
            )
        )
    )

    assert create_calls == []
    assert filter_calls == [
        {"name": "新名称", "deleted": False, "tenant_id": "tenant-1"}
    ]
    assert duplicate_query.excluded == [{"id": "topology-1"}]
    assert record.name == "新名称"
    assert record.action_sources == [
        {"action_id": "new-action", "application": "network-app"}
    ]
    assert record.refresh_interval == 600
    assert record.executor_id == "account-1"
    assert record.revision == 4
    assert record.refresh_status == "pending"
    assert record.saved["using_db"] == "connection-1"
    assert result["id"] == "topology-1"
    assert result["revision"] == 4


class _RefreshQuery:
    def __init__(self, state, conditions):
        self.state = state
        self.conditions = dict(conditions)

    def filter(self, **kwargs):
        self.conditions.update(kwargs)
        return self

    async def first(self):
        self.state["read_filters"].append(dict(self.conditions))
        return self.state["topology"]

    async def update(self, **kwargs):
        self.state["updates"].append(
            {"filters": dict(self.conditions), "values": dict(kwargs)}
        )
        return self.state.get("update_count", 1)


class _RefreshModel:
    state = None

    @classmethod
    def filter(cls, **kwargs):
        return _RefreshQuery(cls.state, kwargs)


class _AwaitableAccount:
    def __init__(self, account):
        self.account = account

    def prefetch_related(self, *_relations):
        return self

    def __await__(self):
        async def resolve():
            return self.account

        return resolve().__await__()


def _refresh_topology_row(*, source_hash):
    started_at = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id="topology-1",
        tenant_id="tenant-1",
        revision=7,
        refresh_status="running",
        refresh_started_at=started_at,
        refresh_token=None,
        executor_id="account-1",
        source_hash=source_hash,
        source_config="last-good-config",
        topology_json={"nodes": [{"id": "last-good"}], "edges": []},
        layout_json={"last-good": {"x": 10, "y": 20}},
        enabled=True,
        refresh_interval=600,
    )


def _install_refresh_fakes(monkeypatch, refresh, topology):
    state = {
        "topology": topology,
        "read_filters": [],
        "updates": [],
        "update_count": 1,
    }
    _RefreshModel.state = state

    class FakeAccount:
        @classmethod
        def get_or_none(cls, **kwargs):
            assert kwargs == {
                "id": "account-1",
                "deleted": False,
                "disabled": False,
            }
            return _AwaitableAccount(SimpleNamespace(id="account-1"))

    class MembershipQuery:
        async def exists(self):
            return True

    class FakeTenant:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs == {
                "id": "tenant-1",
                "deleted": False,
                "member__id": "account-1",
            }
            return MembershipQuery()

    monkeypatch.setattr(refresh, "NetworkTopology", _RefreshModel)
    monkeypatch.setattr(refresh, "Account", FakeAccount)
    monkeypatch.setattr(refresh, "Tenant", FakeTenant)
    return state


def test_normalized_unchanged_configuration_hash_skips_topology_analysis(monkeypatch):
    from services.network_topology import refresh

    first = {
        "schema_version": 1,
        "run_id": "run-old",
        "sources": [{"result": {"hostname": "core  \r\n", "vlans": [20, 10]}}],
    }
    second = {
        "sources": [{"result": {"vlans": [20, 10], "hostname": "core\n"}}],
        "run_id": "run-new",
        "schema_version": 1,
    }
    assert refresh._source_hash(first) == refresh._source_hash(second)

    topology = _refresh_topology_row(source_hash=refresh._source_hash(first))
    state = _install_refresh_fakes(monkeypatch, refresh, topology)
    analyze_calls = []

    async def collect_source_config(_topology, account):
        assert account.id == "account-1"
        return second, ["run-new"]

    async def analyze(*args, **kwargs):
        analyze_calls.append((args, kwargs))
        raise AssertionError(
            "unchanged source config must not trigger topology analysis"
        )

    monkeypatch.setattr(refresh, "_collect_source_config", collect_source_config)
    monkeypatch.setattr(refresh, "analyze_topology", analyze)

    result = asyncio.run(
        refresh.refresh_topology(
            topology.id,
            topology.revision,
            refresh_started_at=topology.refresh_started_at,
            tenant_id="tenant-1",
        )
    )

    assert analyze_calls == []
    assert result == {
        "id": "topology-1",
        "status": "success",
        "changed": False,
        "last_run_ids": ["run-new"],
    }
    assert len(state["updates"]) == 1
    assert state["read_filters"] == [
        {
            "id": "topology-1",
            "revision": 7,
            "refresh_status": "running",
            "deleted": False,
            "tenant_id": "tenant-1",
            "refresh_started_at": topology.refresh_started_at,
        }
    ]
    update = state["updates"][0]
    assert update["filters"]["tenant_id"] == "tenant-1"
    assert update["filters"]["revision"] == 7
    assert update["filters"]["refresh_started_at"] == topology.refresh_started_at
    assert update["values"]["refresh_status"] == "success"
    assert update["values"]["last_run_ids"] == ["run-new"]
    for last_good_field in (
        "source_config",
        "source_hash",
        "topology_json",
        "layout_json",
        "revision",
    ):
        assert last_good_field not in update["values"]


def test_failed_refresh_preserves_last_good_graph_hash_and_revision(monkeypatch):
    from services.network_topology import refresh

    topology = _refresh_topology_row(source_hash="last-good-hash")
    state = _install_refresh_fakes(monkeypatch, refresh, topology)

    async def collect_source_config(_topology, _account):
        return {"schema_version": 1, "sources": [{"result": "new config"}]}, [
            "run-failed-analysis"
        ]

    async def analyze(*_args, **_kwargs):
        raise RuntimeError("topology analyzer unavailable")

    monkeypatch.setattr(refresh, "_collect_source_config", collect_source_config)
    monkeypatch.setattr(refresh, "analyze_topology", analyze)

    result = asyncio.run(
        refresh.refresh_topology(
            topology.id,
            topology.revision,
            refresh_started_at=topology.refresh_started_at,
            tenant_id="tenant-1",
        )
    )

    assert result["status"] == "failed"
    assert result["changed"] is False
    assert result["last_run_ids"] == ["run-failed-analysis"]
    assert result["error"] == "topology analyzer unavailable"
    assert topology.source_config == "last-good-config"
    assert topology.source_hash == "last-good-hash"
    assert topology.topology_json == {
        "nodes": [{"id": "last-good"}],
        "edges": [],
    }
    assert topology.layout_json == {"last-good": {"x": 10, "y": 20}}
    assert topology.revision == 7

    assert len(state["updates"]) == 1
    failure_update = state["updates"][0]["values"]
    assert failure_update["refresh_status"] == "failed"
    assert failure_update["last_run_ids"] == ["run-failed-analysis"]
    assert failure_update["refresh_error"] == "topology analyzer unavailable"
    for last_good_field in (
        "source_config",
        "source_hash",
        "topology_json",
        "layout_json",
        "revision",
    ):
        assert last_good_field not in failure_update


def test_refresh_completion_cas_rejects_a_stale_revision_or_lease(monkeypatch):
    from services.network_topology import refresh

    topology = _refresh_topology_row(source_hash="last-good-hash")
    state = _install_refresh_fakes(monkeypatch, refresh, topology)
    state["update_count"] = 0

    collected = {"schema_version": 1, "sources": [{"result": "changed config"}]}

    async def collect_source_config(_topology, _account):
        return collected, ["run-stale"]

    analyze_calls = []

    async def analyze(*args, **kwargs):
        analyze_calls.append((args, kwargs))
        return {"nodes": [{"id": "new"}], "edges": []}

    monkeypatch.setattr(refresh, "_collect_source_config", collect_source_config)
    monkeypatch.setattr(refresh, "analyze_topology", analyze)

    result = asyncio.run(
        refresh.refresh_topology(
            topology.id,
            topology.revision,
            refresh_started_at=topology.refresh_started_at,
            tenant_id="tenant-1",
        )
    )

    assert result == {
        "id": "topology-1",
        "status": "superseded",
        "changed": False,
        "last_run_ids": ["run-stale"],
    }
    assert analyze_calls[0][1]["session_id"] == (
        f"network-topology:topology-1:r7:h{refresh._source_hash(collected)[:16]}"
    )
    assert len(state["updates"]) == 1
    cas_filter = state["updates"][0]["filters"]
    assert cas_filter["id"] == "topology-1"
    assert cas_filter["tenant_id"] == "tenant-1"
    assert cas_filter["revision"] == 7
    assert cas_filter["refresh_status"] == "running"
    assert cas_filter["refresh_started_at"] == topology.refresh_started_at
    assert "layout_json" not in state["updates"][0]["values"]
    assert topology.source_hash == "last-good-hash"
    assert topology.topology_json == {
        "nodes": [{"id": "last-good"}],
        "edges": [],
    }


def test_execute_claimed_refresh_propagates_tenant_to_read_and_cas_boundary(
    monkeypatch,
):
    from services.network_topology import refresh

    captured = []

    async def refresh_one(*args, **kwargs):
        captured.append((args, kwargs))
        return {"status": "success"}

    monkeypatch.setattr(refresh, "refresh_topology", refresh_one)
    started_at = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    claim = refresh.RefreshClaim(
        topology_id="topology-1",
        tenant_id="tenant-1",
        revision=7,
        refresh_started_at=started_at,
        refresh_token="lease-token-1",
    )

    result = asyncio.run(refresh.execute_claimed_refresh(claim))

    assert result == {"status": "success"}
    assert captured == [
        (
            ("topology-1", 7),
            {
                "refresh_started_at": started_at,
                "tenant_id": "tenant-1",
                "refresh_token": "lease-token-1",
            },
        )
    ]


def test_status_two_action_failure_is_terminal_and_does_not_wait(monkeypatch):
    from services.network_topology import refresh

    class RunlogQuery:
        def order_by(self, *_ordering):
            return self

        async def all(self):
            return [
                SimpleNamespace(
                    node_type="action-node",
                    tool_name="network-device-app",
                    action_name="show_running_config",
                    status=2,
                    result="sensitive device error details",
                )
            ]

    class FakeRunlog:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs == {
                "run_id": "run-failed",
                "tenant_id": "tenant-1",
                "deleted": False,
            }
            return RunlogQuery()

    sleep_calls = []

    async def sleep(_seconds):
        sleep_calls.append(_seconds)

    monkeypatch.setattr(refresh, "Runlog", FakeRunlog)
    monkeypatch.setattr(refresh.asyncio, "sleep", sleep)
    resolved_source = {
        "definition": {"resource_ids": []},
        "tool": SimpleNamespace(name="network-device-app"),
        "action_func": "show_running_config",
        "action": SimpleNamespace(action={"timeout": 60}),
    }

    with pytest.raises(refresh.TopologyRefreshError) as exc_info:
        asyncio.run(
            refresh._wait_for_action_run(
                "run-failed",
                resolved_source,
                "tenant-1",
            )
        )

    assert str(exc_info.value) == (
        "动作执行失败 run_id=run-failed action=show_running_config"
    )
    assert "sensitive device error details" not in str(exc_info.value)
    assert sleep_calls == []


def test_action_wait_ignores_unrelated_action_logs_on_a_colliding_run_id(
    monkeypatch,
):
    from services.network_topology import refresh

    expected_log = SimpleNamespace(
        node_type="action-node",
        tool_name="network-device-app",
        action_name="show_running_config",
        status=0,
        result="hostname core-switch",
    )

    class RunlogQuery:
        def order_by(self, *_ordering):
            return self

        async def all(self):
            return [
                SimpleNamespace(
                    node_type="action-node",
                    tool_name="another-app",
                    action_name="delete_configuration",
                    status=2,
                    result="unrelated failure",
                ),
                expected_log,
            ]

    class FakeRunlog:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs["run_id"] == "shared-run-id"
            return RunlogQuery()

    monkeypatch.setattr(refresh, "Runlog", FakeRunlog)
    resolved_source = {
        "definition": {"resource_ids": []},
        "tool": SimpleNamespace(name="network-device-app"),
        "action_func": "show_running_config",
        "action": SimpleNamespace(action={"timeout": 60}),
    }

    logs = asyncio.run(
        refresh._wait_for_action_run(
            "shared-run-id",
            resolved_source,
            "tenant-1",
        )
    )

    assert logs == [expected_log]


def test_action_source_execution_dispatches_application_action(monkeypatch):
    from services.network_topology import refresh

    produced = []

    async def produce(topic, message):
        produced.append((topic, message))

    async def wait_for_action_run(run_id, resolved_source, tenant_id):
        assert run_id == "run-action-1"
        assert resolved_source["action_func"] == "show_running_config"
        assert tenant_id == "tenant-1"
        return [
            SimpleNamespace(
                id="runlog-1",
                result="hostname core-switch\r\ninterface Gi0/1  ",
            )
        ]

    monkeypatch.setattr(refresh.uuid, "uuid4", lambda: "run-action-1")
    monkeypatch.setattr(refresh, "aproduce", produce)
    monkeypatch.setattr(refresh, "_wait_for_action_run", wait_for_action_run)

    resolved_source = {
        "index": 0,
        "definition": {
            "action_id": "action-1",
            "resource_ids": [],
            "action_input": {"scope": "all"},
        },
        "action": SimpleNamespace(
            id="action-1",
            action={
                "func": "show_running_config",
                "name": "获取运行配置",
                "timeout": 60,
                "retry_times": 2,
            },
        ),
        "action_func": "show_running_config",
        "action_name": "获取运行配置",
        "tool": SimpleNamespace(
            name="network-device-app",
            description="网络设备应用",
            version="1.0.0",
            main="main.py",
        ),
        "resources": [],
    }

    result = asyncio.run(
        refresh._execute_one_source(
            resolved_source,
            "tenant-1",
            "account-1",
            asyncio.Semaphore(1),
        )
    )

    assert len(produced) == 1
    topic, message = produced[0]
    assert topic == refresh.TASK_TOPIC
    assert message["run_type"] == "action"
    assert message["run_id"] == "run-action-1"
    assert message["tenant_id"] == "tenant-1"
    assert message["run_args"]["app_name"] == "network-device-app"
    assert message["run_args"]["action"] == "show_running_config"
    assert message["run_args"]["action_input"] == {"scope": "all"}
    assert message["run_args"]["encrypted_action_input_keys"] == []
    assert message["run_args"]["encrypt_action_result"] is True
    assert message["run_args"]["executor"] == "account-1"
    assert result["run_id"] == "run-action-1"
    assert result["resources"][0]["result"] == ("hostname core-switch\ninterface Gi0/1")


def test_encrypted_action_result_is_decrypted_only_for_topology_analysis(monkeypatch):
    import orjson

    from services.network_topology import refresh

    raw_result = {
        "running_config": "hostname core-switch\nsnmp-server community private-value"
    }
    serialized = orjson.dumps(raw_result).decode("utf-8")
    monkeypatch.setattr(refresh, "decrypt_aes256_gcm", lambda value: value[::-1])

    results = refresh._resource_results(
        {"resources": []},
        [
            SimpleNamespace(
                id="runlog-encrypted",
                result=f"{refresh._ENCRYPTED_RESULT_PREFIX}{serialized[::-1]}",
            )
        ],
    )

    assert results == [
        {
            "resource_id": "",
            "resource_name": "",
            "runlog_id": "runlog-encrypted",
            "result": raw_result,
        }
    ]


def test_action_password_is_encrypted_at_rest_and_placeholder_keeps_it(monkeypatch):
    from services.network_topology import refresh
    from services.network_topology import views

    action = SimpleNamespace(
        id="action-1",
        tool_name="network-device-app",
        version_number="1.0.0",
        action={
            "name": "获取配置",
            "func": "get_config",
            "classify": "query",
            "input": [
                {
                    "key": "credential",
                    "type": "password",
                    "required": True,
                }
            ],
        },
    )
    tool = SimpleNamespace(
        name="network-device-app",
        version="1.0.0",
        main="main.py",
        description="网络设备应用",
    )

    class FakeToolAction:
        @classmethod
        async def get_or_none(cls, **_kwargs):
            return action

    class FakeTool:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs["maintain"] is False
            return tool

    async def no_resource(_tool_name, _tenant_id):
        return False

    async def permitted(*_args, **_kwargs):
        return True

    monkeypatch.setattr(refresh, "ToolAction", FakeToolAction)
    monkeypatch.setattr(refresh, "Tool", FakeTool)
    monkeypatch.setattr(refresh, "tool_requires_resource", no_resource)
    monkeypatch.setattr(refresh, "check_action_execute_permission", permitted)
    monkeypatch.setattr(refresh, "encrypt_aes256_gcm", lambda value: value[::-1])
    monkeypatch.setattr(refresh, "decrypt_aes256_gcm", lambda value: value[::-1])

    existing_secret = refresh._encrypt_action_secret("TopSecret123")
    existing_sources = [
        {
            "action_id": "action-1",
            "resource_ids": [],
            "action_input": {"credential": existing_secret},
            "secret_keys": ["credential"],
        }
    ]
    normalized, resolved = asyncio.run(
        refresh._resolve_action_sources(
            [
                {
                    "action_id": "action-1",
                    "resource_ids": [],
                    "action_input": {"credential": "********"},
                }
            ],
            "tenant-1",
            SimpleNamespace(id="account-1", roles=[]),
            existing_action_sources=existing_sources,
        )
    )

    persisted_secret = normalized[0]["action_input"]["credential"]
    assert persisted_secret.startswith("enc::")
    assert "TopSecret123" not in repr(normalized)
    assert (
        refresh._decrypt_action_secret(
            persisted_secret,
            index=0,
            key="credential",
        )
        == "TopSecret123"
    )
    assert resolved[0]["definition"]["action_input"]["credential"] == "TopSecret123"
    action_params = refresh._build_action_params(resolved[0], "account-1")
    assert action_params["action_input"] == {"credential": persisted_secret}
    assert action_params["encrypted_action_input_keys"] == ["credential"]
    assert "TopSecret123" not in repr(action_params)
    assert views._redact_action_sources(normalized)[0]["action_input"] == {
        "credential": "********"
    }
    assert views._redact_action_sources(
        [
            {
                "action_id": "action-1",
                "action_input": {"neutral_name": "legacy-secret"},
            }
        ],
        {"action-1": {"neutral_name"}},
    )[0]["action_input"] == {"neutral_name": "********"}
    assert views._redact_action_sources(
        [
            {
                "action_id": "deleted-action",
                "action_input": {
                    "host": "192.0.2.1",
                    "custom_auth": "legacy-secret",
                },
            }
        ],
        {"deleted-action": None},
    )[0]["action_input"] == {
        "host": "********",
        "custom_auth": "********",
    }

    migrated, changed = refresh._encrypt_legacy_action_source(
        {
            "action_id": "action-1",
            "action_input": {"credential": "legacy-plaintext"},
        },
        {"input": [{"key": "replacement", "type": "string"}]},
    )
    assert changed is True
    assert "legacy-plaintext" not in repr(migrated)
    _migrated_again, changed_again = refresh._encrypt_legacy_action_source(
        migrated,
        action.action,
    )
    assert changed_again is False


def test_legacy_secret_migration_uses_source_definition_cas(monkeypatch):
    from services.network_topology import refresh

    legacy_sources = [
        {
            "action_id": "action-1",
            "action_input": {"password": "legacy-password"},
        }
    ]
    concurrent_sources = [
        {
            "action_id": "action-2",
            "action_input": {"password": "enc::new-password"},
        }
    ]
    topology_snapshot = SimpleNamespace(
        id="topology-concurrent",
        tenant_id="tenant-1",
        action_sources=legacy_sources,
        revision=4,
    )
    current_row = {
        "action_sources": concurrent_sources,
        "revision": 5,
    }
    update_calls = []

    class EmptyConnection:
        async def execute_query_dict(self, sql):
            assert 'FROM "network_topology"' in sql
            return []

    class TopologyQuery:
        def __init__(self, filters=None):
            self.filters = dict(filters or {})

        def only(self, *fields):
            assert fields == ("id", "tenant_id", "action_sources", "revision")
            return self

        async def all(self):
            return [topology_snapshot]

        async def update(self, **values):
            update_calls.append({"filters": self.filters, "values": values})
            if (
                self.filters.get("action_sources") != current_row["action_sources"]
                or self.filters.get("revision") != current_row["revision"]
            ):
                return 0
            current_row.update(values)
            return 1

    class FakeNetworkTopology:
        @classmethod
        def all(cls):
            return TopologyQuery()

        @classmethod
        def filter(cls, **filters):
            return TopologyQuery(filters)

    class EmptyActionQuery:
        def only(self, *_fields):
            return self

        async def all(self):
            return []

    class FakeToolAction:
        @classmethod
        def filter(cls, **_filters):
            return EmptyActionQuery()

    monkeypatch.setattr(refresh.connections, "get", lambda _name: EmptyConnection())
    monkeypatch.setattr(refresh, "NetworkTopology", FakeNetworkTopology)
    monkeypatch.setattr(refresh, "ToolAction", FakeToolAction)
    monkeypatch.setattr(
        refresh,
        "encrypt_aes256_gcm",
        lambda value: f"cipher:{value[::-1]}",
    )

    result = asyncio.run(refresh.migrate_legacy_topology_secrets())

    assert result == {"source_configs": 0, "action_inputs": 0}
    assert current_row == {
        "action_sources": concurrent_sources,
        "revision": 5,
    }
    assert update_calls[0]["filters"] == {
        "id": "topology-concurrent",
        "tenant_id": "tenant-1",
        "action_sources": legacy_sources,
        "revision": 4,
    }
    assert "legacy-password" not in repr(update_calls[0]["values"])


def test_legacy_secret_migration_covers_soft_deleted_rows_and_missing_actions(
    monkeypatch,
):
    from services.network_topology import refresh
    from services.network_topology import views

    legacy_sources = [
        {
            "action_id": "deleted-action",
            "action_input": {
                "credential": "legacy-credential",
                "password": "legacy-password",
                "access_token": "legacy-token",
                "snmp_community": "legacy-community",
                "private key": "legacy-private-key",
                "privateKey": "legacy-camel-private-key",
                "custom_auth": "legacy-custom-auth",
                "renamed_login": "legacy-renamed-secret",
                "wrapped_unknown": "enc::already-encrypted",
                "host": "192.0.2.1",
            },
            "secret_keys": ["renamed_login"],
        }
    ]
    deleted_topology = SimpleNamespace(
        id="topology-deleted",
        tenant_id="tenant-1",
        deleted=True,
        action_sources=legacy_sources,
        revision=9,
    )
    raw_queries = []
    raw_updates = []
    topology_updates = []
    action_filters = []

    class MigrationConnection:
        async def execute_query_dict(self, sql):
            raw_queries.append(sql)
            return [
                {
                    "id": "topology-deleted",
                    "source_config": "legacy-device-config",
                    "deleted": True,
                }
            ]

        async def execute_query(self, sql, params):
            raw_updates.append((sql, params))
            return 1, []

    class TopologyQuery:
        def __init__(self, filters=None):
            self.filters = dict(filters or {})

        def only(self, *fields):
            assert fields == ("id", "tenant_id", "action_sources", "revision")
            return self

        async def all(self):
            return [deleted_topology]

        async def update(self, **values):
            topology_updates.append({"filters": self.filters, "values": values})
            return 1

    class FakeNetworkTopology:
        @classmethod
        def all(cls):
            return TopologyQuery()

        @classmethod
        def filter(cls, **filters):
            return TopologyQuery(filters)

    class EmptyActionQuery:
        def only(self, *fields):
            assert fields == ("id", "action")
            return self

        async def all(self):
            return []

    class FakeToolAction:
        @classmethod
        def filter(cls, **filters):
            action_filters.append(filters)
            return EmptyActionQuery()

    def decrypt(value):
        assert value == "legacy-device-config"
        raise ValueError("plaintext")

    monkeypatch.setattr(
        refresh.connections,
        "get",
        lambda _name: MigrationConnection(),
    )
    monkeypatch.setattr(refresh, "NetworkTopology", FakeNetworkTopology)
    monkeypatch.setattr(refresh, "ToolAction", FakeToolAction)
    monkeypatch.setattr(refresh, "decrypt_aes256_gcm", decrypt)
    monkeypatch.setattr(
        refresh,
        "encrypt_aes256_gcm",
        lambda value: f"cipher:{value[::-1]}",
    )

    result = asyncio.run(refresh.migrate_legacy_topology_secrets())

    assert result == {"source_configs": 1, "action_inputs": 1}
    assert len(raw_queries) == 1
    assert '"deleted"' not in raw_queries[0]
    assert raw_updates[0][1] == (
        "cipher:gifnoc-ecived-ycagel",
        "topology-deleted",
        "legacy-device-config",
    )
    assert action_filters == [
        {
            "id__in": {"deleted-action"},
            "tenant_id": "tenant-1",
            "deleted": False,
        }
    ]
    assert topology_updates[0]["filters"] == {
        "id": "topology-deleted",
        "tenant_id": "tenant-1",
        "action_sources": legacy_sources,
        "revision": 9,
    }
    assert "deleted" not in topology_updates[0]["filters"]

    migrated_sources = topology_updates[0]["values"]["action_sources"]
    migrated_input = migrated_sources[0]["action_input"]
    expected_secret_keys = {
        "credential",
        "password",
        "access_token",
        "snmp_community",
        "private key",
        "privateKey",
        "custom_auth",
        "renamed_login",
        "wrapped_unknown",
        "host",
    }
    assert set(migrated_sources[0]["secret_keys"]) == expected_secret_keys
    assert migrated_input["wrapped_unknown"] == "enc::already-encrypted"
    for key in expected_secret_keys - {"wrapped_unknown"}:
        assert str(migrated_input[key]).startswith("enc::")
    serialized = repr(migrated_sources)
    for plaintext in (
        "legacy-credential",
        "legacy-password",
        "legacy-token",
        "legacy-community",
        "legacy-private-key",
        "legacy-camel-private-key",
        "legacy-custom-auth",
        "legacy-renamed-secret",
    ):
        assert plaintext not in serialized
    assert views._redact_action_sources(migrated_sources)[0]["action_input"] == {
        key: "********" if key in expected_secret_keys else value
        for key, value in migrated_input.items()
    }


def test_refresh_rejects_executor_removed_from_tenant(monkeypatch):
    from services.network_topology import refresh

    topology = _refresh_topology_row(source_hash="last-good-hash")
    state = _install_refresh_fakes(monkeypatch, refresh, topology)
    collected = []

    class MissingMembershipQuery:
        async def exists(self):
            return False

    class MissingTenantMembership:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs["member__id"] == "account-1"
            return MissingMembershipQuery()

    async def collect(*_args):
        collected.append(True)
        raise AssertionError("removed tenant member must not execute actions")

    monkeypatch.setattr(refresh, "Tenant", MissingTenantMembership)
    monkeypatch.setattr(refresh, "_collect_source_config", collect)

    result = asyncio.run(
        refresh.refresh_topology(
            topology.id,
            topology.revision,
            refresh_started_at=topology.refresh_started_at,
            tenant_id="tenant-1",
        )
    )

    assert collected == []
    assert result["status"] == "failed"
    assert result["error"] == "拓扑刷新执行账号已不属于当前租户"
    assert state["updates"][0]["values"]["refresh_status"] == "failed"


def test_multi_resource_source_uses_an_independent_run_for_each_device(monkeypatch):
    from services.network_topology import refresh

    produced = []
    run_ids = iter(["run-device-1", "run-device-2"])

    async def produce(_topic, message):
        produced.append(message)

    async def wait_for_action_run(run_id, resolved_source, _tenant_id):
        resource_id = resolved_source["definition"]["resource_ids"][0]
        return [
            SimpleNamespace(
                id=f"log-{resource_id}",
                resource_name=resource_id,
                result=f"hostname {resource_id}",
            )
        ]

    monkeypatch.setattr(refresh.uuid, "uuid4", lambda: next(run_ids))
    monkeypatch.setattr(refresh, "aproduce", produce)
    monkeypatch.setattr(refresh, "_wait_for_action_run", wait_for_action_run)
    resolved_source = {
        "index": 0,
        "definition": {
            "action_id": "action-1",
            "resource_ids": ["device-1", "device-2"],
            "action_input": {},
        },
        "action": SimpleNamespace(id="action-1", action={"func": "get_config"}),
        "action_func": "get_config",
        "action_name": "获取配置",
        "tool": SimpleNamespace(
            name="network-device-app",
            description="网络设备应用",
            version="1.0.0",
            main="main.py",
        ),
        "resources": [
            SimpleNamespace(id="device-1", name="设备一"),
            SimpleNamespace(id="device-2", name="设备二"),
        ],
    }

    result = asyncio.run(
        refresh._execute_one_source(
            resolved_source,
            "tenant-1",
            "account-1",
            asyncio.Semaphore(1),
        )
    )

    assert [message["run_id"] for message in produced] == [
        "run-device-1",
        "run-device-2",
    ]
    assert [message["run_args"]["resource"] for message in produced] == [
        "device-1",
        "device-2",
    ]
    assert result["run_ids"] == ["run-device-1", "run-device-2"]
    assert [item["run_id"] for item in result["resources"]] == result["run_ids"]


def test_refresh_lease_heartbeat_renews_only_its_claim_token(monkeypatch):
    from services.network_topology import refresh

    calls = []
    update_results = iter([1, 0])

    class LeaseQuery:
        def __init__(self, filters):
            self.filters = dict(filters)

        def filter(self, **kwargs):
            self.filters.update(kwargs)
            return self

        async def update(self, **values):
            calls.append((dict(self.filters), values))
            return next(update_results)

    class LeaseModel:
        @classmethod
        def filter(cls, **kwargs):
            return LeaseQuery(kwargs)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(refresh, "NetworkTopology", LeaseModel)
    monkeypatch.setattr(refresh.asyncio, "sleep", no_delay)

    asyncio.run(
        refresh._heartbeat_refresh_lease(
            "topology-1",
            "tenant-1",
            7,
            "lease-token-1",
        )
    )

    assert len(calls) == 2
    for filters, values in calls:
        assert filters["tenant_id"] == "tenant-1"
        assert filters["revision"] == 7
        assert filters["refresh_token"] == "lease-token-1"
        assert set(values) == {"refresh_started_at"}


def test_refresh_lease_loss_immediately_cancels_its_owner(monkeypatch):
    from services.network_topology import refresh

    class LeaseQuery:
        def filter(self, **_kwargs):
            return self

        async def update(self, **_values):
            return 0

    class LeaseModel:
        @classmethod
        def filter(cls, **_kwargs):
            return LeaseQuery()

    async def no_delay(_seconds):
        return None

    async def scenario():
        owner_started = asyncio.Event()

        async def owner():
            owner_started.set()
            await asyncio.Future()

        owner_task = asyncio.create_task(owner())
        await owner_started.wait()
        lease_lost_event = asyncio.Event()
        result = await refresh._heartbeat_refresh_lease(
            "topology-1",
            "tenant-1",
            7,
            "lease-token-1",
            owner_task=owner_task,
            lease_lost_event=lease_lost_event,
        )

        assert result is False
        assert lease_lost_event.is_set()
        with pytest.raises(asyncio.CancelledError):
            await owner_task

    monkeypatch.setattr(refresh, "NetworkTopology", LeaseModel)
    monkeypatch.setattr(refresh.asyncio, "sleep", no_delay)

    asyncio.run(scenario())


def test_refresh_returns_superseded_without_analysis_when_heartbeat_loses_lease(
    monkeypatch,
):
    from services.network_topology import refresh

    topology = _refresh_topology_row(source_hash="last-good-hash")
    topology.refresh_token = "lease-token-1"
    state = _install_refresh_fakes(monkeypatch, refresh, topology)
    calls = {"collect": 0, "analyze": 0}

    async def lose_lease(
        _topology_id,
        _tenant_id,
        _expected_revision,
        _refresh_token,
        *,
        owner_task,
        lease_lost_event,
    ):
        lease_lost_event.set()
        owner_task.cancel()
        return False

    async def collect(*_args):
        calls["collect"] += 1
        await asyncio.Future()

    async def analyze(*_args, **_kwargs):
        calls["analyze"] += 1
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(refresh, "_heartbeat_refresh_lease", lose_lease)
    monkeypatch.setattr(refresh, "_collect_source_config", collect)
    monkeypatch.setattr(refresh, "analyze_topology", analyze)

    result = asyncio.run(
        refresh.refresh_topology(
            topology.id,
            topology.revision,
            refresh_started_at=topology.refresh_started_at,
            tenant_id="tenant-1",
            refresh_token="lease-token-1",
        )
    )

    assert result == {
        "id": "topology-1",
        "status": "superseded",
        "changed": False,
        "last_run_ids": [],
    }
    assert calls["analyze"] == 0
    assert state["updates"] == []


def _topology_source_aggregate(
    result,
    *,
    action_id="action-1",
    resource_id="resource-1",
):
    return {
        "schema_version": 1,
        "sources": [
            {
                "action_id": action_id,
                "resources": [
                    {
                        "resource_id": resource_id,
                        "result": result,
                    }
                ],
            }
        ],
    }


def test_topology_analysis_uses_stable_device_ids_and_explicit_connection_evidence():
    import orjson

    from services.network_topology.analyzer import _parse_topology

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    node_evidence = "hostname core-switch and edge-switch management 10.0.0.2"
    edge_evidence = "show lldp neighbors: Gi0/1 -> edge"
    raw_topology = {
        "nodes": [
            {
                "id": "model-core-a",
                "serial": "SERIAL-CORE",
                "label": "核心交换机",
                "type": "switch",
                "policies": ["允许管理网段"],
                "source_refs": [source_ref],
                "evidence": [node_evidence],
            },
            {
                "id": "model-core-b",
                "serial": "SERIAL-CORE",
                "label": "核心交换机备用名称",
                "type": "switch",
                "policies": ["限制访客 VLAN"],
                "source_refs": [source_ref],
                "evidence": [node_evidence],
            },
            {
                "id": "model-edge",
                "management_ip": "10.0.0.2/24",
                "label": "接入交换机",
                "type": "switch",
                "source_refs": [source_ref],
                "evidence": [node_evidence],
            },
        ],
        "edges": [
            {
                "source": "model-core-a",
                "target": "model-edge",
                "port": "Gi0/1",
            },
            {
                "source": "model-core-b",
                "target": "model-edge",
                "port": "Gi0/1",
                "evidence_type": "lldp",
                "evidence": [edge_evidence],
                "source_refs": [source_ref],
            },
            {
                "source": "model-edge",
                "target": "model-core-a",
                "port": "Gi0/1",
                "evidence_type": "lldp",
                "evidence": [edge_evidence],
                "source_refs": [source_ref],
            },
            {
                "source": "model-core-a",
                "target": "missing-device",
                "port": "Gi0/2",
                "evidence_type": "cdp",
                "evidence": ["dangling neighbor reference"],
                "source_refs": [source_ref],
            },
        ],
    }

    topology = _parse_topology(
        orjson.dumps(raw_topology).decode(),
        source_aggregate=_topology_source_aggregate(
            f"{node_evidence}\n{edge_evidence}\ndangling neighbor reference"
        ),
    )

    assert [node["id"] for node in topology["nodes"]] == [
        "10.0.0.2",
        "SERIAL-CORE",
    ]
    core = next(node for node in topology["nodes"] if node["id"] == "SERIAL-CORE")
    assert core["policies"] == ["允许管理网段", "限制访客 VLAN"]
    assert len(topology["edges"]) == 1
    edge = topology["edges"][0]
    assert {edge["source"], edge["target"]} == {"SERIAL-CORE", "10.0.0.2"}
    assert edge["evidence_type"] == "lldp"
    assert edge["evidence"] == [edge_evidence]


def test_topology_analysis_whitelists_and_bounds_untrusted_model_output(monkeypatch):
    import orjson

    from services.network_topology import analyzer

    policies = [
        f"policy-{index:03d}-" + ("p" * analyzer.MAX_POLICY_LENGTH)
        for index in range(analyzer.MAX_POLICY_ITEMS + 5)
    ]
    evidence = [
        f"lldp evidence-{index:03d} " + ("e" * analyzer.MAX_EVIDENCE_LENGTH)
        for index in range(analyzer.MAX_EVIDENCE_ITEMS + 5)
    ]
    source_refs = [
        {
            "action_id": f"action-{index:03d}",
            "resource_id": f"resource-{index:03d}",
            "raw_config": "SOURCE_REF_SECRET_MUST_NOT_SURVIVE",
        }
        for index in range(analyzer.MAX_SOURCE_REF_ITEMS + 5)
    ]
    source_refs.extend(
        [
            "invalid-ref",
            {"raw_config": "invalid"},
            {
                "action_id": "a" * (analyzer.MAX_SOURCE_REF_VALUE_LENGTH + 1),
                "resource_id": "resource-too-long",
            },
        ]
    )
    raw_topology = {
        "raw_config": "TOP_LEVEL_SECRET_MUST_NOT_SURVIVE",
        "debug": {"credentials": "TOP_LEVEL_CREDENTIAL_MUST_NOT_SURVIVE"},
        "nodes": [
            {
                "id": "model-core",
                "serial": "SERIAL-CORE",
                "label": "L" * (analyzer.MAX_TOPOLOGY_FIELD_LENGTH + 50),
                "type": "unsupported-secret-device-type",
                "policies": policies + [{"raw_config": "invalid-policy"}, 123],
                "evidence": evidence + [{"raw_config": "invalid-evidence"}],
                "source_refs": source_refs,
                "raw_config": "NODE_SECRET_MUST_NOT_SURVIVE",
                "credentials": {"password": "NODE_PASSWORD_MUST_NOT_SURVIVE"},
            },
            {
                "id": "model-edge",
                "management_ip": "10.0.0.2/24",
                "label": "接入交换机",
                "type": "switch",
                "source_refs": [source_refs[0]],
                "evidence": [evidence[0]],
            },
        ],
        "edges": [
            {
                "source": "model-core",
                "target": "model-edge",
                "vlan": "v" * (analyzer.MAX_TOPOLOGY_FIELD_LENGTH + 50),
                "port": "Gi0/1",
                "bandwidth": "b" * (analyzer.MAX_TOPOLOGY_FIELD_LENGTH + 50),
                "evidence_type": "lldp",
                "evidence": evidence,
                "source_refs": source_refs,
                "raw_config": "EDGE_SECRET_MUST_NOT_SURVIVE",
                "packet_dump": "EDGE_PACKET_MUST_NOT_SURVIVE",
            },
        ],
    }

    source_aggregate = {
        "schema_version": 1,
        "sources": [
            {
                "action_id": source_ref["action_id"],
                "resources": [
                    {
                        "resource_id": source_ref["resource_id"],
                        "result": evidence[index][: analyzer.MAX_EVIDENCE_LENGTH],
                    }
                ],
            }
            for index, source_ref in enumerate(
                source_refs[: analyzer.MAX_SOURCE_REF_ITEMS]
            )
        ],
    }
    topology = analyzer._parse_topology(
        orjson.dumps(raw_topology).decode(),
        source_aggregate=source_aggregate,
    )

    assert set(topology) == {"nodes", "edges"}
    assert len(topology["nodes"]) == 2
    assert len(topology["edges"]) == 1
    node = next(item for item in topology["nodes"] if item["id"] == "SERIAL-CORE")
    assert set(node) == {
        "id",
        "serial",
        "chassis_id",
        "management_ip",
        "resource_id",
        "type",
        "label",
        "ip",
        "policies",
        "source_refs",
        "evidence",
    }
    assert node["type"] == "unknown"
    assert len(node["label"]) == analyzer.MAX_TOPOLOGY_FIELD_LENGTH
    assert len(node["policies"]) == analyzer.MAX_POLICY_ITEMS
    assert all(
        isinstance(item, str) and len(item) <= analyzer.MAX_POLICY_LENGTH
        for item in node["policies"]
    )
    assert len(node["evidence"]) == analyzer.MAX_EVIDENCE_ITEMS
    assert all(
        isinstance(item, str) and len(item) <= analyzer.MAX_EVIDENCE_LENGTH
        for item in node["evidence"]
    )
    assert len(node["source_refs"]) == analyzer.MAX_SOURCE_REF_ITEMS
    assert all(
        set(item) == {"action_id", "resource_id"}
        and len(item["action_id"]) <= analyzer.MAX_SOURCE_REF_VALUE_LENGTH
        and len(item["resource_id"]) <= analyzer.MAX_SOURCE_REF_VALUE_LENGTH
        for item in node["source_refs"]
    )

    edge = topology["edges"][0]
    assert set(edge) == {
        "id",
        "source",
        "target",
        "vlan",
        "port",
        "bandwidth",
        "evidence_type",
        "source_refs",
        "evidence",
    }
    assert len(edge["vlan"]) == analyzer.MAX_TOPOLOGY_FIELD_LENGTH
    assert len(edge["bandwidth"]) == analyzer.MAX_TOPOLOGY_FIELD_LENGTH
    assert len(edge["evidence"]) == analyzer.MAX_EVIDENCE_ITEMS
    assert len(edge["source_refs"]) == analyzer.MAX_SOURCE_REF_ITEMS

    serialized = repr(topology)
    for secret in (
        "TOP_LEVEL_SECRET_MUST_NOT_SURVIVE",
        "TOP_LEVEL_CREDENTIAL_MUST_NOT_SURVIVE",
        "SOURCE_REF_SECRET_MUST_NOT_SURVIVE",
        "NODE_SECRET_MUST_NOT_SURVIVE",
        "NODE_PASSWORD_MUST_NOT_SURVIVE",
        "EDGE_SECRET_MUST_NOT_SURVIVE",
        "EDGE_PACKET_MUST_NOT_SURVIVE",
    ):
        assert secret not in serialized


def test_topology_analysis_rejects_incomplete_graphs_over_core_count_limits(
    monkeypatch,
):
    import orjson

    from services.network_topology import analyzer

    monkeypatch.setattr(analyzer, "MAX_TOPOLOGY_NODES", 2)
    with pytest.raises(ValueError):
        analyzer._parse_topology(
            orjson.dumps(
                {
                    "nodes": [
                        {"id": "node-1"},
                        {"id": "node-2"},
                        {"id": "node-3"},
                    ],
                    "edges": [],
                }
            ).decode()
        )

    monkeypatch.setattr(analyzer, "MAX_TOPOLOGY_NODES", 3)
    monkeypatch.setattr(analyzer, "MAX_TOPOLOGY_EDGES", 1)
    with pytest.raises(ValueError):
        analyzer._parse_topology(
            orjson.dumps(
                {
                    "nodes": [{"id": "node-1"}, {"id": "node-2"}],
                    "edges": [
                        {
                            "source": "node-1",
                            "target": "node-2",
                            "port": "Gi0/1",
                            "evidence_type": "lldp",
                        },
                        {
                            "source": "node-1",
                            "target": "node-2",
                            "port": "Gi0/2",
                            "evidence_type": "cdp",
                        },
                    ],
                }
            ).decode()
        )


def test_topology_analysis_rejects_model_and_normalized_utf8_byte_overflow(
    monkeypatch,
):
    import orjson

    from services.network_topology import analyzer

    raw_topology = orjson.dumps(
        {"nodes": [], "edges": [], "ignored_multibyte": "网络拓扑"}
    ).decode()
    monkeypatch.setattr(
        analyzer,
        "MAX_MODEL_RESPONSE_BYTES",
        len(raw_topology.encode("utf-8")) - 1,
    )
    with pytest.raises(ValueError, match="模型原始响应超过"):
        analyzer._parse_topology(raw_topology)

    monkeypatch.setattr(analyzer, "MAX_MODEL_RESPONSE_BYTES", 1024)
    monkeypatch.setattr(analyzer, "MAX_TOPOLOGY_JSON_BYTES", 8)
    with pytest.raises(ValueError, match="标准化拓扑数据超过"):
        analyzer._parse_topology('{"nodes":[],"edges":[]}')


def test_topology_analyzer_stops_reading_stream_at_utf8_byte_limit(monkeypatch):
    from services.network_topology import analyzer

    consumed = []

    async def stream_prompt_events(**_kwargs):
        consumed.append("first")
        yield "delta", {"content": "界"}
        consumed.append("overflow")
        yield "delta", {"content": "界"}
        consumed.append("must-not-be-read")
        yield "delta", {"content": "{}"}

    monkeypatch.setattr(analyzer, "stream_prompt_events", stream_prompt_events)
    monkeypatch.setattr(analyzer, "MAX_MODEL_RESPONSE_BYTES", 5)

    with pytest.raises(ValueError, match="模型原始响应超过"):
        asyncio.run(
            analyzer._execute_bounded_prompt(
                "{}",
                tenant_id="tenant-1",
                session_id="topology-session",
            )
        )

    assert consumed == ["first", "overflow"]


def test_topology_analyzer_redacts_collected_credentials_before_model_call(
    monkeypatch,
):
    import orjson

    from services.network_topology import analyzer

    evidence = "show lldp neighbors: Gi0/1 connects core to edge"
    aggregate = _topology_source_aggregate(
        {
            "password": "STRUCTURED_SECRET",
            "token": "STRUCTURED_TOKEN",
            "running_config": (
                "username admin secret 5 HASHED_DEVICE_SECRET\n"
                "snmp-server community COMMUNITY_VALUE RO\n"
                f"{evidence}"
            ),
        }
    )
    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    model_topology = {
        "nodes": [
            {
                "id": "core",
                "serial": "CORE-001",
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
                "port": "Gi0/1",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": [evidence],
            }
        ],
    }
    captured = {}

    async def execute(config_text, *, tenant_id, session_id):
        captured.update(
            {
                "config_text": config_text,
                "tenant_id": tenant_id,
                "session_id": session_id,
            }
        )
        return orjson.dumps(model_topology).decode("utf-8")

    monkeypatch.setattr(analyzer, "_execute_bounded_prompt", execute)

    topology = asyncio.run(
        analyzer.analyze_topology(
            orjson.dumps(aggregate).decode("utf-8"),
            tenant_id="tenant-1",
            session_id="topology-session",
        )
    )

    assert captured["tenant_id"] == "tenant-1"
    assert captured["session_id"] == "topology-session"
    assert evidence in captured["config_text"]
    assert "[REDACTED]" in captured["config_text"]
    for secret in (
        "STRUCTURED_SECRET",
        "STRUCTURED_TOKEN",
        "HASHED_DEVICE_SECRET",
        "COMMUNITY_VALUE",
    ):
        assert secret not in captured["config_text"]
    assert len(topology["edges"]) == 1
    assert topology["edges"][0]["evidence"] == [evidence]


def test_topology_edges_require_bound_refs_and_locatable_evidence():
    import orjson

    from services.network_topology import analyzer

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    valid_evidence = "show lldp neighbors: Gi0/1 connects core to edge"
    common_edge = {
        "source": "core",
        "target": "edge",
        "evidence_type": "lldp",
    }
    raw_topology = {
        "nodes": [
            {
                "id": "core",
                "serial": "CORE-001",
                "source_refs": [source_ref],
                "evidence": [valid_evidence],
            },
            {
                "id": "edge",
                "serial": "EDGE-001",
                "source_refs": [source_ref],
                "evidence": [valid_evidence],
            },
        ],
        "edges": [
            common_edge,
            {**common_edge, "evidence": [valid_evidence]},
            {
                **common_edge,
                "port": "Gi0/2",
                "evidence": [valid_evidence],
                "source_refs": [
                    {"action_id": "unknown-action", "resource_id": "resource-1"}
                ],
            },
            {
                **common_edge,
                "port": "Gi0/3",
                "evidence": ["hallucinated lldp neighbor"],
                "source_refs": [source_ref],
            },
            {
                **common_edge,
                "port": "Gi0/4",
                "evidence": [valid_evidence],
                "source_refs": [source_ref],
            },
        ],
    }

    topology = analyzer._parse_topology(
        orjson.dumps(raw_topology).decode(),
        source_aggregate=_topology_source_aggregate(valid_evidence),
    )

    assert len(topology["edges"]) == 1
    assert topology["edges"][0]["port"] == "Gi0/4"
    assert topology["edges"][0]["source_refs"] == [source_ref]
    assert topology["edges"][0]["evidence"] == [valid_evidence]


def test_topology_nodes_require_bound_refs_and_locatable_evidence():
    import orjson

    from services.network_topology import analyzer

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    hostname_evidence = "hostname real-switch"
    serial_evidence = "serial REAL-001"
    source_result = "\n".join(
        [hostname_evidence, serial_evidence, "operational mode o"]
    )
    raw_topology = {
        "nodes": [
            {
                "id": "real",
                "serial": "REAL-001",
                "source_refs": [source_ref],
                "evidence": [hostname_evidence, serial_evidence],
            },
            {
                "id": "phantom",
                "serial": "PHANTOM-999",
                "source_refs": [source_ref],
                "evidence": ["o"],
            },
        ],
        "edges": [
            {
                "source": "real",
                "target": "phantom",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": [hostname_evidence],
            }
        ],
    }

    topology = analyzer._parse_topology(
        orjson.dumps(raw_topology).decode(),
        source_aggregate=_topology_source_aggregate(source_result),
    )

    assert [node["id"] for node in topology["nodes"]] == ["REAL-001"]
    assert topology["nodes"][0]["source_refs"] == [source_ref]
    assert topology["nodes"][0]["evidence"] == [
        hostname_evidence,
        serial_evidence,
    ]
    assert topology["edges"] == []


def test_topology_rejects_short_generic_evidence_and_keeps_concrete_network_lines():
    import orjson

    from services.network_topology import analyzer

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    lldp_evidence = "show lldp neighbors: Gi0/10 -> edge-switch-01"
    cdp_evidence = "Device ID: edge-switch-01, Local Intrfce: Gi0/20, Port ID: Gi0/1"
    interface_evidence = "interface GigabitEthernet0/30 connected to edge-switch-01"
    source_result = "\n".join(
        [
            "some common output",
            "lldp neighbor",
            "hostname core-switch",
            "interface Gi0/100 connected to edge-switch-01",
            lldp_evidence,
            cdp_evidence,
            interface_evidence,
        ]
    )
    raw_topology = {
        "nodes": [
            {
                "id": "core",
                "serial": "CORE-001",
                "source_refs": [source_ref],
                "evidence": [lldp_evidence],
            },
            {
                "id": "edge",
                "serial": "EDGE-001",
                "source_refs": [source_ref],
                "evidence": [lldp_evidence],
            },
        ],
        "edges": [
            {
                "source": "core",
                "target": "edge",
                "port": "invalid-single-character",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": ["o"],
            },
            {
                "source": "core",
                "target": "edge",
                "port": "invalid-generic",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": ["lldp neighbor"],
            },
            {
                "source": "core",
                "target": "edge",
                "port": "invalid-unrelated",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": ["hostname core-switch"],
            },
            {
                "source": "core",
                "target": "edge",
                "port": "invalid-token-prefix",
                "evidence_type": "interface",
                "source_refs": [source_ref],
                "evidence": ["interface Gi0/10"],
            },
            {
                "source": "core",
                "target": "edge",
                "port": "Gi0/10",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": [lldp_evidence],
            },
            {
                "source": "core",
                "target": "edge",
                "port": "Gi0/20",
                "evidence_type": "cdp",
                "source_refs": [source_ref],
                "evidence": [cdp_evidence],
            },
            {
                "source": "core",
                "target": "edge",
                "port": "Gi0/30",
                "evidence_type": "interface",
                "source_refs": [source_ref],
                "evidence": [interface_evidence],
            },
        ],
    }

    topology = analyzer._parse_topology(
        orjson.dumps(raw_topology).decode(),
        source_aggregate=_topology_source_aggregate(source_result),
    )

    assert {edge["port"] for edge in topology["edges"]} == {
        "Gi0/10",
        "Gi0/20",
        "Gi0/30",
    }
    assert {edge["evidence"][0] for edge in topology["edges"]} == {
        lldp_evidence,
        cdp_evidence,
        interface_evidence,
    }


def test_topology_policies_and_evidence_redact_network_credentials():
    import orjson

    from services.network_topology import analyzer

    source_ref = {"action_id": "action-1", "resource_id": "resource-1"}
    lldp_evidence = "lldp neighbor Gi0/1 password edge-password"
    private_key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "private-key-material\n"
        "-----END RSA PRIVATE KEY-----"
    )
    source_result = "\n".join(
        [
            lldp_evidence,
            "password admin-password",
            "snmp-server community public-community RO",
            'token="api-token-value"',
            "authorization: Bearer bearer-token-value",
            'set encrypted-password "$6$junos-password-hash"',
            "set psksecret ENC fortigate-psk",
            private_key,
        ]
    )
    raw_topology = {
        "nodes": [
            {
                "id": "core",
                "serial": "CORE-001",
                "policies": [
                    "password admin-password",
                    "snmp-server community public-community RO",
                    'token="api-token-value"',
                    'set encrypted-password "$6$junos-password-hash"',
                    "set psksecret ENC fortigate-psk",
                    private_key,
                ],
                "evidence": [
                    "authorization: Bearer bearer-token-value",
                    private_key,
                ],
                "source_refs": [source_ref],
            },
            {
                "id": "edge",
                "serial": "EDGE-001",
                "source_refs": [source_ref],
                "evidence": [lldp_evidence],
            },
        ],
        "edges": [
            {
                "source": "core",
                "target": "edge",
                "evidence_type": "lldp",
                "source_refs": [source_ref],
                "evidence": [lldp_evidence],
            }
        ],
    }

    topology = analyzer._parse_topology(
        orjson.dumps(raw_topology).decode(),
        source_aggregate=_topology_source_aggregate(source_result),
    )
    serialized = orjson.dumps(topology).decode()

    for secret in (
        "admin-password",
        "public-community",
        "api-token-value",
        "bearer-token-value",
        "junos-password-hash",
        "fortigate-psk",
        "private-key-material",
        "edge-password",
    ):
        assert secret not in serialized
    assert "[REDACTED]" in serialized
    assert "[REDACTED PRIVATE KEY]" in serialized


def test_network_topology_listener_is_registered_with_a_stable_task_name():
    from core.listeners import LISTENER_TUPLE, NetworkTopologyRefreshListener
    from core.listeners import network_topology as listener_module

    assert NetworkTopologyRefreshListener in LISTENER_TUPLE
    assert listener_module._TASK_NAME == "network_topology_refresh_sweeper"

    class FakeApp:
        def __init__(self):
            self.added = []
            self.cancelled = []

        def add_task(self, coroutine, *, name):
            self.added.append(name)
            coroutine.close()

        async def cancel_task(self, name):
            self.cancelled.append(name)

    app = FakeApp()
    listener = NetworkTopologyRefreshListener()
    asyncio.run(listener.after_server_start(app, None))
    asyncio.run(listener.before_server_stop(app, None))

    assert app.added == ["network_topology_refresh_sweeper"]
    assert app.cancelled == ["network_topology_refresh_sweeper"]


def test_network_topology_migration_adds_automation_fields_and_crud_permissions():
    migration = importlib.import_module(
        "migrations.models.81_20260719230000_automate_network_topology"
    )
    upgrade_sql = asyncio.run(migration.upgrade(None))
    init_sql = (ROOT.parent / "aioe_package/aioe_install/init.sql").read_text()

    for definition in (
        "\"action_sources\" JSONB NOT NULL DEFAULT '[]'::jsonb",
        '"refresh_interval" INT NOT NULL DEFAULT 3600',
        '"enabled" BOOL NOT NULL DEFAULT TRUE',
        '"executor_id" VARCHAR(36)',
        '"source_hash" VARCHAR(64)',
        '"revision" INT NOT NULL DEFAULT 1',
        "\"last_run_ids\" JSONB NOT NULL DEFAULT '[]'::jsonb",
        "\"refresh_status\" VARCHAR(32) NOT NULL DEFAULT 'pending'",
        '"last_refresh_at" TIMESTAMPTZ',
        '"next_refresh_at" TIMESTAMPTZ',
        '"refresh_started_at" TIMESTAMPTZ',
        '"refresh_token" VARCHAR(36)',
        '"refresh_error" TEXT',
    ):
        assert definition in upgrade_sql

    assert "\"refresh_status\" = 'legacy'" in upgrade_sql
    assert '"enabled" = FALSE' in upgrade_sql
    assert "\"source_config\" = ''" not in upgrade_sql

    crud_permissions = (
        '["network_topology:c","network_topology:r",'
        '"network_topology:u","network_topology:d"]'
    )
    for text in (upgrade_sql, init_sql):
        assert crud_permissions in text
        assert "WHERE permission_value <> 'network_topology:w'" in text
        assert "network_topology:c" in text
        assert "network_topology:u" in text
        assert "network_topology:d" in text


def test_legacy_manual_parse_and_save_endpoints_are_explicitly_disabled():
    source = (ROOT / "services/network_topology/views.py").read_text()
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"parse_topology", "save_topology"}
    }

    assert set(functions) == {"parse_topology", "save_topology"}
    for function_name, disabled_message in (
        ("parse_topology", "手工解析拓扑已停用"),
        ("save_topology", "手工保存拓扑已停用"),
    ):
        function = functions[function_name]
        segment = ast.get_source_segment(source, function)
        assert disabled_message in segment
        assert "status=410" in segment
        assert not any(
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"create", "get", "write"}
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"analyze_topology_stream", "ResponseStream"}
                )
            )
            for node in ast.walk(function)
        )
