# coding=utf-8
import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.asset_management import manager, views


class _FakeQuery:
    def __init__(self, row=None, operations=None):
        self.row = row
        self.operations = operations
        self.connection = None
        self.locked = False
        self.ordering = ()

    def using_db(self, connection):
        self.connection = connection
        if self.operations is not None:
            self.operations.append(("using_db", connection))
        return self

    def select_for_update(self):
        self.locked = True
        if self.operations is not None:
            self.operations.append("select_for_update")
        return self

    def order_by(self, *fields):
        self.ordering = fields
        if self.operations is not None:
            self.operations.append(("order_by", fields))
        return self

    def annotate(self, **annotations):
        if self.operations is not None:
            self.operations.append(("annotate", tuple(annotations)))
        return self

    def group_by(self, *fields):
        if self.operations is not None:
            self.operations.append(("group_by", fields))
        return self

    async def first(self):
        if self.operations is not None:
            self.operations.append("first")
        if isinstance(self.row, list):
            return self.row[0] if self.row else None
        return self.row

    async def exists(self):
        return self.row is not None

    async def count(self):
        if self.row is None:
            return 0
        return len(self.row) if isinstance(self.row, list) else 1

    def offset(self, value):
        if self.operations is not None:
            self.operations.append(("offset", value))
        return self

    def limit(self, value):
        if self.operations is not None:
            self.operations.append(("limit", value))
        return self

    async def all(self):
        if self.operations is not None:
            self.operations.append("all")
        if self.row is None:
            return []
        return self.row if isinstance(self.row, list) else [self.row]

    async def values(self, *fields):
        if self.operations is not None:
            self.operations.append(("values", fields))
        if self.row is None:
            return []
        return self.row if isinstance(self.row, list) else [self.row]

    async def values_list(self, *fields, flat=False):
        rows = (
            self.row if isinstance(self.row, list) else [self.row] if self.row else []
        )
        if flat:
            return [getattr(row, fields[0]) for row in rows]
        return [tuple(getattr(row, field) for field in fields) for row in rows]

    async def delete(self):
        if self.operations is not None:
            self.operations.append(("delete", self.connection))
        return 0


class _FakeTransaction:
    def __init__(self, operations=None):
        self.operations = operations
        self.connection = object()

    async def __aenter__(self):
        if self.operations is not None:
            self.operations.append("transaction_enter")
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        if self.operations is not None:
            self.operations.append(("transaction_exit", exc_type))
        return False


def _install_transaction(monkeypatch, operations=None):
    transaction = _FakeTransaction(operations)
    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    return transaction


@pytest.fixture(autouse=True)
def _install_structure_write_tenant(monkeypatch):
    tenant = SimpleNamespace(id="tenant-a", deleted=False)
    group = _FakeRecord(
        _json={},
        id="group-custom",
        key="custom",
        name="自定义模型",
        sort=1,
        is_builtin=False,
        is_locked=False,
        owner="tenant",
    )
    monkeypatch.setattr(
        manager.Tenant,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(tenant)),
    )
    monkeypatch.setattr(
        manager.AssetModelGroup,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(group)),
    )

    async def ensure_catalog(*_args, **_kwargs):
        return SimpleNamespace(
            catalog_version="aioe-assets-2026.07-v2",
            revision=0,
        )

    monkeypatch.setattr(views, "ensure_builtin_catalog", ensure_catalog)


class _FakeRecord(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.save_calls = []
        self.delete_calls = []

    @property
    def json(self):
        return dict(self._json)

    async def save(self, **kwargs):
        self.save_calls.append(kwargs)

    async def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


def _field(field_id, field_key, asset_model_key="server", **overrides):
    payload = {
        "id": field_id,
        "tenant_id": "tenant-a",
        "asset_model_key": asset_model_key,
        "field_key": field_key,
        "label": field_key,
        "data_type": "text",
        "enum_options": [],
        "referenced_asset_model_key": None,
        "required": False,
        "unique": False,
        "default_value": None,
        "sort": 0,
        "is_locked": False,
        "is_builtin": False,
        "owner": "tenant",
    }
    payload.update(overrides)
    return _FakeRecord(_json=payload, **payload)


def _asset_model(primary_field="name", **overrides):
    payload = {
        "id": "type-1",
        "tenant_id": "tenant-a",
        "key": "server",
        "name": "服务器",
        "icon": "server",
        "category": "asset",
        "color": "#123456",
        "primary_field": primary_field,
        "status": "active",
        "is_locked": False,
        "is_builtin": False,
        "owner": "tenant",
        "group_id": "group-custom",
        "group_key": "custom",
        "sort": 0,
    }
    payload.update(overrides)
    return _FakeRecord(_json=payload, **payload)


def _field_payload(field_key, **overrides):
    payload = {
        "asset_model_key": "server",
        "field_key": field_key,
        "label": field_key,
        "data_type": "text",
    }
    payload.update(overrides)
    return payload


def _v2_package(package):
    asset_models = [
        {"group_key": "custom", **item} for item in package.get("asset_models", [])
    ]
    return {
        "schema_version": 2,
        "asset_model_groups": [{"key": "custom", "name": "自定义模型", "sort": 1}],
        "asset_models": asset_models,
        "asset_fields": package.get("asset_fields", []),
        "asset_unique_rules": package.get("asset_unique_rules", []),
    }


def _install_field_save_fakes(monkeypatch, asset_model, existing=None, created=None):
    transaction = _FakeTransaction()
    filter_calls = []
    create_kwargs = {}

    def asset_model_filter(**kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "key": "server",
            "deleted": False,
        }
        return _FakeQuery(asset_model)

    def field_filter(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(existing)

    async def create_field(**kwargs):
        create_kwargs.update(kwargs)
        return created

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition, "filter", staticmethod(field_filter)
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition, "create", staticmethod(create_field)
    )
    return transaction, filter_calls, create_kwargs


def _install_asset_create_fakes(monkeypatch, asset_model):
    operations = []
    transaction = _FakeTransaction(operations)
    create_kwargs = {}

    def asset_model_filter(**kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "key": asset_model.key,
            "deleted": False,
        }
        return _FakeQuery(asset_model, operations)

    async def create_asset(**kwargs):
        create_kwargs.update(kwargs)
        operations.append(("create", kwargs["using_db"]))
        return _FakeRecord(_json=kwargs, id="asset-1", **kwargs)

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetInstance,
        "create",
        staticmethod(create_asset),
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    return transaction, operations, create_kwargs


def _install_asset_update_fakes(monkeypatch, asset_model, existing):
    operations = []
    transaction = _FakeTransaction(operations)

    def asset_model_filter(**kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "deleted": False,
            "key": asset_model.key,
        }
        return _FakeQuery(asset_model, operations)

    def asset_filter(**kwargs):
        assert kwargs == {
            "id": existing.id,
            "tenant_id": "tenant-a",
            "deleted": False,
        }
        return _FakeQuery(existing, operations)

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(asset_filter),
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    return transaction, operations


def _install_empty_field_reference_cleanup(monkeypatch):
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )


@pytest.mark.parametrize("value", ["a", "Z", "a_1", "a" + "0" * 63])
def test_validate_key_accepts_identifiers_from_one_to_64_characters(value):
    assert manager.validate_key(value) == value


@pytest.mark.parametrize("value", ["", "1a", "_a", "a" + "0" * 64])
def test_validate_key_rejects_missing_invalid_or_overlong_identifiers(value):
    with pytest.raises(CustomError) as exc_info:
        manager.validate_key(value)

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("服务器地址", "fuwuqidizhi"),
        ("IP Address", "ip_address"),
        ("123 端口", "field_123_duankou"),
    ],
)
def test_field_key_base_is_generated_from_the_field_name(label, expected):
    assert manager._field_key_base(label) == expected


def test_duplicate_field_names_ignore_whitespace_and_english_case():
    fields = [SimpleNamespace(id="field-1", label=" Host Name ")]

    assert manager._has_duplicate_field_label(fields, "host   name") is True
    assert (
        manager._has_duplicate_field_label(
            fields,
            "host name",
            exclude_id="field-1",
        )
        is False
    )


def test_generated_field_key_uses_a_numeric_suffix_for_a_collision(monkeypatch):
    filter_calls = []

    def field_filter(**kwargs):
        filter_calls.append(kwargs)
        occupied = kwargs["field_key"] in {"fuwuqi", "fuwuqi_2"}
        return _FakeQuery(SimpleNamespace(id="occupied") if occupied else None)

    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(field_filter),
    )

    key = asyncio.run(
        manager._allocate_field_key(
            "tenant-a",
            "model-1",
            "服务器",
            object(),
        )
    )

    assert key == "fuwuqi_3"
    assert [call["field_key"] for call in filter_calls] == [
        "fuwuqi",
        "fuwuqi_2",
        "fuwuqi_3",
    ]


def test_save_field_creates_single_character_key(monkeypatch):
    asset_model = _asset_model(primary_field="")
    created = _field("field-1", "a")
    transaction, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        created=created,
    )

    item = asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("a"),
            "admin",
            expected_exists=False,
        )
    )

    assert item is created
    assert create_kwargs["field_key"] == "a"
    assert create_kwargs["using_db"] is transaction.connection


def test_save_field_generates_an_internal_key_when_the_api_omits_it(monkeypatch):
    asset_model = _asset_model(primary_field="")
    created = _field("field-1", "fuwuqi")
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        created=created,
    )
    payload = _field_payload("unused", label="服务器")
    payload.pop("field_key")

    item = asyncio.run(
        manager.save_field(
            "tenant-a",
            payload,
            "admin",
            expected_exists=False,
        )
    )

    assert item is created
    assert create_kwargs["field_key"] == "fuwuqi"


def test_reorder_fields_persists_the_complete_order_atomically(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    hostname = _field("field-1", "hostname", sort=1)
    address = _field("field-2", "ip_address", sort=2)
    transaction = _install_transaction(monkeypatch)

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery([hostname, address])),
    )

    result = asyncio.run(
        manager.reorder_fields(
            "tenant-a",
            "server",
            ["field-2", "field-1"],
            "admin",
        )
    )

    assert result == [address, hostname]
    assert [address.sort, hostname.sort] == [1, 2]
    assert address.updater == hostname.updater == "admin"
    expected_save = {
        "using_db": transaction.connection,
        "update_fields": ["sort", "updater", "utime"],
    }
    assert address.save_calls == [expected_save]
    assert hostname.save_calls == [expected_save]


def test_reorder_fields_rejects_an_incomplete_field_set(monkeypatch):
    asset_model = _asset_model()
    fields = [
        _field("field-1", "hostname", sort=1),
        _field("field-2", "ip_address", sort=2),
    ]
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(fields)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.reorder_fields(
                "tenant-a",
                "server",
                ["field-1"],
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert all(field.save_calls == [] for field in fields)


def test_reorder_fields_ignores_legacy_locked_state(monkeypatch):
    asset_model = _asset_model(is_locked=True)
    field = _field("field-1", "hostname", sort=1)
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery([field])),
    )

    result = asyncio.run(
        manager.reorder_fields(
            "tenant-a",
            "server",
            ["field-1"],
            "admin",
        )
    )

    assert result == [field]
    assert field.save_calls == []


def test_field_reorder_endpoint_forwards_the_requested_order(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    fields = [
        _field("field-2", "ip_address", sort=1),
        _field("field-1", "hostname", sort=2),
    ]
    reorder_calls = []

    async def reorder(tenant, asset_model_ref, field_ids, operator):
        reorder_calls.append((tenant, asset_model_ref, field_ids, operator))
        return fields

    async def get_asset_model(tenant, asset_model_ref):
        assert (tenant, asset_model_ref) == ("tenant-a", "type-1")
        return asset_model

    monkeypatch.setattr(views, "reorder_fields", reorder)
    monkeypatch.setattr(views, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "asset_model_id": "type-1",
            "field_ids": ["field-2", "field-1"],
        },
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-a",
                "account_info": {"username": "admin"},
            }
        ),
    )
    payload = asyncio.run(inspect.unwrap(views.asset_field_reorder)(request))

    assert reorder_calls == [("tenant-a", "type-1", ["field-2", "field-1"], "admin")]
    assert [item["id"] for item in payload["items"]] == ["field-2", "field-1"]
    assert payload["total"] == 2


def test_asset_import_prefers_selected_model_over_record_model(monkeypatch):
    save_calls = []

    async def save_asset_instance(tenant, record, operator):
        save_calls.append((tenant, record, operator))
        return _FakeRecord(_json={"id": f"asset-{len(save_calls)}"})

    monkeypatch.setattr(views, "save_asset_instance", save_asset_instance)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "asset_model_id": "target-type",
            "items": [
                {
                    "id": "source-asset",
                    "asset_model_id": "source-type",
                    "asset_model_key": "source-key",
                    "attributes": {"name": "server-1"},
                }
            ],
        },
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-a",
                "account_info": {"username": "admin"},
            }
        ),
    )

    payload = asyncio.run(inspect.unwrap(views.asset_import)(request))

    assert payload == {"total": 1, "items": [{"id": "asset-1"}]}
    assert save_calls == [
        (
            "tenant-a",
            {
                "asset_model_id": "target-type",
                "attributes": {"name": "server-1"},
            },
            "admin",
        )
    ]


def test_save_field_rejects_create_intent_for_existing_key(monkeypatch):
    asset_model = _asset_model(primary_field="")
    existing = _field("field-1", "a")
    _install_field_save_fakes(monkeypatch, asset_model, existing=existing)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload("a"),
                "admin",
                expected_exists=False,
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS
    assert existing.save_calls == []


def test_save_field_sets_new_field_as_model_primary(monkeypatch):
    asset_model = _asset_model()
    created = _field("field-1", "hostname")
    transaction, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        created=created,
    )

    item = asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("hostname", is_primary=True),
            "admin",
        )
    )

    assert item is created
    assert create_kwargs["tenant_id"] == "tenant-a"
    assert create_kwargs["using_db"] is transaction.connection
    assert asset_model.primary_field == "hostname"
    assert asset_model.updater == "admin"
    assert asset_model.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["primary_field", "updater", "utime"],
        }
    ]


def test_save_field_sets_existing_field_as_model_primary(monkeypatch):
    asset_model = _asset_model(primary_field="name")
    existing = _field("field-2", "serial_number")
    transaction, filter_calls, _ = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        existing=existing,
    )

    item = asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("serial_number", id="field-2", is_primary=True),
            "admin",
        )
    )

    assert item is existing
    assert filter_calls[0] == {
        "id": "field-2",
        "tenant_id": "tenant-a",
        "deleted": False,
    }
    assert existing.save_calls == [{"using_db": transaction.connection}]
    assert asset_model.primary_field == "serial_number"


def test_false_does_not_clear_current_primary_field(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    existing = _field("field-1", "hostname")
    _install_field_save_fakes(monkeypatch, asset_model, existing=existing)

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("hostname", id="field-1", is_primary=False),
            "admin",
        )
    )

    assert asset_model.primary_field == "hostname"
    assert asset_model.save_calls == []


def test_non_text_field_cannot_be_selected_as_primary(monkeypatch):
    asset_model = _asset_model(primary_field="name")
    created = _field("field-1", "port", data_type="number")
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        created=created,
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload("port", data_type="number", is_primary=True),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert create_kwargs == {}
    assert asset_model.primary_field == "name"
    assert asset_model.save_calls == []


def test_text_primary_field_cannot_change_to_non_text(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    existing = _field("field-1", "hostname", data_type="text")
    _install_field_save_fakes(monkeypatch, asset_model, existing=existing)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload(
                    "hostname",
                    id="field-1",
                    data_type="number",
                    is_primary=True,
                ),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert existing.data_type == "text"
    assert existing.save_calls == []
    assert asset_model.primary_field == "hostname"


def test_legacy_non_text_primary_can_be_saved_without_type_change(monkeypatch):
    asset_model = _asset_model(primary_field="port")
    existing = _field("field-1", "port", data_type="number")
    transaction, _, _ = _install_field_save_fakes(
        monkeypatch, asset_model, existing=existing
    )

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("port", id="field-1", data_type="number", is_primary=True),
            "admin",
        )
    )

    assert existing.save_calls == [{"using_db": transaction.connection}]
    assert asset_model.primary_field == "port"
    assert asset_model.save_calls == []


def test_renaming_current_primary_field_preserves_internal_key_and_model_reference(
    monkeypatch,
):
    asset_model = _asset_model(primary_field="hostname")
    existing = _field("field-1", "hostname")
    _install_field_save_fakes(monkeypatch, asset_model, existing=existing)

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("display_name", id="field-1", is_primary=False),
            "admin",
        )
    )

    assert existing.field_key == "hostname"
    assert existing.label == "display_name"
    assert asset_model.primary_field == "hostname"


def test_field_without_primary_flag_keeps_model_package_primary_field(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    created = _field("field-1", "hostname")
    _install_field_save_fakes(monkeypatch, asset_model, created=created)

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("hostname"),
            "package-import",
        )
    )

    assert asset_model.primary_field == "hostname"
    assert asset_model.save_calls == []


def test_asset_field_list_and_type_detail_mark_only_primary_field(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    fields = [_field("field-1", "hostname"), _field("field-2", "ip_address")]

    async def get_asset_model(tenant, key_or_id):
        assert tenant == "tenant-a"
        assert key_or_id in {"server", "type-1"}
        return asset_model

    async def get_fields(tenant, asset_model_key):
        assert (tenant, asset_model_key) == ("tenant-a", "server")
        return fields

    monkeypatch.setattr(views, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "fields_for_model", get_fields)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    list_request = SimpleNamespace(
        json={"asset_model_key": "server"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    detail_request = SimpleNamespace(
        json={"id": "type-1"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    list_payload = asyncio.run(inspect.unwrap(views.asset_field_list)(list_request))
    detail_payload = asyncio.run(
        inspect.unwrap(views.asset_model_detail)(detail_request)
    )

    expected_flags = {"hostname": True, "ip_address": False}
    assert {
        item["field_key"]: item["is_primary"] for item in list_payload["items"]
    } == expected_flags
    assert {
        item["field_key"]: item["is_primary"] for item in detail_payload["fields"]
    } == expected_flags


def test_delete_primary_field_is_forbidden_within_current_tenant(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    field = _field("field-1", "hostname")
    filter_calls = []
    type_calls = []

    def field_filter(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(field)

    def asset_model_filter(**kwargs):
        type_calls.append(kwargs)
        return _FakeQuery(asset_model)

    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(field_filter),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(manager, "in_transaction", _FakeTransaction)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_field("tenant-a", "field-1"))

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert filter_calls == [
        {"id": "field-1", "tenant_id": "tenant-a", "deleted": False},
        {"id": "field-1", "tenant_id": "tenant-a", "deleted": False},
    ]
    assert type_calls == [{"tenant_id": "tenant-a", "key": "server", "deleted": False}]
    assert field.save_calls == []


def test_delete_non_primary_field_uses_same_model_and_field_lock_order(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    field = _field("field-2", "ip_address")
    transaction = _FakeTransaction()

    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    _install_empty_field_reference_cleanup(monkeypatch)

    result = asyncio.run(manager.delete_field("tenant-a", "field-2"))

    assert result == {"id": "field-2"}
    assert field.deleted is True
    assert field.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["deleted", "utime"],
        }
    ]


def test_model_edit_uses_only_formal_v1_fields(monkeypatch):
    asset_model = _asset_model(
        primary_field="hostname", category="master_data", color="#abcdef"
    )
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(asset_model)),
    )

    asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {
                "id": "type-1",
                "key": "server",
                "name": "服务器模型",
                "is_locked": True,
                "primary_field": "serial_number",
            },
            "admin",
        )
    )

    assert asset_model.name == "服务器模型"
    assert asset_model.icon == "server"
    assert asset_model.primary_field == "hostname"
    assert asset_model.sort == 0
    assert asset_model.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": [
                "key",
                "name",
                "updater",
                "utime",
            ],
        }
    ]


def test_new_model_starts_without_implicit_primary_field(monkeypatch):
    created = _asset_model(primary_field="")
    create_kwargs = {}
    transaction = _install_transaction(monkeypatch)

    async def create_asset_model(**kwargs):
        create_kwargs.update(kwargs)
        return created

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "create",
        staticmethod(create_asset_model),
    )

    item = asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {
                "key": "server",
                "name": "服务器",
                "primary_field": "hostname",
            },
            "admin",
        )
    )

    assert item is created
    assert create_kwargs["primary_field"] == ""
    assert create_kwargs["sort"] == 1
    assert create_kwargs["using_db"] is transaction.connection


def test_new_model_can_select_a_product_builtin_group(monkeypatch):
    group = _FakeRecord(
        _json={},
        id="group-hardware",
        key="hardware",
        name="硬件设备",
        sort=1,
        is_builtin=True,
        is_locked=False,
    )
    created = _asset_model(
        primary_field="",
        group_id=group.id,
        group_key=group.key,
    )
    create_kwargs = {}
    transaction = _install_transaction(monkeypatch)

    class GroupQuery(_FakeQuery):
        def filter(self, *_args, **_kwargs):
            return self

    async def create_asset_model(**kwargs):
        create_kwargs.update(kwargs)
        return created

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "create",
        staticmethod(create_asset_model),
    )
    monkeypatch.setattr(
        manager.AssetModelGroup,
        "filter",
        staticmethod(lambda **_kwargs: GroupQuery(group)),
    )

    result = asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {
                "key": "server",
                "name": "服务器",
                "group_key": "hardware",
            },
            "admin",
        )
    )

    assert result is created
    assert create_kwargs["group_id"] == group.id
    assert create_kwargs["group_key"] == group.key
    assert create_kwargs["using_db"] is transaction.connection


def test_generated_group_key_is_not_filtered_as_a_model_group_id(monkeypatch):
    group_key = "group_05e8c818c8cf4c0d808d6f8df4c0942f"
    group = _FakeRecord(_json={}, id="group-id", key=group_key)
    captured = {}

    class GroupQuery(_FakeQuery):
        def filter(self, condition):
            captured["condition"] = condition
            return self

    monkeypatch.setattr(
        manager.AssetModelGroup,
        "filter",
        staticmethod(lambda **_kwargs: GroupQuery(group)),
    )

    result = asyncio.run(
        manager.resolve_asset_model_group("tenant-a", group_key, object())
    )

    assert result is group
    assert captured["condition"].filters == {"key": group_key}
    assert captured["condition"].children == ()


@pytest.mark.parametrize("sort", [-1, 1.5, True, "not-a-number", []])
def test_model_payload_rejects_invalid_sort(sort):
    with pytest.raises(CustomError) as exc_info:
        manager.payload_asset_model(
            {"key": "server", "name": "服务器", "sort": sort},
            "admin",
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_regular_model_save_cannot_overwrite_drag_order(monkeypatch):
    asset_model = _asset_model(sort=7)
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(asset_model)),
    )

    asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {
                "id": "type-1",
                "key": "server",
                "name": "服务器模型",
                "sort": 2,
            },
            "admin",
        )
    )

    assert asset_model.sort == 7
    assert asset_model.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["key", "name", "updater", "utime"],
        }
    ]


def test_reorder_asset_models_persists_the_complete_order_atomically(monkeypatch):
    server = _asset_model(id="type-1", key="server", sort=1)
    database = _asset_model(id="type-2", key="database", sort=2)
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery([server, database])),
    )

    result = asyncio.run(
        manager.reorder_asset_models(
            "tenant-a",
            ["type-2", "type-1"],
            "admin",
        )
    )

    assert result == [database, server]
    assert [database.sort, server.sort] == [1, 2]
    expected_save = {
        "using_db": transaction.connection,
        "update_fields": ["sort", "updater", "utime"],
    }
    assert database.save_calls == [expected_save]
    assert server.save_calls == [expected_save]


def test_reorder_asset_models_rejects_an_incomplete_model_set(monkeypatch):
    models = [
        _asset_model(id="type-1", key="server", sort=1),
        _asset_model(id="type-2", key="database", sort=2),
    ]
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(models)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.reorder_asset_models(
                "tenant-a",
                ["type-1"],
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert all(model.save_calls == [] for model in models)


def test_asset_model_reorder_endpoint_forwards_the_requested_order(monkeypatch):
    models = [
        _asset_model(id="type-2", key="database", sort=1),
        _asset_model(id="type-1", key="server", sort=2),
    ]
    reorder_calls = []

    async def reorder(tenant, asset_model_ids, operator):
        reorder_calls.append((tenant, asset_model_ids, operator))
        return models

    monkeypatch.setattr(views, "reorder_asset_models", reorder)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"asset_model_ids": ["type-2", "type-1"]},
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-a",
                "account_info": {"username": "admin"},
            }
        ),
    )

    payload = asyncio.run(inspect.unwrap(views.asset_model_reorder)(request))

    assert reorder_calls == [("tenant-a", ["type-2", "type-1"], "admin")]
    assert [item["id"] for item in payload["items"]] == ["type-2", "type-1"]
    assert payload["total"] == 2


def test_asset_model_list_orders_models_by_persisted_sort(monkeypatch):
    operations = []
    models = [
        _asset_model(id="type-2", key="database", sort=1),
        _asset_model(id="type-1", key="server", sort=2),
    ]
    monkeypatch.setattr(
        views.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(models, operations)),
    )
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"page": 1, "size": 100},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    payload = asyncio.run(inspect.unwrap(views.asset_model_list)(request))

    assert [item["id"] for item in payload["items"]] == ["type-2", "type-1"]
    assert ("order_by", ("sort", "key")) in operations


def test_asset_model_list_optionally_aggregates_instance_counts(monkeypatch):
    models = [
        _asset_model(id="type-1", key="server", sort=1),
        _asset_model(id="type-2", key="database", sort=2),
    ]
    aggregate_operations = []
    instance_filter_calls = []

    monkeypatch.setattr(
        views.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(models)),
    )

    def instance_filter(**kwargs):
        instance_filter_calls.append(kwargs)
        return _FakeQuery(
            [{"asset_model_id": "type-1", "instance_count": 3}],
            aggregate_operations,
        )

    monkeypatch.setattr(
        views.AssetInstance,
        "filter",
        staticmethod(instance_filter),
    )
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"page": 1, "size": 100, "include_instance_count": True},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    payload = asyncio.run(inspect.unwrap(views.asset_model_list)(request))

    assert instance_filter_calls == [
        {
            "tenant_id": "tenant-a",
            "asset_model_id__in": ["type-1", "type-2"],
            "deleted": False,
        }
    ]
    assert [item["instance_count"] for item in payload["items"]] == [3, 0]
    assert ("annotate", ("instance_count",)) in aggregate_operations
    assert ("group_by", ("asset_model_id",)) in aggregate_operations
    assert ("values", ("asset_model_id", "instance_count")) in aggregate_operations


def test_asset_model_list_skips_instance_query_without_count_option(monkeypatch):
    monkeypatch.setattr(
        views.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery([_asset_model()])),
    )

    def unexpected_instance_query(**_kwargs):
        raise AssertionError("instance counts must remain opt-in")

    monkeypatch.setattr(
        views.AssetInstance,
        "filter",
        staticmethod(unexpected_instance_query),
    )
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"page": 1, "size": 100},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    payload = asyncio.run(inspect.unwrap(views.asset_model_list)(request))

    assert "instance_count" not in payload["items"][0]


def test_required_attachment_field_rejects_empty_file_id_list():
    field = _field(
        "field-1",
        "evidence",
        data_type="attachment",
        required=True,
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.validate_field_value("tenant-a", field, []))

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_required_ref_ignores_historical_default_when_target_has_no_instances():
    field = _field(
        "field-1",
        "application",
        data_type="ref",
        referenced_asset_model_id="application-type",
        required=True,
        default_value="默认",
    )
    attributes = {}

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.validate_dynamic_attributes("tenant-a", [field], attributes)
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert attributes == {}


def test_ref_field_payload_discards_defaults_and_table_type_is_rejected():
    payload = manager.payload_dynamic_field(
        {
            "field_key": "application",
            "label": "应用",
            "data_type": "ref",
            "referenced_asset_model_id": "application-type",
            "default_value": "默认",
        }
    )

    assert payload["default_value"] is None
    with pytest.raises(CustomError) as exc_info:
        manager.payload_dynamic_field(
            {"field_key": "details", "label": "明细", "data_type": "table"}
        )
    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_destructive_rename_migration_creates_asset_model_sort_contract():
    migration = importlib.import_module(
        "migrations.models.96_20260731203000_destructive_asset_network_rename"
    )

    upgrade_sql = asyncio.run(migration.upgrade(None))
    asset_model_sql = upgrade_sql[upgrade_sql.index('CREATE TABLE "asset_model"') :]

    assert '"sort" INT NOT NULL DEFAULT 0' in asset_model_sql
    assert 'CREATE INDEX "idx_asset_model_tenant__6ca4a7"' in asset_model_sql
    assert '("tenant_id", "group_id", "sort")' in asset_model_sql


def test_model_save_rejects_create_to_update_race(monkeypatch):
    existing = _asset_model(primary_field="hostname")
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(existing)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_asset_model(
                "tenant-a",
                {"key": "server", "name": "服务器"},
                "admin",
                expected_exists=False,
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS
    assert existing.save_calls == []


def test_model_save_maps_deleted_key_tombstone_to_already_exists(monkeypatch):
    tombstone = _asset_model(primary_field="", deleted=True)
    filter_calls = []
    _install_transaction(monkeypatch)

    def filter_asset_model(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(tombstone)

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(filter_asset_model),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_asset_model(
                "tenant-a",
                {"key": "server", "name": "服务器"},
                "admin",
                expected_exists=False,
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS
    assert filter_calls == [{"tenant_id": "tenant-a", "key": "server"}]
    assert tombstone.save_calls == []


def test_instance_name_does_not_fall_back_to_unselected_name_attribute(monkeypatch):
    asset_model = _asset_model(primary_field="")
    transaction, operations, create_kwargs = _install_asset_create_fakes(
        monkeypatch,
        asset_model,
    )

    async def validate_attributes(*_args):
        return []

    monkeypatch.setattr(manager, "validate_asset_attributes", validate_attributes)

    asyncio.run(
        manager.save_asset_instance(
            "tenant-a",
            {"asset_model_key": "server", "attributes": {"name": "未勾选名称"}},
            "admin",
        )
    )

    assert create_kwargs["name"] == "server"
    assert create_kwargs["using_db"] is transaction.connection
    assert operations == [
        "transaction_enter",
        ("using_db", transaction.connection),
        "select_for_update",
        "first",
        ("create", transaction.connection),
        ("transaction_exit", None),
    ]


def test_legacy_numeric_primary_value_is_converted_to_display_text(monkeypatch):
    asset_model = _asset_model(primary_field="port")
    transaction, _, create_kwargs = _install_asset_create_fakes(
        monkeypatch,
        asset_model,
    )

    async def validate_attributes(*_args):
        return []

    monkeypatch.setattr(manager, "validate_asset_attributes", validate_attributes)

    asyncio.run(
        manager.save_asset_instance(
            "tenant-a",
            {"asset_model_key": "server", "attributes": {"port": 443}},
            "admin",
        )
    )

    assert create_kwargs["name"] == "443"
    assert create_kwargs["using_db"] is transaction.connection


def test_save_asset_instance_update_locks_instance_and_rechecks_soft_delete(
    monkeypatch,
):
    asset_model = _asset_model(primary_field="hostname")
    existing = _FakeRecord(
        _json={},
        id="asset-1",
        asset_model_key="server",
        name="old",
        project_area_id=None,
        accessory_project_id=None,
        attributes={"hostname": "old"},
        status="active",
        risk_status="normal",
    )
    transaction, operations = _install_asset_update_fakes(
        monkeypatch,
        asset_model,
        existing,
    )

    async def validate_attributes(*_args, **_kwargs):
        return []

    monkeypatch.setattr(manager, "validate_asset_attributes", validate_attributes)

    result = asyncio.run(
        manager.save_asset_instance(
            "tenant-a",
            {
                "id": "asset-1",
                "asset_model_key": "server",
                "attributes": {"hostname": "new"},
            },
            "admin",
        )
    )

    assert result is existing
    assert existing.attributes == {"hostname": "new"}
    assert existing.save_calls == [{"using_db": transaction.connection}]
    assert operations == [
        "transaction_enter",
        ("using_db", transaction.connection),
        "select_for_update",
        "first",
        ("using_db", transaction.connection),
        "select_for_update",
        "first",
        ("transaction_exit", None),
    ]


def test_model_package_is_only_path_that_can_restore_primary_field(monkeypatch):
    save_calls = []
    primary_calls = []

    async def save_type(tenant, data, operator, **_kwargs):
        save_calls.append((tenant, data["key"], operator))
        return _asset_model(primary_field="")

    async def save_package_field(*_args, **_kwargs):
        return _field("field-1", "hostname")

    async def set_primary(
        tenant,
        asset_model_key,
        primary_field,
        operator,
        **_kwargs,
    ):
        primary_calls.append((tenant, asset_model_key, primary_field, operator))
        return _asset_model(primary_field=primary_field)

    monkeypatch.setattr(manager, "save_asset_model", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    result = asyncio.run(
        manager.import_asset_model_package(
            "tenant-a",
            _v2_package(
                {
                    "asset_models": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "hostname",
                        }
                    ],
                    "asset_fields": [
                        {
                            "asset_model_key": "server",
                            "field_key": "hostname",
                            "label": "主机名",
                            "data_type": "text",
                        }
                    ],
                }
            ),
            "package-import",
        )
    )

    assert result == {
        "asset_model_groups": 1,
        "asset_models": 1,
        "asset_fields": 1,
        "asset_unique_rules": 0,
    }
    assert save_calls == [("tenant-a", "server", "package-import")]
    assert primary_calls == [("tenant-a", "server", "hostname", "package-import")]


def test_imported_export_package_upserts_by_business_key(monkeypatch):
    saved = {"asset_models": [], "asset_fields": []}
    expected_flags = {}

    async def save_type(_tenant, data, _operator, **kwargs):
        saved["asset_models"].append(data)
        expected_flags["asset_models"] = kwargs["expected_exists"]
        return _asset_model(primary_field="")

    async def save_package_field(_tenant, data, _operator, **kwargs):
        saved["asset_fields"].append(data)
        expected_flags["asset_fields"] = kwargs["expected_exists"]
        return _field("target-field", data["field_key"])

    async def set_primary(*_args, **_kwargs):
        return _asset_model(primary_field="hostname")

    monkeypatch.setattr(manager, "save_asset_model", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    result = asyncio.run(
        manager.import_asset_model_package(
            "target-tenant",
            _v2_package(
                {
                    "asset_models": [
                        {
                            "id": "source-type",
                            "tenant_id": "source-tenant",
                            "ctime": "2026-07-13T12:00:00",
                            "utime": "2026-07-13T12:30:00",
                            "creator": "source-user",
                            "updater": "source-user",
                            "deleted": False,
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "hostname",
                        }
                    ],
                    "asset_fields": [
                        {
                            "id": "source-field",
                            "tenant_id": "source-tenant",
                            "creator": "source-user",
                            "is_primary": True,
                            "asset_model_key": "server",
                            "field_key": "hostname",
                            "label": "主机名",
                            "data_type": "text",
                        }
                    ],
                }
            ),
            "package-import",
            expected_existing={
                "asset_model_groups": {"custom"},
                "asset_models": {"server"},
                "asset_fields": set(),
                "asset_unique_rules": set(),
            },
        )
    )

    assert result == {
        "asset_model_groups": 1,
        "asset_models": 1,
        "asset_fields": 1,
        "asset_unique_rules": 0,
    }
    assert saved["asset_models"] == [
        {
            "group_key": "custom",
            "key": "server",
            "name": "服务器",
            "primary_field": "hostname",
        }
    ]
    assert saved["asset_fields"] == [
        {
            "asset_model_key": "server",
            "field_key": "hostname",
            "label": "主机名",
            "data_type": "text",
        }
    ]
    assert expected_flags == {
        "asset_models": True,
        "asset_fields": False,
    }


def test_model_package_switches_primary_before_updating_old_primary(monkeypatch):
    events = []

    async def save_type(_tenant, data, _operator, **_kwargs):
        events.append(("model", data["key"]))
        return _asset_model(primary_field="name")

    async def save_package_field(_tenant, data, _operator, **_kwargs):
        events.append(("field", data["field_key"], data["data_type"]))
        return _field("field-id", data["field_key"], data_type=data["data_type"])

    async def set_primary(
        _tenant,
        asset_model_key,
        primary_field,
        _operator,
        **_kwargs,
    ):
        events.append(("primary", asset_model_key, primary_field))
        return _asset_model(primary_field=primary_field)

    monkeypatch.setattr(manager, "save_asset_model", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    asyncio.run(
        manager.import_asset_model_package(
            "tenant-a",
            _v2_package(
                {
                    "asset_models": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "hostname",
                        }
                    ],
                    "asset_fields": [
                        {
                            "asset_model_key": "server",
                            "field_key": "name",
                            "data_type": "number",
                        },
                        {
                            "asset_model_key": "server",
                            "field_key": "hostname",
                            "data_type": "text",
                        },
                    ],
                }
            ),
            "package-import",
        )
    )

    assert events == [
        ("model", "server"),
        ("field", "hostname", "text"),
        ("primary", "server", "hostname"),
        ("field", "name", "number"),
    ]


def test_model_package_reuses_one_tenant_locked_connection(monkeypatch):
    operations = []
    transaction = _install_transaction(monkeypatch, operations)
    tenant_filter_calls = []
    field_operations = []
    connection_calls = []
    existing_primary = _field("field-1", "hostname")

    def tenant_filter(**kwargs):
        tenant_filter_calls.append(kwargs)
        return _FakeQuery(
            SimpleNamespace(id="tenant-a", deleted=False),
            operations,
        )

    async def save_type(_tenant, data, _operator, **kwargs):
        connection_calls.append(("model", data["key"], kwargs["connection"]))
        return _asset_model(key=data["key"], primary_field="")

    async def save_package_field(_tenant, data, _operator, **kwargs):
        connection_calls.append(("field", data["field_key"], kwargs["connection"]))
        return _field("field-2", data["field_key"], data["asset_model_key"])

    async def set_primary(
        _tenant,
        asset_model_key,
        primary_field,
        _operator,
        **kwargs,
    ):
        connection_calls.append(("primary", asset_model_key, kwargs["connection"]))
        return _asset_model(key=asset_model_key, primary_field=primary_field)

    monkeypatch.setattr(manager.Tenant, "filter", staticmethod(tenant_filter))
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(existing_primary, field_operations)),
    )
    monkeypatch.setattr(manager, "save_asset_model", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)

    result = asyncio.run(
        manager.import_asset_model_package(
            "tenant-a",
            _v2_package(
                {
                    "asset_models": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "hostname",
                        },
                        {"key": "app", "name": "应用"},
                    ],
                    "asset_fields": [
                        {
                            "asset_model_key": "app",
                            "field_key": "name",
                            "data_type": "text",
                        }
                    ],
                }
            ),
            "package-import",
        )
    )

    assert result == {
        "asset_model_groups": 1,
        "asset_models": 2,
        "asset_fields": 1,
        "asset_unique_rules": 0,
    }
    assert tenant_filter_calls == [{"id": "tenant-a", "deleted": False}]
    assert all(
        connection is transaction.connection
        for _resource, _key, connection in connection_calls
    )
    assert ("using_db", transaction.connection) in field_operations
    assert operations.count("select_for_update") == 1
    assert operations[0] == "transaction_enter"
    assert operations[-1] == ("transaction_exit", None)


def test_model_package_discards_legacy_locked_metadata(monkeypatch):
    transaction = _install_transaction(monkeypatch)
    imported_asset_model = _asset_model(is_locked=False)
    saved_payloads = []

    async def save_type(_tenant, data, _operator, **kwargs):
        saved_payloads.append((data, kwargs))
        return imported_asset_model

    monkeypatch.setattr(manager, "save_asset_model", save_type)

    result = asyncio.run(
        manager.import_asset_model_package(
            "tenant-a",
            _v2_package(
                {
                    "asset_models": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "is_locked": True,
                        }
                    ]
                }
            ),
            "package-import",
        )
    )

    assert result == {
        "asset_model_groups": 1,
        "asset_models": 1,
        "asset_fields": 0,
        "asset_unique_rules": 0,
    }
    assert "is_locked" not in saved_payloads[0][0]
    assert saved_payloads[0][1]["allow_sort_update"] is True
    assert saved_payloads[0][1]["connection"] is transaction.connection
    assert imported_asset_model.save_calls == []


def test_model_package_rolls_back_outer_transaction_on_late_failure(monkeypatch):
    class RecordingTransaction(_FakeTransaction):
        def __init__(self):
            super().__init__()
            self.entered = False
            self.exit_type = None

        async def __aenter__(self):
            self.entered = True
            return self.connection

        async def __aexit__(self, exc_type, exc, traceback):
            self.exit_type = exc_type
            return False

    transaction = RecordingTransaction()
    save_calls = []

    async def save_type(*_args, **_kwargs):
        save_calls.append("model")
        return _asset_model(primary_field="")

    async def fail_field(*_args, **_kwargs):
        save_calls.append("field")
        raise CustomError(ErrorCode.PARAM_ERROR)

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(manager, "save_asset_model", save_type)
    monkeypatch.setattr(manager, "save_field", fail_field)

    with pytest.raises(CustomError):
        asyncio.run(
            manager.import_asset_model_package(
                "tenant-a",
                _v2_package(
                    {
                        "asset_models": [
                            {
                                "key": "server",
                                "name": "服务器",
                                "primary_field": "hostname",
                            }
                        ],
                        "asset_fields": [
                            {
                                "asset_model_key": "server",
                                "field_key": "hostname",
                                "data_type": "text",
                            }
                        ],
                    }
                ),
                "package-import",
            )
        )

    assert transaction.entered is True
    assert transaction.exit_type is CustomError
    assert save_calls == ["model", "field"]


def test_package_primary_restore_locks_model_and_field(monkeypatch):
    asset_model = _asset_model(primary_field="")
    field = _field("field-1", "hostname")
    transaction = _FakeTransaction()

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)

    result = asyncio.run(
        manager.set_package_primary_field(
            "tenant-a",
            "server",
            "hostname",
            "package-import",
        )
    )

    assert result is asset_model
    assert asset_model.primary_field == "hostname"
    assert asset_model.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["primary_field", "updater", "utime"],
        }
    ]


def test_package_primary_restore_rejects_non_text_field(monkeypatch):
    asset_model = _asset_model(primary_field="")
    field = _field("field-1", "port", data_type="number")

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.set_package_primary_field(
                "tenant-a",
                "server",
                "port",
                "package-import",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert asset_model.primary_field == ""


def test_model_package_rejects_dangling_primary_field_before_writes(monkeypatch):
    save_calls = []
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )

    async def unexpected_save(*_args, **_kwargs):
        save_calls.append((_args, _kwargs))

    monkeypatch.setattr(manager, "save_asset_model", unexpected_save)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.import_asset_model_package(
                "tenant-a",
                _v2_package(
                    {
                        "asset_models": [
                            {
                                "key": "server",
                                "name": "服务器",
                                "primary_field": "hostname",
                            }
                        ]
                    }
                ),
                "package-import",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert save_calls == []


def test_model_package_rejects_non_text_primary_before_writes(monkeypatch):
    save_calls = []
    _install_transaction(monkeypatch)

    async def unexpected_save(*_args, **_kwargs):
        save_calls.append((_args, _kwargs))

    monkeypatch.setattr(manager, "save_asset_model", unexpected_save)
    monkeypatch.setattr(manager, "save_field", unexpected_save)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.import_asset_model_package(
                "tenant-a",
                _v2_package(
                    {
                        "asset_models": [
                            {
                                "key": "server",
                                "name": "服务器",
                                "primary_field": "port",
                            }
                        ],
                        "asset_fields": [
                            {
                                "asset_model_key": "server",
                                "field_key": "port",
                                "data_type": "number",
                            }
                        ],
                    }
                ),
                "package-import",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert save_calls == []


def test_save_permission_uses_create_or_update_permission(monkeypatch):
    calls = []

    async def check_permissions(_request, permission):
        calls.append(permission)
        return True

    monkeypatch.setattr(views, "check_permissions", check_permissions)
    request = SimpleNamespace()

    asyncio.run(
        views._require_save_permission(
            request,
            False,
            "resource:c",
            "resource:u",
        )
    )
    asyncio.run(
        views._require_save_permission(
            request,
            True,
            "resource:c",
            "resource:u",
        )
    )

    assert calls == ["resource:c", "resource:u"]


def test_model_package_permissions_require_every_create_and_update_scope(
    monkeypatch,
):
    class RowsQuery:
        def __init__(self, rows):
            self.rows = rows

        async def all(self):
            return self.rows

    permission_calls = []

    monkeypatch.setattr(
        views.AssetModelGroup,
        "filter",
        staticmethod(lambda **_kwargs: RowsQuery([SimpleNamespace(key="custom")])),
    )
    monkeypatch.setattr(
        views.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: RowsQuery([SimpleNamespace(key="server")])),
    )
    monkeypatch.setattr(
        views.AssetFieldDefinition,
        "filter",
        staticmethod(
            lambda **_kwargs: RowsQuery(
                [SimpleNamespace(asset_model_key="server", field_key="hostname")]
            )
        ),
    )
    monkeypatch.setattr(
        views.AssetUniqueRule,
        "filter",
        staticmethod(
            lambda **_kwargs: RowsQuery(
                [SimpleNamespace(asset_model_key="server", key="hostname_unique")]
            )
        ),
    )

    async def check_permission(_request, permission):
        permission_calls.append(permission)
        return True

    monkeypatch.setattr(views, "check_permissions", check_permission)
    normalized, expected_existing = asyncio.run(
        views._require_model_package_permissions(
            SimpleNamespace(),
            "tenant-a",
            _v2_package(
                {
                    "asset_models": [
                        {"id": "source-server", "key": " server "},
                        {"id": "source-app", "key": "app"},
                    ],
                    "asset_fields": [
                        {
                            "id": "source-hostname",
                            "asset_model_key": " server ",
                            "field_key": " hostname ",
                        },
                        {
                            "id": "source-name",
                            "asset_model_key": "app",
                            "field_key": "name",
                        },
                    ],
                    "asset_unique_rules": [
                        {
                            "id": "source-hostname-unique",
                            "asset_model_key": "server",
                            "key": "hostname_unique",
                            "field_keys": ["hostname"],
                        }
                    ],
                }
            ),
        )
    )

    assert permission_calls == [
        "asset_management:c",
        "asset_management:u",
        "asset_management:c",
        "asset_management:u",
    ]
    assert [item["key"] for item in normalized["asset_models"]] == ["server", "app"]
    assert normalized["asset_fields"][0]["asset_model_key"] == "server"
    assert normalized["asset_fields"][0]["field_key"] == "hostname"
    assert all(
        "id" not in item
        for resource in (
            "asset_model_groups",
            "asset_models",
            "asset_fields",
            "asset_unique_rules",
        )
        for item in normalized[resource]
    )
    assert expected_existing == {
        "asset_model_groups": {"custom"},
        "asset_models": {"server"},
        "asset_fields": {("server", "hostname")},
        "asset_unique_rules": {("server", "hostname_unique")},
    }


def test_model_package_endpoint_passes_permission_snapshot_to_import(monkeypatch):
    normalized = {"asset_models": [{"key": "server"}], "asset_fields": []}
    expected_existing = {
        "asset_models": {"server"},
        "asset_fields": set(),
    }
    import_calls = []

    async def require_permissions(_request, tenant, package):
        assert tenant == "tenant-a"
        assert package == {"asset_models": [{"key": "server"}]}
        return normalized, expected_existing

    async def import_package(
        tenant,
        package,
        operator,
        expected_existing=None,
    ):
        import_calls.append((tenant, package, operator, expected_existing))
        return {"asset_models": 1, "asset_fields": 0}

    monkeypatch.setattr(
        views,
        "_require_model_package_permissions",
        require_permissions,
    )
    monkeypatch.setattr(views, "import_asset_model_package", import_package)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"package": {"asset_models": [{"key": "server"}]}},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.asset_model_import)(request))

    assert payload == {"asset_models": 1, "asset_fields": 0}
    assert import_calls == [
        ("tenant-a", normalized, None, expected_existing),
    ]


def test_destructive_rename_migration_creates_asset_primary_field_contract():
    migration = importlib.import_module(
        "migrations.models.96_20260731203000_destructive_asset_network_rename"
    )
    sql = asyncio.run(migration.upgrade(None))

    asset_model_sql = sql[sql.index('CREATE TABLE "asset_model"') :]
    assert "\"primary_field\" VARCHAR(64) NOT NULL DEFAULT ''" in asset_model_sql
    assert 'CREATE TABLE "asset_field_definition"' in sql


def test_field_save_endpoint_requires_update_permission_for_existing_id(monkeypatch):
    field = _field("field-1", "hostname")
    asset_model = _asset_model(primary_field="hostname")
    permission_calls = []
    expected_exists_calls = []

    async def require_permission(
        _request,
        exists,
        create_permission,
        update_permission,
    ):
        permission_calls.append((exists, create_permission, update_permission))

    async def save_field(*_args, **kwargs):
        expected_exists_calls.append(kwargs["expected_exists"])
        return field

    async def get_asset_model(*_args):
        return asset_model

    monkeypatch.setattr(
        views.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_field", save_field)
    monkeypatch.setattr(views, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "id": "field-1",
            "asset_model_key": "server",
            "field_key": "hostname",
            "data_type": "text",
            "is_primary": True,
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.asset_field_save)(request))

    assert permission_calls == [
        (
            True,
            "asset_management:c",
            "asset_management:u",
        )
    ]
    assert expected_exists_calls == [True]
    assert payload["is_primary"] is True


def test_model_create_endpoint_uses_create_intent_even_when_key_exists(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    permission_calls = []
    expected_exists_calls = []

    async def require_permission(
        _request,
        exists,
        create_permission,
        update_permission,
    ):
        permission_calls.append((exists, create_permission, update_permission))

    async def save_type(*_args, **kwargs):
        expected_exists_calls.append(kwargs["expected_exists"])
        return asset_model

    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_asset_model", save_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"key": " server ", "name": "服务器"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.asset_model_save)(request))

    assert permission_calls == [
        (
            False,
            "asset_management:c",
            "asset_management:u",
        )
    ]
    assert expected_exists_calls == [False]


def test_model_update_endpoint_uses_update_intent(monkeypatch):
    asset_model = _asset_model(primary_field="hostname")
    permission_calls = []
    expected_exists_calls = []

    async def require_permission(
        _request,
        exists,
        create_permission,
        update_permission,
    ):
        permission_calls.append((exists, create_permission, update_permission))

    async def save_type(*_args, **kwargs):
        expected_exists_calls.append(kwargs["expected_exists"])
        return asset_model

    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_asset_model", save_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"id": "type-1", "key": "server", "name": "服务器"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.asset_model_save)(request))

    assert permission_calls == [
        (
            True,
            "asset_management:c",
            "asset_management:u",
        )
    ]
    assert expected_exists_calls == [True]


def test_field_create_endpoint_uses_create_intent_even_when_key_exists(monkeypatch):
    field = _field("field-1", "hostname")
    asset_model = _asset_model(primary_field="hostname")
    permission_calls = []
    expected_exists_calls = []

    async def require_permission(
        _request,
        exists,
        create_permission,
        update_permission,
    ):
        permission_calls.append((exists, create_permission, update_permission))

    async def save_package_field(*_args, **kwargs):
        expected_exists_calls.append(kwargs["expected_exists"])
        return field

    async def get_asset_model(*_args):
        return asset_model

    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_field", save_package_field)
    monkeypatch.setattr(views, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "asset_model_key": " server ",
            "field_key": " hostname ",
            "data_type": "text",
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.asset_field_save)(request))

    assert permission_calls == [
        (
            False,
            "asset_management:c",
            "asset_management:u",
        )
    ]
    assert expected_exists_calls == [False]


def test_product_builtin_model_can_be_renamed_without_changing_lock_metadata(
    monkeypatch,
):
    asset_model = _asset_model(is_locked=True, is_builtin=True, owner="product")
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(asset_model)),
    )

    result = asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {
                "id": "type-1",
                "key": "server",
                "name": "已修改的服务器模型",
                "status": "active",
                "is_locked": False,
            },
            "admin",
        )
    )

    assert result is asset_model
    assert asset_model.name == "已修改的服务器模型"
    assert asset_model.is_locked is True
    assert asset_model.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["key", "name", "updater", "utime"],
        }
    ]


@pytest.mark.parametrize(
    "field_data",
    [
        _field_payload("hostname"),
        _field_payload("hostname", id="field-1"),
    ],
    ids=["create", "update"],
)
def test_legacy_locked_model_allows_field_create_and_update(monkeypatch, field_data):
    asset_model = _asset_model(is_locked=True)
    existing = _field("field-1", "hostname") if field_data.get("id") else None
    created = _field("field-2", "hostname") if existing is None else None
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        existing=existing,
        created=created,
    )

    result = asyncio.run(manager.save_field("tenant-a", field_data, "admin"))

    if existing:
        assert result is existing
        assert existing.save_calls
    else:
        assert result is created
        assert create_kwargs["asset_model_id"] == asset_model.id


def test_product_builtin_model_allows_tenant_overlay_field(monkeypatch):
    asset_model = _asset_model(is_locked=True, is_builtin=True, owner="product")
    created = _field("overlay-field", "business_owner")
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        asset_model,
        created=created,
    )

    result = asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("business_owner", label="业务负责人"),
            "admin",
        )
    )

    assert result is created
    assert create_kwargs["asset_model_id"] == asset_model.id
    assert create_kwargs["asset_model_key"] == asset_model.key
    assert "is_builtin" not in create_kwargs
    assert "owner" not in create_kwargs


def test_product_builtin_field_can_be_updated_and_deleted(monkeypatch):
    asset_model = _asset_model(is_locked=True, is_builtin=True, owner="product")
    field = _field(
        "field-1",
        "hostname",
        is_locked=True,
        is_builtin=True,
        owner="product",
    )
    _install_field_save_fakes(monkeypatch, asset_model, existing=field)

    updated = asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("hostname", id="field-1", label="新名称"),
            "admin",
        )
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    _install_empty_field_reference_cleanup(monkeypatch)
    deleted = asyncio.run(manager.delete_field("tenant-a", "field-1"))

    assert updated is field
    assert field.label == "新名称"
    assert deleted == {"id": "field-1"}
    assert field.deleted is True
    assert len(field.save_calls) == 2


def test_legacy_locked_model_allows_field_delete_and_primary_change(monkeypatch):
    asset_model = _asset_model(primary_field="hostname", is_locked=True)
    field = _field("field-2", "ip_address")

    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(manager, "in_transaction", _FakeTransaction)
    _install_empty_field_reference_cleanup(monkeypatch)

    deleted = asyncio.run(manager.delete_field("tenant-a", "field-2"))
    primary = asyncio.run(
        manager.set_package_primary_field(
            "tenant-a",
            "server",
            "ip_address",
            "package-import",
        )
    )

    assert deleted == {"id": "field-2"}
    assert field.deleted is True
    assert primary is asset_model
    assert asset_model.primary_field == "ip_address"
    assert field.save_calls
    assert asset_model.save_calls


def test_legacy_locked_reference_model_allows_new_ref_field(monkeypatch):
    owner_asset_model = _asset_model(key="application")
    locked_referenced_asset_model_key = _asset_model(key="server", is_locked=True)
    create_calls = []

    def asset_model_filter(**kwargs):
        if kwargs.get("key") == "application":
            return _FakeQuery(owner_asset_model)
        if kwargs.get("key") == "server":
            return _FakeQuery(locked_referenced_asset_model_key)
        if kwargs.get("key__in") == ["server"]:
            return _FakeQuery([locked_referenced_asset_model_key])
        return _FakeQuery()

    async def create_field(**kwargs):
        create_calls.append(kwargs)

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "create",
        staticmethod(create_field),
    )

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload(
                "server_ref",
                asset_model_key="application",
                data_type="ref",
                referenced_asset_model_key="server",
            ),
            "admin",
        )
    )

    assert (
        create_calls[0]["referenced_asset_model_id"]
        == locked_referenced_asset_model_key.id
    )


def test_legacy_locked_reference_model_allows_ref_field_redirect(monkeypatch):
    owner_asset_model = _asset_model(key="application")
    locked_old_ref = _asset_model(key="server", is_locked=True)
    new_ref = _asset_model(key="database")
    existing = _field(
        "field-1",
        "target_ref",
        "application",
        data_type="ref",
        referenced_asset_model_key="server",
    )

    def asset_model_filter(**kwargs):
        if kwargs.get("key") == "application":
            return _FakeQuery(owner_asset_model)
        if kwargs.get("key") == "database":
            return _FakeQuery(new_ref)
        if kwargs.get("key__in") == ["database", "server"]:
            return _FakeQuery([new_ref, locked_old_ref])
        return _FakeQuery()

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(existing)),
    )
    _install_empty_field_reference_cleanup(monkeypatch)

    result = asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload(
                "target_ref",
                id="field-1",
                asset_model_key="application",
                data_type="ref",
                referenced_asset_model_key="database",
            ),
            "admin",
        )
    )

    assert result is existing
    assert existing.referenced_asset_model_key == "database"
    assert existing.save_calls


def test_save_field_reports_incompatible_existing_scalar_for_multiple_ref(
    monkeypatch,
):
    asset_model = _asset_model(key="application")
    referenced_asset_model_key = _asset_model(key="employee", id="employee-model")
    existing = _field(
        "field-1",
        "owner",
        "application",
        data_type="ref",
        referenced_asset_model_key="employee",
        referenced_asset_model_id="employee-model",
        options={"multiple": False, "reverse_unique": False},
    )
    _install_field_save_fakes(monkeypatch, asset_model, existing=existing)

    def asset_model_filter(**kwargs):
        if kwargs.get("key") == "employee":
            return _FakeQuery(referenced_asset_model_key)
        if kwargs.get("key__in") == ["employee"]:
            return _FakeQuery([referenced_asset_model_key])
        return _FakeQuery(asset_model)

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(
            lambda **_kwargs: _FakeQuery(
                SimpleNamespace(
                    id="application-1",
                    attributes={"owner": "00000000-0000-0000-0000-000000000001"},
                )
            )
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload(
                    "owner",
                    id="field-1",
                    asset_model_key="application",
                    data_type="ref",
                    referenced_asset_model_key="employee",
                    options={"multiple": True},
                ),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.ASSET_FIELD_DATA_INCOMPATIBLE
    assert "已有资产实例数据" in exc_info.value.enum.message
    assert existing.save_calls == []


def test_save_field_preserves_existing_reference_unique_violation(monkeypatch):
    asset_model = _asset_model(key="application")
    referenced_asset_model_key = _asset_model(key="employee", id="employee-model")
    existing = _field(
        "field-1",
        "owners",
        "application",
        data_type="ref",
        referenced_asset_model_key="employee",
        referenced_asset_model_id="employee-model",
        options={"multiple": True, "reverse_unique": False},
    )
    _install_field_save_fakes(monkeypatch, asset_model, existing=existing)

    def asset_model_filter(**kwargs):
        if kwargs.get("key") == "employee":
            return _FakeQuery(referenced_asset_model_key)
        if kwargs.get("key__in") == ["employee"]:
            return _FakeQuery([referenced_asset_model_key])
        return _FakeQuery(asset_model)

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    instances = [
        SimpleNamespace(id="application-1", attributes={"owners": ["employee-1"]}),
        SimpleNamespace(id="application-2", attributes={"owners": ["employee-1"]}),
    ]

    def asset_filter(**kwargs):
        if kwargs.get("asset_model_id") == asset_model.id:
            return _FakeQuery(instances)
        return _FakeQuery(SimpleNamespace(id="employee-1"))

    monkeypatch.setattr(manager.AssetInstance, "filter", staticmethod(asset_filter))

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload(
                    "owners",
                    id="field-1",
                    asset_model_key="application",
                    data_type="ref",
                    referenced_asset_model_key="employee",
                    options={"multiple": True},
                    unique=True,
                ),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS
    assert existing.save_calls == []


def test_save_field_preserves_unrelated_ref_request_shape_error():
    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload(
                    "owners",
                    id="field-1",
                    asset_model_key="application",
                    data_type="ref",
                    referenced_asset_model_key="employee",
                    options={"multiple": "true"},
                ),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_legacy_locked_reference_model_allows_ref_field_delete(monkeypatch):
    owner_asset_model = _asset_model(key="application")
    locked_referenced_asset_model_key = _asset_model(key="server", is_locked=True)
    field = _field(
        "field-1",
        "server_ref",
        "application",
        data_type="ref",
        referenced_asset_model_key="server",
    )

    def asset_model_filter(**kwargs):
        if kwargs.get("key") == "application":
            return _FakeQuery(owner_asset_model)
        if kwargs.get("key__in") == ["server"]:
            return _FakeQuery([locked_referenced_asset_model_key])
        return _FakeQuery()

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    _install_empty_field_reference_cleanup(monkeypatch)

    result = asyncio.run(manager.delete_field("tenant-a", "field-1"))

    assert result == {"id": "field-1"}
    assert field.deleted is True
    assert field.save_calls


def test_model_package_normalization_discards_legacy_lock_metadata():
    package = manager.normalize_asset_model_package(
        _v2_package(
            {
                "asset_models": [
                    {
                        "key": "server",
                        "name": "服务器",
                        "is_locked": True,
                    }
                ]
            }
        )
    )

    assert "is_locked" not in package["asset_models"][0]


def _install_model_delete_fakes(
    monkeypatch,
    asset_model,
    *,
    assets=None,
    referencing_fields=None,
):
    operations = []
    field_queries = []
    transaction = _install_transaction(monkeypatch)

    class FieldUpdateQuery:
        def __init__(self):
            self.connection = None

        def using_db(self, connection):
            self.connection = connection
            return self

        async def update(self, **kwargs):
            operations.append(("update", kwargs, self.connection))

        async def delete(self):
            operations.append(("delete", self.connection))

    def active_rows(rows, filters):
        if "deleted" not in filters:
            return rows
        return [
            row for row in rows if getattr(row, "deleted", False) is filters["deleted"]
        ]

    def field_filter(**kwargs):
        field_queries.append(kwargs)
        if (
            "referenced_asset_model_key" in kwargs
            or "referenced_asset_model_id" in kwargs
        ) and kwargs.get("deleted") is not True:
            return _FakeQuery(active_rows(referencing_fields or [], kwargs))
        return FieldUpdateQuery()

    class BulkDeleteQuery:
        def __init__(self, op_name, rows=None):
            self.op_name = op_name
            self.connection = None
            self.rows = rows or []

        def using_db(self, connection):
            self.connection = connection
            return self

        def order_by(self, *fields):
            return self

        def select_for_update(self):
            return self

        async def first(self):
            return None

        async def all(self):
            return []

        async def values_list(self, *fields, flat=False):
            if flat:
                field = fields[0]
                return [getattr(row, field) for row in self.rows]
            return [tuple(getattr(row, field) for field in fields) for row in self.rows]

        async def delete(self):
            operations.append((self.op_name, self.connection))

        async def update(self, **kwargs):
            operations.append(("update", kwargs, self.connection))

    async def delete_model(**kwargs):
        asset_model.delete_calls.append(kwargs)
        operations.append(("model_delete", kwargs["using_db"]))

    asset_model.delete = delete_model

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(asset_model)),
    )

    def asset_filter(**kwargs):
        rows = active_rows(assets or [], kwargs)
        # Hard-delete path on empty / tombstone-only models: no deleted=False
        # filter means purge all remaining assets of this type. The same filter
        # backs both the id lookup (values_list) that scopes edge cleanup and
        # the subsequent bulk delete, so expose every remaining asset row.
        if "deleted" not in kwargs and "id" not in kwargs:
            return BulkDeleteQuery("asset_delete", rows=assets or [])
        return _FakeQuery(rows)

    def reference_filter(*_args, **_kwargs):
        return BulkDeleteQuery("reference_delete")

    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(asset_filter),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(field_filter),
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(reference_filter),
    )
    return transaction, operations, field_queries


def test_model_delete_ignores_legacy_locked_state(monkeypatch):
    asset_model = _asset_model(key="application", is_locked=True)
    _, operations, _ = _install_model_delete_fakes(monkeypatch, asset_model)

    result = asyncio.run(manager.delete_asset_model("tenant-a", "application"))

    assert result == {"id": asset_model.id}
    assert asset_model.delete_calls
    assert any(operation[0] == "model_delete" for operation in operations)


def test_model_delete_rejects_active_asset(monkeypatch):
    asset_model = _asset_model(key="application")
    asset = _FakeRecord(
        _json={},
        id="asset-1",
        asset_model_id=asset_model.id,
        asset_model_key="application",
        deleted=False,
    )
    _, operations, _ = _install_model_delete_fakes(
        monkeypatch,
        asset_model,
        assets=[asset],
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_asset_model("tenant-a", "application"))

    assert exc_info.value.enum is ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES
    assert asset_model.save_calls == []
    assert operations == []


def test_model_delete_purges_soft_deleted_assets(monkeypatch):
    """Soft-deleted instance tombstones must not block model deletion."""
    asset_model = _asset_model(key="application")
    soft_deleted = _FakeRecord(
        _json={},
        id="asset-soft",
        asset_model_id=asset_model.id,
        asset_model_key="application",
        deleted=True,
    )
    transaction, operations, field_queries = _install_model_delete_fakes(
        monkeypatch,
        asset_model,
        assets=[soft_deleted],
    )

    result = asyncio.run(manager.delete_asset_model("tenant-a", "application"))

    assert result == {"id": "type-1"}
    assert ("reference_delete", transaction.connection) in operations
    assert ("asset_delete", transaction.connection) in operations
    assert ("model_delete", transaction.connection) in operations
    assert field_queries  # field tombstone detach + own field purge still run


def test_model_delete_rejects_external_inbound_ref(monkeypatch):
    asset_model = _asset_model(key="application")
    inbound_ref = _field(
        "field-1",
        "application_ref",
        "server",
        data_type="ref",
        referenced_asset_model_id=asset_model.id,
        referenced_asset_model_key="application",
    )
    _, operations, _ = _install_model_delete_fakes(
        monkeypatch,
        asset_model,
        referencing_fields=[inbound_ref],
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_asset_model("tenant-a", "application"))

    assert exc_info.value.enum is ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES
    assert asset_model.save_calls == []
    assert operations == []


def test_model_delete_by_key_ignores_deleted_dependencies_and_detaches_id_only_field_tombstone(
    monkeypatch,
):
    model_id = "11111111-1111-1111-1111-111111111111"
    asset_model = _asset_model(id=model_id, key="application")
    deleted_inbound_ref = _field(
        "field-1",
        "application_ref",
        "server",
        asset_model_id="server-type",
        data_type="ref",
        referenced_asset_model_id=model_id,
        referenced_asset_model_key=None,
        deleted=True,
    )
    transaction, operations, field_queries = _install_model_delete_fakes(
        monkeypatch,
        asset_model,
        referencing_fields=[deleted_inbound_ref],
    )

    result = asyncio.run(manager.delete_asset_model("tenant-a", "application"))

    assert result == {"id": model_id}
    assert field_queries == [
        {
            "tenant_id": "tenant-a",
            "deleted": False,
            "referenced_asset_model_id": model_id,
        },
        {
            "tenant_id": "tenant-a",
            "deleted": True,
            "referenced_asset_model_id": model_id,
        },
        {"tenant_id": "tenant-a", "asset_model_id": model_id},
    ]
    # No live or tombstone instances exist, so reference-index cleanup is skipped.
    assert operations == [
        (
            "update",
            {"referenced_asset_model_id": None, "referenced_asset_model_key": None},
            transaction.connection,
        ),
        ("asset_delete", transaction.connection),
        ("delete", transaction.connection),
        ("model_delete", transaction.connection),
    ]


def test_model_delete_allows_self_reference_field(monkeypatch):
    asset_model = _asset_model(key="application")
    self_ref = _field(
        "field-1",
        "parent",
        "application",
        data_type="ref",
        referenced_asset_model_id=asset_model.id,
        referenced_asset_model_key="application",
    )
    transaction, operations, _ = _install_model_delete_fakes(
        monkeypatch,
        asset_model,
        referencing_fields=[self_ref],
    )

    result = asyncio.run(manager.delete_asset_model("tenant-a", "application"))

    assert result == {"id": "type-1"}
    assert asset_model.save_calls == []
    assert asset_model.delete_calls == [{"using_db": transaction.connection}]
    # No instances exist for this model, so edge cleanup is skipped.
    assert operations == [
        (
            "update",
            {"referenced_asset_model_id": None, "referenced_asset_model_key": None},
            transaction.connection,
        ),
        ("asset_delete", transaction.connection),
        ("delete", transaction.connection),
        ("model_delete", transaction.connection),
    ]


def test_structure_model_locks_use_stable_key_order_and_transaction_connection(
    monkeypatch,
):
    operations = []
    filter_calls = []
    connection = object()
    asset_models = [
        _asset_model(key="database"),
        _asset_model(key="server"),
    ]

    def asset_model_filter(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(asset_models, operations)

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )

    result = asyncio.run(
        manager.lock_asset_model_keys_for_structure(
            "tenant-a",
            {"server", "database"},
            connection,
        )
    )

    assert result == asset_models
    assert filter_calls == [
        {
            "tenant_id": "tenant-a",
            "deleted": False,
            "key__in": ["database", "server"],
        }
    ]
    assert operations == [
        ("using_db", connection),
        ("order_by", ("key",)),
        "select_for_update",
        "all",
    ]


def test_structure_transaction_locks_tenant_once_before_nested_work(monkeypatch):
    operations = []
    transaction = _FakeTransaction(operations)
    tenant_filter_calls = []

    def tenant_filter(**kwargs):
        tenant_filter_calls.append(kwargs)
        return _FakeQuery(
            SimpleNamespace(id="tenant-a", deleted=False),
            operations,
        )

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(manager.Tenant, "filter", staticmethod(tenant_filter))

    async def exercise():
        async with manager.structure_write_transaction("tenant-a") as connection:
            operations.append(("outer_body", connection))
            async with manager.structure_write_transaction(
                "tenant-a",
                connection,
            ) as nested_connection:
                operations.append(("nested_body", nested_connection))

    asyncio.run(exercise())

    assert tenant_filter_calls == [{"id": "tenant-a", "deleted": False}]
    assert operations == [
        "transaction_enter",
        ("using_db", transaction.connection),
        "select_for_update",
        "first",
        ("outer_body", transaction.connection),
        ("nested_body", transaction.connection),
        ("transaction_exit", None),
    ]


def test_model_create_checks_business_key_after_tenant_lock(monkeypatch):
    operations = []
    transaction = _FakeTransaction(operations)
    created = _asset_model(primary_field="")

    def tenant_filter(**kwargs):
        operations.append(("tenant_filter", kwargs))
        return _FakeQuery(
            SimpleNamespace(id="tenant-a", deleted=False),
            operations,
        )

    def asset_model_filter(**kwargs):
        operations.append(("asset_model_filter", kwargs))
        return _FakeQuery(None, operations)

    async def create_asset_model(**kwargs):
        operations.append(("create", kwargs["using_db"]))
        return created

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(manager.Tenant, "filter", staticmethod(tenant_filter))
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(asset_model_filter),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "create",
        staticmethod(create_asset_model),
    )

    result = asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {"key": "server", "name": "服务器"},
            "admin",
        )
    )

    assert result is created
    assert operations.index("select_for_update") < next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, tuple) and operation[0] == "asset_model_filter"
    )
    assert operations[-2] == ("create", transaction.connection)
    assert operations[-1] == ("transaction_exit", None)


def test_save_asset_model_ignores_legacy_lock_state_after_waiting_for_row_lock(
    monkeypatch,
):
    operations = []
    asset_model = _asset_model(is_locked=False)
    transaction = _install_transaction(monkeypatch, operations)

    class ConcurrentLockQuery(_FakeQuery):
        def select_for_update(self):
            asset_model.is_locked = True
            return super().select_for_update()

    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(
            lambda *_args, **_kwargs: ConcurrentLockQuery(asset_model, operations)
        ),
    )

    result = asyncio.run(
        manager.save_asset_model(
            "tenant-a",
            {
                "id": "type-1",
                "key": "server",
                "name": "并发修改",
            },
            "admin",
        )
    )

    assert result is asset_model
    assert asset_model.name == "并发修改"
    assert asset_model.save_calls
    assert operations == [
        "transaction_enter",
        ("using_db", transaction.connection),
        ("order_by", ("key",)),
        "select_for_update",
        "first",
        ("transaction_exit", None),
    ]


@pytest.mark.parametrize(
    "operation",
    [
        manager.save_asset_model,
        manager.save_field,
        manager.delete_field,
        manager.set_package_primary_field,
        manager.delete_asset_instance,
        manager.delete_asset_model,
    ],
)
def test_every_model_structure_write_uses_tenant_transaction_helper(operation):
    source = inspect.getsource(operation)

    assert (
        "async with structure_write_transaction(tenant, connection) as connection:"
        in source
    )


def test_locked_model_allows_asset_instance_edit_and_integrity_cleanup_delete(
    monkeypatch,
):
    asset_model = _asset_model(is_locked=True)
    asset = _FakeRecord(
        _json={},
        id="asset-1",
        tenant_id="tenant-a",
        asset_model_id="type-1",
        asset_model_key="server",
        name="旧名称",
        project_area_id=None,
        accessory_project_id=None,
        attributes={"hostname": "old"},
        status="active",
        risk_status="normal",
    )
    reference_updates = []
    transaction = _install_transaction(monkeypatch)

    async def no_validation(*_args, **_kwargs):
        return None

    async def get_asset_model(*_args):
        return asset_model

    class ReferenceQuery:
        def __init__(self):
            self.connection = None

        def using_db(self, connection):
            self.connection = connection
            return self

        async def exists(self):
            return False

        async def delete(self):
            reference_updates.append(("delete", self.connection))

    monkeypatch.setattr(manager, "validate_asset_attributes", no_validation)
    monkeypatch.setattr(manager, "sync_asset_references", no_validation)
    monkeypatch.setattr(manager, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset)),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(lambda *_args, **_kwargs: ReferenceQuery()),
    )

    result = asyncio.run(
        manager.save_asset_instance(
            "tenant-a",
            {
                "id": "asset-1",
                "asset_model_key": "server",
                "attributes": {"hostname": "new"},
            },
            "admin",
        )
    )
    delete_request = SimpleNamespace(
        json={"id": "asset-1"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    delete_result = asyncio.run(inspect.unwrap(views.asset_delete)(delete_request))

    assert result is asset
    assert asset.attributes == {"hostname": "new"}
    # Instance deletion is hard-delete: no soft-delete flag, record.delete() called.
    assert asset.delete_calls == [{"using_db": transaction.connection}]
    assert delete_result == {"id": "asset-1"}
    assert reference_updates == [("delete", transaction.connection)]
