# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

from services.supply_chain_review import manager, views


class _PackageObject:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self._json = dict(kwargs)

    @property
    def json(self):
        return self._json


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def order_by(self, *fields):
        rows = self.rows
        for field in reversed(fields):
            reverse = field.startswith("-")
            key = field[1:] if reverse else field
            rows = sorted(rows, key=lambda item: getattr(item, key, ""), reverse=reverse)
        self.rows = rows
        return self

    async def all(self):
        return list(self.rows)


def _q_filters(q):
    filters = dict(getattr(q, "filters", {}) or {})
    for child in getattr(q, "children", ()) or ():
        filters.update(_q_filters(child))
    return filters


def _install_export_fakes(monkeypatch, missing_ci_type_keys=()):
    ci_types = [
        _PackageObject(id="app-id", key="app", name="应用", group_key="custom"),
        _PackageObject(id="server-id", key="server", name="服务器", group_key="custom"),
        _PackageObject(id="team-id", key="team", name="团队", group_key="custom"),
        _PackageObject(id="user-id", key="user", name="人员", group_key="custom"),
    ]
    ci_types = [item for item in ci_types if item.key not in missing_ci_type_keys]
    fields = [
        _PackageObject(ci_type_key="app", field_key="name", sort=1),
        _PackageObject(ci_type_key="server", field_key="hostname", sort=1),
        _PackageObject(ci_type_key="team", field_key="name", sort=1),
        _PackageObject(ci_type_key="user", field_key="name", sort=1),
    ]
    relation_types = [
        _PackageObject(
            key="runs_on",
            source_ci_types=["app"],
            target_ci_types=["server"],
            relation_fields=[],
        ),
        _PackageObject(
            key="runs_on_with_owner",
            source_ci_types=["app"],
            target_ci_types=["server"],
            relation_fields=[{"field_key": "owner", "ref_ci_type": "team"}],
        ),
        _PackageObject(
            key="belongs_to",
            source_ci_types=["user"],
            target_ci_types=["team"],
            relation_fields=[],
        ),
    ]

    def ci_type_filter(*args, **kwargs):
        key_filter = None
        for arg in args:
            key_filter = _q_filters(arg).get("key__in", key_filter)
        rows = ci_types if not key_filter else [item for item in ci_types if item.key in key_filter]
        return _FakeQuery(rows)

    def field_filter(*args, **kwargs):
        key_filter = kwargs.get("ci_type_key__in")
        rows = fields if key_filter is None else [item for item in fields if item.ci_type_key in key_filter]
        return _FakeQuery(rows)

    monkeypatch.setattr(manager.SupplyChainCiType, "filter", staticmethod(ci_type_filter))
    monkeypatch.setattr(manager.SupplyChainCiFieldDef, "filter", staticmethod(field_filter))
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda *args, **kwargs: _FakeQuery(relation_types)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiModelGroup,
        "filter",
        staticmethod(
            lambda *args, **kwargs: _FakeQuery(
                [_PackageObject(key="custom", name="自定义模型", sort=1)]
            )
        ),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiUniqueRule,
        "filter",
        staticmethod(lambda *args, **kwargs: _FakeQuery([])),
    )


def test_export_model_package_respects_selected_model_relation_scope(monkeypatch):
    _install_export_fakes(monkeypatch)

    package = asyncio.run(
        manager.export_model_package(
            "tenant-a",
            ci_type_keys=["server"],
            include_related=False,
        )
    )

    assert [item["key"] for item in package["ci_types"]] == ["server"]
    assert [item["ci_type_key"] for item in package["fields"]] == ["server"]
    assert package["relation_types"] == []

    related_package = asyncio.run(
        manager.export_model_package(
            "tenant-a",
            ci_type_keys=["server"],
            include_related=True,
        )
    )

    assert [item["key"] for item in related_package["ci_types"]] == ["app", "server"]
    assert [item["ci_type_key"] for item in related_package["fields"]] == ["app", "server"]
    assert [item["key"] for item in related_package["relation_types"]] == [
        "runs_on",
        "runs_on_with_owner",
    ]

    relation_package = asyncio.run(
        manager.export_model_package(
            "tenant-a",
            ci_type_keys=["app", "server"],
            include_relations=True,
        )
    )

    assert [item["key"] for item in relation_package["ci_types"]] == ["app", "server"]
    assert [item["ci_type_key"] for item in relation_package["fields"]] == ["app", "server"]
    assert [item["key"] for item in relation_package["relation_types"]] == ["runs_on"]

    partial_relation_package = asyncio.run(
        manager.export_model_package(
            "tenant-a",
            ci_type_keys=["server"],
            include_relations=True,
        )
    )

    assert [item["key"] for item in partial_relation_package["ci_types"]] == ["server"]
    assert partial_relation_package["relation_types"] == []


def test_export_model_package_filters_relations_against_existing_models(monkeypatch):
    _install_export_fakes(monkeypatch, missing_ci_type_keys={"server"})

    package = asyncio.run(
        manager.export_model_package(
            "tenant-a",
            ci_type_keys=["app", "server"],
            include_relations=True,
        )
    )

    assert [item["key"] for item in package["ci_types"]] == ["app"]
    assert [item["ci_type_key"] for item in package["fields"]] == ["app"]
    assert package["relation_types"] == []


def test_export_model_package_endpoint_forwards_relation_mode(monkeypatch):
    export_calls = []

    async def export_package(
        tenant,
        ci_type_keys=None,
        include_related=False,
        include_relations=False,
    ):
        export_calls.append(
            (tenant, ci_type_keys, include_related, include_relations)
        )
        return {"ci_types": [], "fields": [], "relation_types": []}

    monkeypatch.setattr(views, "export_model_package", export_package)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "ci_type_keys": ["app", "server"],
            "include_relations": True,
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.ci_type_export)(request))

    assert payload == {"ci_types": [], "fields": [], "relation_types": []}
    assert export_calls == [
        ("tenant-a", ["app", "server"], False, True),
    ]
