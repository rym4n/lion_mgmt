# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from libs.error_code.exception import CustomError
from services.asset_management import manager, views


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
            rows = sorted(
                rows, key=lambda item: getattr(item, key, ""), reverse=reverse
            )
        self.rows = rows
        return self

    async def all(self):
        return list(self.rows)


def _q_filters(q):
    filters = dict(getattr(q, "filters", {}) or {})
    for child in getattr(q, "children", ()) or ():
        filters.update(_q_filters(child))
    return filters


def _install_export_fakes(monkeypatch, missing_asset_model_keys=()):
    asset_models = [
        _PackageObject(
            id="app-id",
            key="app",
            name="应用",
            group_key="custom",
            is_locked=True,
            is_builtin=True,
            builtin_version="aioe-asset-internal-version",
            owner="product",
        ),
        _PackageObject(id="server-id", key="server", name="服务器", group_key="custom"),
        _PackageObject(id="team-id", key="team", name="团队", group_key="custom"),
        _PackageObject(id="user-id", key="user", name="人员", group_key="custom"),
    ]
    asset_models = [
        item for item in asset_models if item.key not in missing_asset_model_keys
    ]
    fields = [
        _PackageObject(
            asset_model_id="app-id",
            asset_model_key="app",
            field_key="name",
            sort=1,
            is_locked=True,
            field_group="基础信息",
            field_group_sort=1,
        ),
        _PackageObject(
            asset_model_id="app-id",
            asset_model_key="app",
            field_key="server",
            data_type="ref",
            referenced_asset_model_id="server-id",
            referenced_asset_model_key="server",
            options={"multiple": False, "reverse_unique": False},
            sort=2,
        ),
        _PackageObject(
            asset_model_id="app-id",
            asset_model_key="app",
            field_key="owner",
            data_type="ref",
            referenced_asset_model_id="team-id",
            referenced_asset_model_key="team",
            options={"multiple": False, "reverse_unique": False},
            sort=3,
        ),
        _PackageObject(
            asset_model_id="server-id",
            asset_model_key="server",
            field_key="hostname",
            data_type="text",
            sort=1,
        ),
        _PackageObject(
            asset_model_id="team-id",
            asset_model_key="team",
            field_key="name",
            data_type="text",
            sort=1,
        ),
        _PackageObject(
            asset_model_id="user-id",
            asset_model_key="user",
            field_key="name",
            data_type="text",
            sort=1,
        ),
        _PackageObject(
            asset_model_id="user-id",
            asset_model_key="user",
            field_key="team",
            data_type="ref",
            referenced_asset_model_id="team-id",
            referenced_asset_model_key="team",
            options={"multiple": False, "reverse_unique": False},
            sort=2,
        ),
    ]

    def asset_model_filter(*args, **kwargs):
        key_filter = None
        for arg in args:
            key_filter = _q_filters(arg).get("key__in", key_filter)
        rows = (
            asset_models
            if not key_filter
            else [item for item in asset_models if item.key in key_filter]
        )
        return _FakeQuery(rows)

    def field_filter(*args, **kwargs):
        key_filter = kwargs.get("asset_model_key__in")
        data_type = kwargs.get("data_type")
        rows = (
            fields
            if key_filter is None
            else [item for item in fields if item.asset_model_key in key_filter]
        )
        if data_type is not None:
            rows = [
                item for item in rows if getattr(item, "data_type", None) == data_type
            ]
        return _FakeQuery(rows)

    monkeypatch.setattr(manager.AssetModel, "filter", staticmethod(asset_model_filter))
    monkeypatch.setattr(
        manager.AssetFieldDefinition, "filter", staticmethod(field_filter)
    )
    monkeypatch.setattr(
        manager.AssetModelGroup,
        "filter",
        staticmethod(
            lambda *args, **kwargs: _FakeQuery(
                [_PackageObject(key="custom", name="自定义模型", sort=1)]
            )
        ),
    )
    monkeypatch.setattr(
        manager.AssetUniqueRule,
        "filter",
        staticmethod(lambda *args, **kwargs: _FakeQuery([])),
    )


@pytest.mark.parametrize(
    "package",
    [
        {
            "schema_version": 1,
            "asset_model_groups": [],
            "asset_models": [],
            "asset_fields": [],
            "asset_unique_rules": [],
        },
        {
            "schema_version": 2,
            "model_groups": [],
            "models": [],
            "fields": [],
            "unique_rules": [],
        },
        {
            "schema_version": 2,
            "asset_model_groups": [],
            "asset_models": [
                {
                    "key": "server",
                    "group_key": "custom",
                    "legacy_model_id": "source-model-id",
                }
            ],
            "asset_fields": [],
            "asset_unique_rules": [],
        },
    ],
)
def test_model_package_rejects_v1_and_unsupported_fields(package):
    with pytest.raises(CustomError):
        manager.normalize_asset_model_package(package)


def test_export_asset_model_package_respects_selected_model_reference_scope(
    monkeypatch,
):
    _install_export_fakes(monkeypatch)

    package = asyncio.run(
        manager.export_asset_model_package(
            "tenant-a",
            asset_model_keys=["server"],
            include_related=False,
        )
    )

    assert [item["key"] for item in package["asset_models"]] == ["server"]
    assert [item["asset_model_key"] for item in package["asset_fields"]] == ["server"]
    assert set(package) == {
        "schema_version",
        "asset_model_groups",
        "asset_models",
        "asset_fields",
        "asset_unique_rules",
    }

    related_package = asyncio.run(
        manager.export_asset_model_package(
            "tenant-a",
            asset_model_keys=["server"],
            include_related=True,
        )
    )

    assert [item["key"] for item in related_package["asset_models"]] == [
        "app",
        "server",
        "team",
        "user",
    ]
    assert [item["asset_model_key"] for item in related_package["asset_fields"]] == [
        "app",
        "app",
        "app",
        "server",
        "team",
        "user",
        "user",
    ]
    internal_fields = {
        "is_locked",
        "is_builtin",
        "builtin_version",
        "owner",
        "field_group",
        "field_group_sort",
    }
    for collection in (
        related_package["asset_model_groups"],
        related_package["asset_models"],
        related_package["asset_fields"],
    ):
        assert all(internal_fields.isdisjoint(item) for item in collection)
    ref_fields = [
        item
        for item in related_package["asset_fields"]
        if item.get("data_type") == "ref"
    ]
    assert {item["referenced_asset_model_key"] for item in ref_fields} == {
        "server",
        "team",
    }
    assert all("referenced_asset_model_id" not in item for item in ref_fields)


def test_export_asset_model_package_ignores_unknown_requested_models(monkeypatch):
    _install_export_fakes(monkeypatch, missing_asset_model_keys={"server"})

    package = asyncio.run(
        manager.export_asset_model_package(
            "tenant-a",
            asset_model_keys=["app", "server"],
        )
    )

    assert [item["key"] for item in package["asset_models"]] == ["app"]
    assert [item["asset_model_key"] for item in package["asset_fields"]] == [
        "app",
        "app",
        "app",
    ]
    assert "relation_types" not in package


def test_export_asset_model_package_endpoint_forwards_reference_scope(monkeypatch):
    export_calls = []

    async def export_package(
        tenant,
        asset_model_keys=None,
        include_related=False,
    ):
        export_calls.append((tenant, asset_model_keys, include_related))
        return {
            "schema_version": 2,
            "asset_model_groups": [],
            "asset_models": [],
            "asset_fields": [],
            "asset_unique_rules": [],
        }

    monkeypatch.setattr(views, "export_asset_model_package", export_package)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "asset_model_keys": ["app", "server"],
            "include_related": True,
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.asset_model_export)(request))

    assert payload == {
        "schema_version": 2,
        "asset_model_groups": [],
        "asset_models": [],
        "asset_fields": [],
        "asset_unique_rules": [],
    }
    assert export_calls == [
        ("tenant-a", ["app", "server"], True),
    ]
