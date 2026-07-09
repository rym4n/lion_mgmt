# coding=utf-8
import asyncio

from services.supply_chain_review import manager


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


def _install_export_fakes(monkeypatch):
    ci_types = [
        _PackageObject(key="app", name="应用"),
        _PackageObject(key="server", name="服务器"),
        _PackageObject(key="team", name="团队"),
        _PackageObject(key="user", name="人员"),
    ]
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
        ),
        _PackageObject(
            key="belongs_to",
            source_ci_types=["user"],
            target_ci_types=["team"],
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
    assert [item["key"] for item in related_package["relation_types"]] == ["runs_on"]
