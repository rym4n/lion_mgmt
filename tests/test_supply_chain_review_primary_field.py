# coding=utf-8
import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.supply_chain_review import manager, views


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
    monkeypatch.setattr(
        manager.Tenant,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(tenant)),
    )


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


def _field(field_id, field_key, ci_type_key="server", **overrides):
    payload = {
        "id": field_id,
        "tenant_id": "tenant-a",
        "ci_type_key": ci_type_key,
        "field_key": field_key,
        "label": field_key,
        "data_type": "text",
        "enum_options": [],
        "ref_ci_type": None,
        "required": False,
        "unique": False,
        "default_value": None,
        "sort": 0,
        "is_locked": False,
    }
    payload.update(overrides)
    return _FakeRecord(_json=payload, **payload)


def _ci_type(primary_field="name", **overrides):
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
        "sort": 0,
    }
    payload.update(overrides)
    return _FakeRecord(_json=payload, **payload)


def _field_payload(field_key, **overrides):
    payload = {
        "ci_type_key": "server",
        "field_key": field_key,
        "label": field_key,
        "data_type": "text",
    }
    payload.update(overrides)
    return payload


def _install_field_save_fakes(monkeypatch, ci_type, existing=None, created=None):
    transaction = _FakeTransaction()
    filter_calls = []
    create_kwargs = {}

    def ci_type_filter(**kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "key": "server",
            "deleted": False,
        }
        return _FakeQuery(ci_type)

    def field_filter(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(existing)

    async def create_field(**kwargs):
        create_kwargs.update(kwargs)
        return created

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef, "filter", staticmethod(field_filter)
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef, "create", staticmethod(create_field)
    )
    return transaction, filter_calls, create_kwargs


def _install_ci_create_fakes(monkeypatch, ci_type):
    operations = []
    transaction = _FakeTransaction(operations)
    create_kwargs = {}

    def ci_type_filter(**kwargs):
        assert kwargs == {
            "tenant_id": "tenant-a",
            "key": ci_type.key,
            "deleted": False,
        }
        return _FakeQuery(ci_type, operations)

    async def create_ci(**kwargs):
        create_kwargs.update(kwargs)
        operations.append(("create", kwargs["using_db"]))
        return _FakeRecord(_json=kwargs, id="ci-1", **kwargs)

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "create",
        staticmethod(create_ci),
    )
    return transaction, operations, create_kwargs


def _install_ci_update_fakes(monkeypatch, ci_type, existing):
    operations = []
    transaction = _FakeTransaction(operations)

    async def get_ci_type(tenant, key):
        assert (tenant, key) == ("tenant-a", ci_type.key)
        return ci_type

    def ci_filter(**kwargs):
        assert kwargs == {
            "id": existing.id,
            "tenant_id": "tenant-a",
            "deleted": False,
        }
        return _FakeQuery(existing, operations)

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(manager, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(ci_filter),
    )
    return transaction, operations


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

    assert manager._has_duplicate_field_label(fields, "host name") is True
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
        manager.SupplyChainCiFieldDef,
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


def test_generated_relation_key_uses_name_and_numeric_suffix(monkeypatch):
    filter_calls = []

    def relation_filter(**kwargs):
        filter_calls.append(kwargs)
        occupied = kwargs["key"] in {"yilai", "yilai_2"}
        return _FakeQuery(SimpleNamespace(id="occupied") if occupied else None)

    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(relation_filter),
    )

    key = asyncio.run(
        manager._allocate_relation_key("tenant-a", "依赖", object())
    )

    assert key == "yilai_3"
    assert [call["key"] for call in filter_calls] == [
        "yilai",
        "yilai_2",
        "yilai_3",
    ]


def test_duplicate_relation_name_is_scoped_to_overlapping_models():
    relation_type = SimpleNamespace(
        name=" Depends On ",
        source_ci_type_ids=["model-a"],
        source_ci_types=[],
        target_ci_type_ids=["model-b"],
        target_ci_types=[],
    )

    assert manager._relation_name_overlaps(
        relation_type, "depends on", {"model-b", "model-c"}
    )
    assert not manager._relation_name_overlaps(
        relation_type, "depends on", {"model-c"}
    )
    assert not manager._relation_name_overlaps(
        relation_type, "contains", {"model-a"}
    )

def test_save_field_creates_single_character_key(monkeypatch):
    ci_type = _ci_type(primary_field="")
    created = _field("field-1", "a")
    transaction, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        ci_type,
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
    ci_type = _ci_type(primary_field="")
    created = _field("field-1", "fuwuqi")
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        ci_type,
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
    ci_type = _ci_type(primary_field="hostname")
    hostname = _field("field-1", "hostname", sort=1)
    address = _field("field-2", "ip_address", sort=2)
    transaction = _install_transaction(monkeypatch)

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
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
    ci_type = _ci_type()
    fields = [
        _field("field-1", "hostname", sort=1),
        _field("field-2", "ip_address", sort=2),
    ]
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
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


def test_reorder_fields_rejects_a_locked_model(monkeypatch):
    ci_type = _ci_type(is_locked=True)
    field = _field("field-1", "hostname", sort=1)
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery([field])),
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

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert field.save_calls == []


def test_field_reorder_endpoint_forwards_the_requested_order(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    fields = [
        _field("field-2", "ip_address", sort=1),
        _field("field-1", "hostname", sort=2),
    ]
    reorder_calls = []

    async def reorder(tenant, ci_type_ref, field_ids, operator):
        reorder_calls.append((tenant, ci_type_ref, field_ids, operator))
        return fields

    async def get_ci_type(tenant, ci_type_ref):
        assert (tenant, ci_type_ref) == ("tenant-a", "type-1")
        return ci_type

    monkeypatch.setattr(views, "reorder_fields", reorder)
    monkeypatch.setattr(views, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "ci_type_id": "type-1",
            "field_ids": ["field-2", "field-1"],
        },
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-a",
                "account_info": {"username": "admin"},
            }
        ),
    )
    payload = asyncio.run(inspect.unwrap(views.ci_field_reorder)(request))

    assert reorder_calls == [
        ("tenant-a", "type-1", ["field-2", "field-1"], "admin")
    ]
    assert [item["id"] for item in payload["items"]] == ["field-2", "field-1"]
    assert payload["total"] == 2


def test_save_field_rejects_create_intent_for_existing_key(monkeypatch):
    ci_type = _ci_type(primary_field="")
    existing = _field("field-1", "a")
    _install_field_save_fakes(monkeypatch, ci_type, existing=existing)

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
    ci_type = _ci_type()
    created = _field("field-1", "hostname")
    transaction, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        ci_type,
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
    assert ci_type.primary_field == "hostname"
    assert ci_type.updater == "admin"
    assert ci_type.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["primary_field", "updater", "utime"],
        }
    ]


def test_save_field_sets_existing_field_as_model_primary(monkeypatch):
    ci_type = _ci_type(primary_field="name")
    existing = _field("field-2", "serial_number")
    transaction, filter_calls, _ = _install_field_save_fakes(
        monkeypatch,
        ci_type,
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
    assert ci_type.primary_field == "serial_number"


def test_false_does_not_clear_current_primary_field(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    existing = _field("field-1", "hostname")
    _install_field_save_fakes(monkeypatch, ci_type, existing=existing)

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("hostname", id="field-1", is_primary=False),
            "admin",
        )
    )

    assert ci_type.primary_field == "hostname"
    assert ci_type.save_calls == []


def test_non_text_field_cannot_be_selected_as_primary(monkeypatch):
    ci_type = _ci_type(primary_field="name")
    created = _field("field-1", "port", data_type="number")
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        ci_type,
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
    assert ci_type.primary_field == "name"
    assert ci_type.save_calls == []


def test_text_primary_field_cannot_change_to_non_text(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    existing = _field("field-1", "hostname", data_type="text")
    _install_field_save_fakes(monkeypatch, ci_type, existing=existing)

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
    assert ci_type.primary_field == "hostname"


def test_legacy_non_text_primary_can_be_saved_without_type_change(monkeypatch):
    ci_type = _ci_type(primary_field="port")
    existing = _field("field-1", "port", data_type="number")
    transaction, _, _ = _install_field_save_fakes(
        monkeypatch, ci_type, existing=existing
    )

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("port", id="field-1", data_type="number", is_primary=True),
            "admin",
        )
    )

    assert existing.save_calls == [{"using_db": transaction.connection}]
    assert ci_type.primary_field == "port"
    assert ci_type.save_calls == []


def test_renaming_current_primary_field_preserves_internal_key_and_model_reference(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    existing = _field("field-1", "hostname")
    _install_field_save_fakes(monkeypatch, ci_type, existing=existing)

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("display_name", id="field-1", is_primary=False),
            "admin",
        )
    )

    assert existing.field_key == "hostname"
    assert existing.label == "display_name"
    assert ci_type.primary_field == "hostname"


def test_field_without_primary_flag_keeps_model_package_primary_field(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    created = _field("field-1", "hostname")
    _install_field_save_fakes(monkeypatch, ci_type, created=created)

    asyncio.run(
        manager.save_field(
            "tenant-a",
            _field_payload("hostname"),
            "package-import",
        )
    )

    assert ci_type.primary_field == "hostname"
    assert ci_type.save_calls == []


def test_ci_field_list_and_type_detail_mark_only_primary_field(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    fields = [_field("field-1", "hostname"), _field("field-2", "ip_address")]

    async def get_ci_type(tenant, key_or_id):
        assert tenant == "tenant-a"
        assert key_or_id in {"server", "type-1"}
        return ci_type

    async def get_fields(tenant, ci_type_key):
        assert (tenant, ci_type_key) == ("tenant-a", "server")
        return fields

    monkeypatch.setattr(views, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(views, "fields_for_type", get_fields)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    list_request = SimpleNamespace(
        json={"ci_type_key": "server"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    detail_request = SimpleNamespace(
        json={"id": "type-1"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    list_payload = asyncio.run(inspect.unwrap(views.ci_field_list)(list_request))
    detail_payload = asyncio.run(inspect.unwrap(views.ci_type_detail)(detail_request))

    expected_flags = {"hostname": True, "ip_address": False}
    assert {
        item["field_key"]: item["is_primary"] for item in list_payload["items"]
    } == expected_flags
    assert {
        item["field_key"]: item["is_primary"] for item in detail_payload["fields"]
    } == expected_flags


def test_delete_primary_field_is_forbidden_within_current_tenant(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    field = _field("field-1", "hostname")
    filter_calls = []
    type_calls = []

    def field_filter(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(field)

    def ci_type_filter(**kwargs):
        type_calls.append(kwargs)
        return _FakeQuery(ci_type)

    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(field_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
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
    ci_type = _ci_type(primary_field="hostname")
    field = _field("field-2", "ip_address")
    transaction = _FakeTransaction()

    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)

    result = asyncio.run(manager.delete_field("tenant-a", "field-2"))

    assert result == {"id": "field-2"}
    assert field.deleted is True
    assert field.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["deleted", "utime"],
        }
    ]


def test_model_edit_preserves_hidden_legacy_fields_when_omitted(monkeypatch):
    ci_type = _ci_type(
        primary_field="hostname", category="master_data", color="#abcdef"
    )
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(ci_type)),
    )

    asyncio.run(
        manager.save_ci_type(
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

    assert ci_type.name == "服务器模型"
    assert ci_type.category == "master_data"
    assert ci_type.color == "#abcdef"
    assert ci_type.icon == "server"
    assert ci_type.primary_field == "hostname"
    assert ci_type.sort == 0
    assert ci_type.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": [
                "key",
                "name",
                "status",
                "is_locked",
                "updater",
                "utime",
            ]
        }
    ]


def test_new_model_starts_without_implicit_primary_field(monkeypatch):
    created = _ci_type(primary_field="")
    create_kwargs = {}
    transaction = _install_transaction(monkeypatch)

    async def create_ci_type(**kwargs):
        create_kwargs.update(kwargs)
        return created

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "create",
        staticmethod(create_ci_type),
    )

    item = asyncio.run(
        manager.save_ci_type(
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


@pytest.mark.parametrize("sort", [-1, 1.5, True, "not-a-number", []])
def test_model_payload_rejects_invalid_sort(sort):
    with pytest.raises(CustomError) as exc_info:
        manager.payload_ci_type(
            {"key": "server", "name": "服务器", "sort": sort},
            "admin",
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_regular_model_save_cannot_overwrite_drag_order(monkeypatch):
    ci_type = _ci_type(sort=7)
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(ci_type)),
    )

    asyncio.run(
        manager.save_ci_type(
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

    assert ci_type.sort == 7
    assert ci_type.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["key", "name", "status", "is_locked", "updater", "utime"],
        }
    ]


def test_reorder_ci_types_persists_the_complete_order_atomically(monkeypatch):
    server = _ci_type(id="type-1", key="server", sort=1)
    database = _ci_type(id="type-2", key="database", sort=2)
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery([server, database])),
    )

    result = asyncio.run(
        manager.reorder_ci_types(
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


def test_reorder_ci_types_rejects_an_incomplete_model_set(monkeypatch):
    models = [
        _ci_type(id="type-1", key="server", sort=1),
        _ci_type(id="type-2", key="database", sort=2),
    ]
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(models)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.reorder_ci_types(
                "tenant-a",
                ["type-1"],
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert all(model.save_calls == [] for model in models)


def test_ci_type_reorder_endpoint_forwards_the_requested_order(monkeypatch):
    models = [
        _ci_type(id="type-2", key="database", sort=1),
        _ci_type(id="type-1", key="server", sort=2),
    ]
    reorder_calls = []

    async def reorder(tenant, ci_type_ids, operator):
        reorder_calls.append((tenant, ci_type_ids, operator))
        return models

    monkeypatch.setattr(views, "reorder_ci_types", reorder)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"ci_type_ids": ["type-2", "type-1"]},
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-a",
                "account_info": {"username": "admin"},
            }
        ),
    )

    payload = asyncio.run(inspect.unwrap(views.ci_type_reorder)(request))

    assert reorder_calls == [("tenant-a", ["type-2", "type-1"], "admin")]
    assert [item["id"] for item in payload["items"]] == ["type-2", "type-1"]
    assert payload["total"] == 2


def test_ci_type_list_orders_models_by_persisted_sort(monkeypatch):
    operations = []
    models = [
        _ci_type(id="type-2", key="database", sort=1),
        _ci_type(id="type-1", key="server", sort=2),
    ]
    monkeypatch.setattr(
        views.SupplyChainCiType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(models, operations)),
    )
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"page": 1, "size": 100},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    payload = asyncio.run(inspect.unwrap(views.ci_type_list)(request))

    assert [item["id"] for item in payload["items"]] == ["type-2", "type-1"]
    assert ("order_by", ("sort", "key")) in operations


def test_ci_type_sort_migration_backfills_and_indexes_the_new_order():
    migration = importlib.import_module(
        "migrations.models.80_20260719120000_add_ci_type_sort"
    )

    upgrade_sql = asyncio.run(migration.upgrade(None))
    downgrade_sql = asyncio.run(migration.downgrade(None))

    assert 'ADD COLUMN "sort" INT NOT NULL DEFAULT 0' in upgrade_sql
    assert 'PARTITION BY "tenant_id"' in upgrade_sql
    assert 'ORDER BY "deleted", "key", "ctime", "id"' in upgrade_sql
    assert 'idx_supply_chain_ci_type_tenant_sort' in upgrade_sql
    assert 'DROP COLUMN "sort"' in downgrade_sql


def test_model_save_rejects_create_to_update_race(monkeypatch):
    existing = _ci_type(primary_field="hostname")
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(existing)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_ci_type(
                "tenant-a",
                {"key": "server", "name": "服务器"},
                "admin",
                expected_exists=False,
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS
    assert existing.save_calls == []


def test_model_save_maps_deleted_key_tombstone_to_already_exists(monkeypatch):
    tombstone = _ci_type(primary_field="", deleted=True)
    filter_calls = []
    _install_transaction(monkeypatch)

    def filter_ci_type(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(tombstone)

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(filter_ci_type),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_ci_type(
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
    ci_type = _ci_type(primary_field="")
    transaction, operations, create_kwargs = _install_ci_create_fakes(
        monkeypatch,
        ci_type,
    )

    async def validate_attributes(*_args):
        return None

    monkeypatch.setattr(manager, "validate_ci_attributes", validate_attributes)

    asyncio.run(
        manager.save_ci(
            "tenant-a",
            {"ci_type_key": "server", "attributes": {"name": "未勾选名称"}},
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
    ci_type = _ci_type(primary_field="port")
    transaction, _, create_kwargs = _install_ci_create_fakes(
        monkeypatch,
        ci_type,
    )

    async def validate_attributes(*_args):
        return None

    monkeypatch.setattr(manager, "validate_ci_attributes", validate_attributes)

    asyncio.run(
        manager.save_ci(
            "tenant-a",
            {"ci_type_key": "server", "attributes": {"port": 443}},
            "admin",
        )
    )

    assert create_kwargs["name"] == "443"
    assert create_kwargs["using_db"] is transaction.connection


def test_save_ci_update_locks_instance_and_rechecks_soft_delete(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    existing = _FakeRecord(
        _json={},
        id="ci-1",
        ci_type_key="server",
        name="old",
        project_area_id=None,
        accessory_project_id=None,
        attributes={"hostname": "old"},
        status="active",
        risk_status="normal",
    )
    transaction, operations = _install_ci_update_fakes(
        monkeypatch,
        ci_type,
        existing,
    )

    async def validate_attributes(*_args, **_kwargs):
        return None

    monkeypatch.setattr(manager, "validate_ci_attributes", validate_attributes)

    result = asyncio.run(
        manager.save_ci(
            "tenant-a",
            {
                "id": "ci-1",
                "ci_type_key": "server",
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
        ("transaction_exit", None),
    ]


def test_model_package_is_only_path_that_can_restore_primary_field(monkeypatch):
    save_calls = []
    primary_calls = []

    async def save_type(tenant, data, operator, **_kwargs):
        save_calls.append((tenant, data["key"], operator))
        return _ci_type(primary_field="")

    async def save_package_field(*_args, **_kwargs):
        return _field("field-1", "hostname")

    async def set_primary(
        tenant,
        ci_type_key,
        primary_field,
        operator,
        **_kwargs,
    ):
        primary_calls.append((tenant, ci_type_key, primary_field, operator))
        return _ci_type(primary_field=primary_field)

    monkeypatch.setattr(manager, "save_ci_type", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    result = asyncio.run(
        manager.import_model_package(
            "tenant-a",
            {
                "ci_types": [
                    {
                        "key": "server",
                        "name": "服务器",
                        "primary_field": "hostname",
                    }
                ],
                "fields": [
                    {
                        "ci_type_key": "server",
                        "field_key": "hostname",
                        "label": "主机名",
                        "data_type": "text",
                    }
                ],
            },
            "package-import",
        )
    )

    assert result == {"ci_types": 1, "fields": 1, "relation_types": 0}
    assert save_calls == [("tenant-a", "server", "package-import")]
    assert primary_calls == [("tenant-a", "server", "hostname", "package-import")]


def test_imported_export_package_upserts_by_business_key(monkeypatch):
    saved = {"ci_types": [], "fields": [], "relation_types": []}
    expected_flags = {}

    async def save_type(_tenant, data, _operator, **kwargs):
        saved["ci_types"].append(data)
        expected_flags["ci_types"] = kwargs["expected_exists"]
        return _ci_type(primary_field="")

    async def save_package_field(_tenant, data, _operator, **kwargs):
        saved["fields"].append(data)
        expected_flags["fields"] = kwargs["expected_exists"]
        return _field("target-field", data["field_key"])

    async def save_relation_type(_tenant, data, _operator, **kwargs):
        saved["relation_types"].append(data)
        expected_flags["relation_types"] = kwargs["expected_exists"]
        return SimpleNamespace(key=data["key"])

    async def set_primary(*_args, **_kwargs):
        return _ci_type(primary_field="hostname")

    monkeypatch.setattr(manager, "save_ci_type", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "save_relation_type", save_relation_type)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    result = asyncio.run(
        manager.import_model_package(
            "target-tenant",
            {
                "ci_types": [
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
                "fields": [
                    {
                        "id": "source-field",
                        "tenant_id": "source-tenant",
                        "creator": "source-user",
                        "is_primary": True,
                        "ci_type_key": "server",
                        "field_key": "hostname",
                        "label": "主机名",
                        "data_type": "text",
                    }
                ],
                "relation_types": [
                    {
                        "id": "source-relation",
                        "tenant_id": "source-tenant",
                        "key": "depends_on",
                        "name": "依赖",
                    }
                ],
            },
            "package-import",
            expected_existing={
                "ci_types": {"server"},
                "fields": set(),
                "relation_types": {"depends_on"},
            },
        )
    )

    assert result == {"ci_types": 1, "fields": 1, "relation_types": 1}
    assert saved["ci_types"] == [
        {
            "key": "server",
            "name": "服务器",
            "primary_field": "hostname",
        }
    ]
    assert saved["fields"] == [
        {
            "ci_type_key": "server",
            "field_key": "hostname",
            "label": "主机名",
            "data_type": "text",
        }
    ]
    assert saved["relation_types"] == [{"key": "depends_on", "name": "依赖"}]
    assert expected_flags == {
        "ci_types": True,
        "fields": False,
        "relation_types": True,
    }


def test_model_package_switches_primary_before_updating_old_primary(monkeypatch):
    events = []

    async def save_type(_tenant, data, _operator, **_kwargs):
        events.append(("model", data["key"]))
        return _ci_type(primary_field="name")

    async def save_package_field(_tenant, data, _operator, **_kwargs):
        events.append(("field", data["field_key"], data["data_type"]))
        return _field("field-id", data["field_key"], data_type=data["data_type"])

    async def set_primary(
        _tenant,
        ci_type_key,
        primary_field,
        _operator,
        **_kwargs,
    ):
        events.append(("primary", ci_type_key, primary_field))
        return _ci_type(primary_field=primary_field)

    monkeypatch.setattr(manager, "save_ci_type", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "in_transaction", lambda: _FakeTransaction())

    asyncio.run(
        manager.import_model_package(
            "tenant-a",
            {
                "ci_types": [
                    {
                        "key": "server",
                        "name": "服务器",
                        "primary_field": "hostname",
                    }
                ],
                "fields": [
                    {
                        "ci_type_key": "server",
                        "field_key": "name",
                        "data_type": "number",
                    },
                    {
                        "ci_type_key": "server",
                        "field_key": "hostname",
                        "data_type": "text",
                    },
                ],
            },
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
        return _ci_type(key=data["key"], primary_field="")

    async def save_package_field(_tenant, data, _operator, **kwargs):
        connection_calls.append(("field", data["field_key"], kwargs["connection"]))
        return _field("field-2", data["field_key"], data["ci_type_key"])

    async def set_primary(
        _tenant,
        ci_type_key,
        primary_field,
        _operator,
        **kwargs,
    ):
        connection_calls.append(("primary", ci_type_key, kwargs["connection"]))
        return _ci_type(key=ci_type_key, primary_field=primary_field)

    async def save_relation_type(_tenant, data, _operator, **kwargs):
        connection_calls.append(("relation", data["key"], kwargs["connection"]))
        return SimpleNamespace(key=data["key"])

    monkeypatch.setattr(manager.Tenant, "filter", staticmethod(tenant_filter))
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(existing_primary, field_operations)),
    )
    monkeypatch.setattr(manager, "save_ci_type", save_type)
    monkeypatch.setattr(manager, "save_field", save_package_field)
    monkeypatch.setattr(manager, "set_package_primary_field", set_primary)
    monkeypatch.setattr(manager, "save_relation_type", save_relation_type)

    result = asyncio.run(
        manager.import_model_package(
            "tenant-a",
            {
                "ci_types": [
                    {
                        "key": "server",
                        "name": "服务器",
                        "primary_field": "hostname",
                    },
                    {"key": "app", "name": "应用"},
                ],
                "fields": [
                    {
                        "ci_type_key": "app",
                        "field_key": "name",
                        "data_type": "text",
                    }
                ],
                "relation_types": [
                    {"key": "runs_on", "name": "运行于"}
                ],
            },
            "package-import",
        )
    )

    assert result == {"ci_types": 2, "fields": 1, "relation_types": 1}
    assert tenant_filter_calls == [{"id": "tenant-a", "deleted": False}]
    assert all(
        connection is transaction.connection
        for _resource, _key, connection in connection_calls
    )
    assert ("using_db", transaction.connection) in field_operations
    assert operations.count("select_for_update") == 1
    assert operations[0] == "transaction_enter"
    assert operations[-1] == ("transaction_exit", None)


def test_model_package_stages_new_locked_model_until_structure_is_imported(
    monkeypatch,
):
    transaction = _install_transaction(monkeypatch)
    imported_ci_type = _ci_type(is_locked=False)
    saved_payloads = []
    final_filter_calls = []

    async def save_type(_tenant, data, _operator, **kwargs):
        saved_payloads.append((data, kwargs))
        return imported_ci_type

    def filter_type(**kwargs):
        final_filter_calls.append(kwargs)
        return _FakeQuery([imported_ci_type])

    monkeypatch.setattr(manager, "save_ci_type", save_type)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(filter_type),
    )

    result = asyncio.run(
        manager.import_model_package(
            "tenant-a",
            {
                "ci_types": [
                    {
                        "key": "server",
                        "name": "服务器",
                        "is_locked": True,
                    }
                ]
            },
            "package-import",
        )
    )

    assert result == {"ci_types": 1, "fields": 0, "relation_types": 0}
    assert saved_payloads[0][0]["is_locked"] is False
    assert saved_payloads[0][1]["allow_unlock"] is False
    assert saved_payloads[0][1]["allow_sort_update"] is True
    assert saved_payloads[0][1]["connection"] is transaction.connection
    assert final_filter_calls == [
        {
            "tenant_id": "tenant-a",
            "key__in": ["server"],
            "deleted": False,
        }
    ]
    assert imported_ci_type.is_locked is True
    assert imported_ci_type.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["is_locked", "updater", "utime"],
        }
    ]


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
        return _ci_type(primary_field="")

    async def fail_field(*_args, **_kwargs):
        save_calls.append("field")
        raise CustomError(ErrorCode.PARAM_ERROR)

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(manager, "save_ci_type", save_type)
    monkeypatch.setattr(manager, "save_field", fail_field)

    with pytest.raises(CustomError):
        asyncio.run(
            manager.import_model_package(
                "tenant-a",
                {
                    "ci_types": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "hostname",
                        }
                    ],
                    "fields": [
                        {
                            "ci_type_key": "server",
                            "field_key": "hostname",
                            "data_type": "text",
                        }
                    ],
                },
                "package-import",
            )
        )

    assert transaction.entered is True
    assert transaction.exit_type is CustomError
    assert save_calls == ["model", "field"]


def test_package_primary_restore_locks_model_and_field(monkeypatch):
    ci_type = _ci_type(primary_field="")
    field = _field("field-1", "hostname")
    transaction = _FakeTransaction()

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
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

    assert result is ci_type
    assert ci_type.primary_field == "hostname"
    assert ci_type.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["primary_field", "updater", "utime"],
        }
    ]


def test_package_primary_restore_rejects_non_text_field(monkeypatch):
    ci_type = _ci_type(primary_field="")
    field = _field("field-1", "port", data_type="number")

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
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
    assert ci_type.primary_field == ""


def test_model_package_rejects_dangling_primary_field_before_writes(monkeypatch):
    save_calls = []
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )

    async def unexpected_save(*_args, **_kwargs):
        save_calls.append((_args, _kwargs))

    monkeypatch.setattr(manager, "save_ci_type", unexpected_save)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.import_model_package(
                "tenant-a",
                {
                    "ci_types": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "hostname",
                        }
                    ]
                },
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

    monkeypatch.setattr(manager, "save_ci_type", unexpected_save)
    monkeypatch.setattr(manager, "save_field", unexpected_save)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.import_model_package(
                "tenant-a",
                {
                    "ci_types": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "primary_field": "port",
                        }
                    ],
                    "fields": [
                        {
                            "ci_type_key": "server",
                            "field_key": "port",
                            "data_type": "number",
                        }
                    ],
                },
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
        views.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: RowsQuery([SimpleNamespace(key="server")])),
    )
    monkeypatch.setattr(
        views.SupplyChainCiFieldDef,
        "filter",
        staticmethod(
            lambda **_kwargs: RowsQuery(
                [SimpleNamespace(ci_type_key="server", field_key="hostname")]
            )
        ),
    )
    monkeypatch.setattr(
        views.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda **_kwargs: RowsQuery([SimpleNamespace(key="runs_on")])),
    )

    async def check_permission(_request, permission):
        permission_calls.append(permission)
        return True

    monkeypatch.setattr(views, "check_permissions", check_permission)
    normalized, expected_existing = asyncio.run(
        views._require_model_package_permissions(
            SimpleNamespace(),
            "tenant-a",
            {
                "ci_types": [
                    {"id": "source-server", "key": " server "},
                    {"id": "source-app", "key": "app"},
                ],
                "fields": [
                    {
                        "id": "source-hostname",
                        "ci_type_key": " server ",
                        "field_key": " hostname ",
                    },
                    {
                        "id": "source-name",
                        "ci_type_key": "app",
                        "field_key": "name",
                    },
                ],
                "relation_types": [
                    {"id": "source-runs-on", "key": " runs_on "},
                    {"id": "source-depends-on", "key": "depends_on"},
                ],
            },
        )
    )

    assert permission_calls == [
        "supply_chain_review:model:c",
        "supply_chain_review:model:u",
        "supply_chain_review:field:c",
        "supply_chain_review:field:u",
        "supply_chain_review:relation:c",
        "supply_chain_review:relation:u",
    ]
    assert [item["key"] for item in normalized["ci_types"]] == ["server", "app"]
    assert normalized["fields"][0]["ci_type_key"] == "server"
    assert normalized["fields"][0]["field_key"] == "hostname"
    assert normalized["relation_types"][0]["key"] == "runs_on"
    assert all("id" not in item for items in normalized.values() for item in items)
    assert expected_existing == {
        "ci_types": {"server"},
        "fields": {("server", "hostname")},
        "relation_types": {"runs_on"},
    }


def test_model_package_endpoint_passes_permission_snapshot_to_import(monkeypatch):
    normalized = {"ci_types": [{"key": "server"}], "fields": [], "relation_types": []}
    expected_existing = {
        "ci_types": {"server"},
        "fields": set(),
        "relation_types": set(),
    }
    import_calls = []

    async def require_permissions(_request, tenant, package):
        assert tenant == "tenant-a"
        assert package == {"ci_types": [{"key": "server"}]}
        return normalized, expected_existing

    async def import_package(
        tenant,
        package,
        operator,
        expected_existing=None,
    ):
        import_calls.append((tenant, package, operator, expected_existing))
        return {"ci_types": 1, "fields": 0, "relation_types": 0}

    monkeypatch.setattr(
        views,
        "_require_model_package_permissions",
        require_permissions,
    )
    monkeypatch.setattr(views, "import_model_package", import_package)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"package": {"ci_types": [{"key": "server"}]}},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.ci_type_import)(request))

    assert payload == {"ci_types": 1, "fields": 0, "relation_types": 0}
    assert import_calls == [
        ("tenant-a", normalized, None, expected_existing),
    ]


def test_primary_field_migration_clears_only_dangling_references():
    migration = importlib.import_module(
        "migrations.models.72_20260713161000_unset_default_ci_primary_field"
    )
    sql = asyncio.run(migration.upgrade(None))

    assert sql.index("ALTER TABLE") < sql.index('UPDATE "supply_chain_ci_type"')
    assert "COALESCE(ci_type.\"primary_field\", '') <> ''" in sql
    assert 'field_def."tenant_id" IS NOT DISTINCT FROM ci_type."tenant_id"' in sql
    assert 'field_def."ci_type_key" = ci_type."key"' in sql
    assert 'field_def."field_key" = ci_type."primary_field"' in sql
    assert 'field_def."deleted" = False' in sql


def test_field_save_endpoint_requires_update_permission_for_existing_id(monkeypatch):
    field = _field("field-1", "hostname")
    ci_type = _ci_type(primary_field="hostname")
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

    async def get_ci_type(*_args):
        return ci_type

    monkeypatch.setattr(
        views.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_field", save_field)
    monkeypatch.setattr(views, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "id": "field-1",
            "ci_type_key": "server",
            "field_key": "hostname",
            "data_type": "text",
            "is_primary": True,
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.ci_field_save)(request))

    assert permission_calls == [
        (
            True,
            "supply_chain_review:field:c",
            "supply_chain_review:field:u",
        )
    ]
    assert expected_exists_calls == [True]
    assert payload["is_primary"] is True


def test_model_create_endpoint_uses_create_intent_even_when_key_exists(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
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
        return ci_type

    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_ci_type", save_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"key": " server ", "name": "服务器"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.ci_type_save)(request))

    assert permission_calls == [
        (
            False,
            "supply_chain_review:model:c",
            "supply_chain_review:model:u",
        )
    ]
    assert expected_exists_calls == [False]


def test_model_update_endpoint_uses_update_intent(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
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
        return ci_type

    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_ci_type", save_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"id": "type-1", "key": "server", "name": "服务器"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.ci_type_save)(request))

    assert permission_calls == [
        (
            True,
            "supply_chain_review:model:c",
            "supply_chain_review:model:u",
        )
    ]
    assert expected_exists_calls == [True]


def test_field_create_endpoint_uses_create_intent_even_when_key_exists(monkeypatch):
    field = _field("field-1", "hostname")
    ci_type = _ci_type(primary_field="hostname")
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

    async def get_ci_type(*_args):
        return ci_type

    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_field", save_package_field)
    monkeypatch.setattr(views, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={
            "ci_type_key": " server ",
            "field_key": " hostname ",
            "data_type": "text",
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.ci_field_save)(request))

    assert permission_calls == [
        (
            False,
            "supply_chain_review:field:c",
            "supply_chain_review:field:u",
        )
    ]
    assert expected_exists_calls == [False]


def test_locked_model_allows_structure_preserving_unlock(monkeypatch):
    ci_type = _ci_type(is_locked=True)
    transaction = _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(ci_type)),
    )

    result = asyncio.run(
        manager.save_ci_type(
            "tenant-a",
            {
                "id": "type-1",
                "key": "server",
                "name": "服务器",
                "status": "active",
                "is_locked": False,
            },
            "admin",
        )
    )

    assert result is ci_type
    assert ci_type.is_locked is False
    assert ci_type.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["is_locked", "updater", "utime"],
        }
    ]


def test_locked_model_rejects_unlock_combined_with_structure_edit(monkeypatch):
    ci_type = _ci_type(is_locked=True)
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(ci_type)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_ci_type(
                "tenant-a",
                {
                    "id": "type-1",
                    "key": "server",
                    "name": "已修改的服务器模型",
                    "is_locked": False,
                },
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert ci_type.is_locked is True
    assert ci_type.save_calls == []


@pytest.mark.parametrize(
    "field_data",
    [
        _field_payload("hostname"),
        _field_payload("hostname", id="field-1"),
    ],
    ids=["create", "update"],
)
def test_locked_model_rejects_field_create_and_update(monkeypatch, field_data):
    ci_type = _ci_type(is_locked=True)
    existing = _field("field-1", "hostname") if field_data.get("id") else None
    _, _, create_kwargs = _install_field_save_fakes(
        monkeypatch,
        ci_type,
        existing=existing,
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.save_field("tenant-a", field_data, "admin"))

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert create_kwargs == {}
    if existing:
        assert existing.save_calls == []


def test_locked_model_rejects_field_delete_and_primary_change(monkeypatch):
    ci_type = _ci_type(primary_field="hostname", is_locked=True)
    field = _field("field-2", "ip_address")

    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(manager, "in_transaction", _FakeTransaction)

    with pytest.raises(CustomError) as delete_exc:
        asyncio.run(manager.delete_field("tenant-a", "field-2"))
    with pytest.raises(CustomError) as primary_exc:
        asyncio.run(
            manager.set_package_primary_field(
                "tenant-a",
                "server",
                "ip_address",
                "package-import",
            )
        )

    assert delete_exc.value.enum is ErrorCode.FORBIDDEN
    assert primary_exc.value.enum is ErrorCode.FORBIDDEN
    assert field.save_calls == []
    assert ci_type.save_calls == []


def test_locked_reference_model_rejects_new_ref_field(monkeypatch):
    owner_ci_type = _ci_type(key="application")
    locked_ref_ci_type = _ci_type(key="server", is_locked=True)
    create_calls = []

    def ci_type_filter(**kwargs):
        if kwargs.get("key") == "application":
            return _FakeQuery(owner_ci_type)
        if kwargs.get("key") == "server":
            return _FakeQuery(locked_ref_ci_type)
        if kwargs.get("key__in") == ["server"]:
            return _FakeQuery([locked_ref_ci_type])
        return _FakeQuery()

    async def create_field(**kwargs):
        create_calls.append(kwargs)

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery()),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "create",
        staticmethod(create_field),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload(
                    "server_ref",
                    ci_type_key="application",
                    data_type="ref",
                    ref_ci_type="server",
                ),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert create_calls == []


def test_locked_old_reference_model_rejects_ref_field_redirect(monkeypatch):
    owner_ci_type = _ci_type(key="application")
    locked_old_ref = _ci_type(key="server", is_locked=True)
    new_ref = _ci_type(key="database")
    existing = _field(
        "field-1",
        "target_ref",
        "application",
        data_type="ref",
        ref_ci_type="server",
    )

    def ci_type_filter(**kwargs):
        if kwargs.get("key") == "application":
            return _FakeQuery(owner_ci_type)
        if kwargs.get("key") == "database":
            return _FakeQuery(new_ref)
        if kwargs.get("key__in") == ["database", "server"]:
            return _FakeQuery([new_ref, locked_old_ref])
        return _FakeQuery()

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(existing)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_field(
                "tenant-a",
                _field_payload(
                    "target_ref",
                    id="field-1",
                    ci_type_key="application",
                    data_type="ref",
                    ref_ci_type="database",
                ),
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert existing.save_calls == []


def test_locked_reference_model_rejects_ref_field_delete(monkeypatch):
    owner_ci_type = _ci_type(key="application")
    locked_ref_ci_type = _ci_type(key="server", is_locked=True)
    field = _field(
        "field-1",
        "server_ref",
        "application",
        data_type="ref",
        ref_ci_type="server",
    )

    def ci_type_filter(**kwargs):
        if kwargs.get("key") == "application":
            return _FakeQuery(owner_ci_type)
        if kwargs.get("key__in") == ["server"]:
            return _FakeQuery([locked_ref_ci_type])
        return _FakeQuery()

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(field)),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_field("tenant-a", "field-1"))

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert field.save_calls == []


def test_model_package_cannot_unlock_existing_locked_model(monkeypatch):
    ci_type = _ci_type(is_locked=True)

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(manager, "in_transaction", _FakeTransaction)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.import_model_package(
                "tenant-a",
                {
                    "ci_types": [
                        {
                            "key": "server",
                            "name": "服务器",
                            "is_locked": False,
                        }
                    ]
                },
                "package-import",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert ci_type.is_locked is True
    assert ci_type.save_calls == []


def test_relation_type_update_cannot_move_away_from_locked_model(monkeypatch):
    relation_type = _FakeRecord(
        _json={},
        id="relation-type-1",
        key="depends_on",
        source_ci_types=["server"],
        target_ci_types=["database"],
        relation_fields=[],
        is_locked=False,
    )

    def ci_type_filter(**kwargs):
        return _FakeQuery(
            [
                _ci_type(key=key, is_locked=key == "server")
                for key in kwargs.get("key__in", [])
            ]
        )

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(relation_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_relation_type(
                "tenant-a",
                {
                    "id": "relation-type-1",
                    "key": "depends_on",
                    "name": "依赖",
                    "source_ci_types": ["workstation"],
                    "target_ci_types": ["database"],
                },
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert relation_type.save_calls == []


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_unrestricted_relation_type_save_checks_every_locked_model(
    monkeypatch,
    existing,
):
    relation_type = (
        _FakeRecord(
            _json={},
            id="relation-type-1",
            key="depends_on",
            source_ci_types=[],
            target_ci_types=["database"],
            relation_fields=[],
            is_locked=False,
        )
        if existing
        else None
    )
    lock_queries = []

    def ci_type_filter(**kwargs):
        lock_queries.append(kwargs)
        return _FakeQuery(
            [
                _ci_type(key="database"),
                _ci_type(key="server", is_locked=True),
            ]
        )

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(relation_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )

    data = {
        "key": "depends_on",
        "name": "依赖",
        "source_ci_types": [],
        "target_ci_types": ["database"],
    }
    if existing:
        data["id"] = "relation-type-1"

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_relation_type("tenant-a", data, "admin")
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert lock_queries[-1:] == [
        {
            "tenant_id": "tenant-a",
            "deleted": False,
        }
    ]
    if relation_type:
        assert relation_type.save_calls == []


def test_unrestricted_relation_type_delete_checks_every_locked_model(monkeypatch):
    relation_type = _FakeRecord(
        _json={},
        id="relation-type-1",
        key="depends_on",
        source_ci_types=["server"],
        target_ci_types=[],
        relation_fields=[],
        is_locked=False,
    )
    lock_queries = []

    def ci_type_filter(**kwargs):
        lock_queries.append(kwargs)
        return _FakeQuery(
            [
                _ci_type(key="server"),
                _ci_type(key="database", is_locked=True),
            ]
        )

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(relation_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_relation_type("tenant-a", "relation-type-1"))

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert lock_queries == [
        {
            "tenant_id": "tenant-a",
            "deleted": False,
        }
    ]
    assert relation_type.save_calls == []


def _install_model_delete_fakes(
    monkeypatch,
    ci_type,
    *,
    cis=None,
    relation_types=None,
    referencing_fields=None,
):
    field_updates = []
    transaction = _install_transaction(monkeypatch)

    class FieldUpdateQuery:
        def __init__(self):
            self.connection = None

        def using_db(self, connection):
            self.connection = connection
            return self

        async def update(self, **kwargs):
            field_updates.append((kwargs, self.connection))

        async def delete(self):
            field_updates.append(("delete", self.connection))

    def field_filter(**kwargs):
        if "ref_ci_type" in kwargs:
            return _FakeQuery(referencing_fields or [])
        return FieldUpdateQuery()

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(cis or [])),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(relation_types or [])),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(field_filter),
    )
    return transaction, field_updates


def test_model_delete_rejects_locked_model(monkeypatch):
    ci_type = _ci_type(key="application", is_locked=True)
    _install_model_delete_fakes(monkeypatch, ci_type)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_ci_type("tenant-a", "application"))

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert ci_type.save_calls == []


def test_model_delete_rejects_active_ci(monkeypatch):
    ci_type = _ci_type(key="application")
    active_ci = _FakeRecord(
        _json={},
        id="ci-1",
        ci_type_key="application",
        deleted=False,
    )
    _install_model_delete_fakes(monkeypatch, ci_type, cis=[active_ci])

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_ci_type("tenant-a", "application"))

    assert exc_info.value.enum is ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES
    assert ci_type.save_calls == []


@pytest.mark.parametrize(
    ("source_ci_types", "target_ci_types", "relation_fields"),
    [
        (["application"], [], []),
        ([], ["application"], []),
        (
            [],
            [],
            [
                {
                    "field_key": "application_ref",
                    "data_type": "ref",
                    "ref_ci_type": "application",
                }
            ],
        ),
    ],
    ids=["source", "target", "relation-field"],
)
def test_model_delete_rejects_explicit_relation_type_dependency(
    monkeypatch,
    source_ci_types,
    target_ci_types,
    relation_fields,
):
    ci_type = _ci_type(key="application")
    relation_type = _FakeRecord(
        _json={},
        id="relation-type-1",
        key="contains",
        source_ci_types=source_ci_types,
        target_ci_types=target_ci_types,
        relation_fields=relation_fields,
        is_locked=False,
    )
    _install_model_delete_fakes(
        monkeypatch,
        ci_type,
        relation_types=[relation_type],
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_ci_type("tenant-a", "application"))

    assert exc_info.value.enum is ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES
    assert ci_type.save_calls == []


def test_model_delete_rejects_external_inbound_ref(monkeypatch):
    ci_type = _ci_type(key="application")
    inbound_ref = _field(
        "field-1",
        "application_ref",
        "server",
        data_type="ref",
        ref_ci_type="application",
    )
    _install_model_delete_fakes(
        monkeypatch,
        ci_type,
        referencing_fields=[inbound_ref],
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_ci_type("tenant-a", "application"))

    assert exc_info.value.enum is ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES
    assert ci_type.save_calls == []


def test_model_delete_allows_self_ref_and_unrestricted_relation(monkeypatch):
    ci_type = _ci_type(key="application")
    self_ref = _field(
        "field-1",
        "parent",
        "application",
        data_type="ref",
        ref_ci_type="application",
    )
    unrestricted_relation = _FakeRecord(
        _json={},
        id="relation-type-1",
        key="contains",
        source_ci_types=["server"],
        target_ci_types=[],
        relation_fields=[],
        is_locked=False,
    )
    transaction, field_updates = _install_model_delete_fakes(
        monkeypatch,
        ci_type,
        relation_types=[unrestricted_relation],
        referencing_fields=[self_ref],
    )

    result = asyncio.run(manager.delete_ci_type("tenant-a", "application"))

    assert result == {"id": "type-1"}
    assert ci_type.save_calls == []
    assert ci_type.delete_calls == [{"using_db": transaction.connection}]
    assert field_updates == [
        ("delete", transaction.connection)
    ]


def test_locked_model_rejects_relation_type_and_relation_deletes(monkeypatch):
    relation_type = _FakeRecord(
        _json={},
        id="relation-type-1",
        key="depends_on",
        source_ci_types=["server"],
        target_ci_types=["database"],
        relation_fields=[],
        is_locked=False,
    )
    relation = _FakeRecord(
        _json={},
        id="relation-1",
        source_ci_id="source-1",
        target_ci_id="target-1",
    )
    source = _FakeRecord(_json={}, id="source-1", ci_type_key="server")
    target = _FakeRecord(_json={}, id="target-1", ci_type_key="database")

    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _FakeQuery(relation_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(relation)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery([source, target])),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(
            lambda **kwargs: _FakeQuery(
                [
                    _ci_type(key=key, is_locked=key == "server")
                    for key in kwargs.get("key__in", [])
                ]
            )
        ),
    )

    with pytest.raises(CustomError) as type_exc:
        asyncio.run(manager.delete_relation_type("tenant-a", "relation-type-1"))
    with pytest.raises(CustomError) as relation_exc:
        asyncio.run(manager.delete_relation("tenant-a", "relation-1"))

    assert type_exc.value.enum is ErrorCode.FORBIDDEN
    assert relation_exc.value.enum is ErrorCode.FORBIDDEN
    assert relation_type.save_calls == []
    assert relation.save_calls == []


def test_relation_update_checks_persisted_old_endpoints(monkeypatch):
    relation_type = SimpleNamespace(
        key="depends_on",
        source_ci_types=[],
        target_ci_types=[],
        relation_fields=[],
        cardinality="n-n",
    )
    relation = _FakeRecord(
        _json={},
        id="relation-1",
        relation_type_key="depends_on",
        source_ci_id="old-source",
        target_ci_id="old-target",
    )
    cis = {
        "old-source": _FakeRecord(
            _json={}, id="old-source", ci_type_key="server"
        ),
        "old-target": _FakeRecord(
            _json={}, id="old-target", ci_type_key="database"
        ),
        "new-source": _FakeRecord(
            _json={}, id="new-source", ci_type_key="workstation"
        ),
        "new-target": _FakeRecord(
            _json={}, id="new-target", ci_type_key="database"
        ),
    }

    async def no_validation(*_args, **_kwargs):
        return None

    def relation_filter(**kwargs):
        return _FakeQuery(relation if kwargs.get("id") == "relation-1" else None)

    def ci_type_filter(**kwargs):
        return _FakeQuery(
            [
                _ci_type(key=key, is_locked=key == "server")
                for key in kwargs.get("key__in", [])
            ]
        )

    _install_transaction(monkeypatch)
    monkeypatch.setattr(manager, "validate_dynamic_attributes", no_validation)
    monkeypatch.setattr(manager, "validate_relation_ends", no_validation)
    monkeypatch.setattr(
        manager.SupplyChainCiRelationType,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(relation_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(relation_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(
            lambda **kwargs: _FakeQuery(
                [cis[ci_id] for ci_id in kwargs.get("id__in", [])]
            )
        ),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_relation(
                "tenant-a",
                {
                    "id": "relation-1",
                    "relation_type_key": "depends_on",
                    "source_ci_id": "new-source",
                    "target_ci_id": "new-target",
                },
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert relation.save_calls == []


def test_structure_model_locks_use_stable_key_order_and_transaction_connection(
    monkeypatch,
):
    operations = []
    filter_calls = []
    connection = object()
    ci_types = [
        _ci_type(key="database"),
        _ci_type(key="server"),
    ]

    def ci_type_filter(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(ci_types, operations)

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )

    result = asyncio.run(
        manager.lock_ci_type_keys_for_structure(
            "tenant-a",
            {"server", "database"},
            connection,
        )
    )

    assert result == ci_types
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
    created = _ci_type(primary_field="")

    def tenant_filter(**kwargs):
        operations.append(("tenant_filter", kwargs))
        return _FakeQuery(
            SimpleNamespace(id="tenant-a", deleted=False),
            operations,
        )

    def ci_type_filter(**kwargs):
        operations.append(("ci_type_filter", kwargs))
        return _FakeQuery(None, operations)

    async def create_ci_type(**kwargs):
        operations.append(("create", kwargs["using_db"]))
        return created

    monkeypatch.setattr(manager, "in_transaction", lambda: transaction)
    monkeypatch.setattr(manager.Tenant, "filter", staticmethod(tenant_filter))
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "create",
        staticmethod(create_ci_type),
    )

    result = asyncio.run(
        manager.save_ci_type(
            "tenant-a",
            {"key": "server", "name": "服务器"},
            "admin",
        )
    )

    assert result is created
    assert operations.index("select_for_update") < next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, tuple) and operation[0] == "ci_type_filter"
    )
    assert operations[-2] == ("create", transaction.connection)
    assert operations[-1] == ("transaction_exit", None)


def test_save_ci_type_rechecks_locked_state_after_waiting_for_row_lock(monkeypatch):
    operations = []
    ci_type = _ci_type(is_locked=False)
    transaction = _install_transaction(monkeypatch, operations)

    class ConcurrentLockQuery(_FakeQuery):
        def select_for_update(self):
            ci_type.is_locked = True
            return super().select_for_update()

    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(
            lambda *_args, **_kwargs: ConcurrentLockQuery(ci_type, operations)
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.save_ci_type(
                "tenant-a",
                {
                    "id": "type-1",
                    "key": "server",
                    "name": "并发修改",
                },
                "admin",
            )
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert ci_type.save_calls == []
    assert operations == [
        "transaction_enter",
        ("using_db", transaction.connection),
        ("order_by", ("key",)),
        "select_for_update",
        "first",
        ("transaction_exit", CustomError),
    ]


@pytest.mark.parametrize(
    "model_rows",
    [
        [
            _ci_type(key="database", deleted=True),
            _ci_type(key="server", deleted=True),
        ],
        [],
    ],
    ids=["soft-deleted-models", "missing-models"],
)
def test_delete_relation_cleans_soft_deleted_endpoints_without_active_models(
    monkeypatch,
    model_rows,
):
    relation = _FakeRecord(
        _json={},
        id="relation-1",
        source_ci_id="source-1",
        target_ci_id="target-1",
    )
    endpoints = [
        _FakeRecord(
            _json={},
            id="source-1",
            tenant_id="tenant-a",
            ci_type_key="server",
            deleted=True,
        ),
        _FakeRecord(
            _json={},
            id="target-1",
            tenant_id="tenant-a",
            ci_type_key="database",
            deleted=True,
        ),
    ]
    ci_filter_calls = []
    ci_type_filter_calls = []
    transaction = _install_transaction(monkeypatch)

    def ci_filter(**kwargs):
        ci_filter_calls.append(kwargs)
        return _FakeQuery(endpoints)

    def ci_type_filter(**kwargs):
        ci_type_filter_calls.append(kwargs)
        return _FakeQuery(model_rows)

    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(relation)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(ci_filter),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(ci_type_filter),
    )

    result = asyncio.run(manager.delete_relation("tenant-a", "relation-1"))

    assert result == {"id": "relation-1"}
    assert ci_filter_calls == [
        {
            "id__in": ["source-1", "target-1"],
            "tenant_id": "tenant-a",
        }
    ]
    assert ci_type_filter_calls == [
        {
            "tenant_id": "tenant-a",
            "key__in": ["database", "server"],
        }
    ]
    assert relation.deleted is True
    assert relation.save_calls == [
        {
            "using_db": transaction.connection,
            "update_fields": ["deleted", "utime"],
        }
    ]


def test_delete_relation_rejects_missing_or_cross_tenant_endpoint(monkeypatch):
    relation = _FakeRecord(
        _json={},
        id="relation-1",
        source_ci_id="source-1",
        target_ci_id="target-1",
    )
    source = _FakeRecord(
        _json={},
        id="source-1",
        tenant_id="tenant-a",
        ci_type_key="server",
        deleted=True,
    )
    ci_filter_calls = []
    _install_transaction(monkeypatch)

    def ci_filter(**kwargs):
        ci_filter_calls.append(kwargs)
        return _FakeQuery([source])

    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(relation)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(ci_filter),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_relation("tenant-a", "relation-1"))

    assert exc_info.value.enum is ErrorCode.NOT_FOUND
    assert ci_filter_calls == [
        {
            "id__in": ["source-1", "target-1"],
            "tenant_id": "tenant-a",
        }
    ]
    assert relation.save_calls == []


def test_delete_relation_rejects_locked_soft_deleted_model(monkeypatch):
    relation = _FakeRecord(
        _json={},
        id="relation-1",
        source_ci_id="source-1",
        target_ci_id="target-1",
    )
    endpoints = [
        _FakeRecord(_json={}, id="source-1", ci_type_key="server"),
        _FakeRecord(_json={}, id="target-1", ci_type_key="database"),
    ]
    _install_transaction(monkeypatch)
    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(relation)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(endpoints)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(
            lambda **_kwargs: _FakeQuery(
                [_ci_type(key="server", deleted=True, is_locked=True)]
            )
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_relation("tenant-a", "relation-1"))

    assert exc_info.value.enum is ErrorCode.FORBIDDEN
    assert relation.save_calls == []


@pytest.mark.parametrize(
    "operation",
    [
        manager.save_ci_type,
        manager.save_field,
        manager.delete_field,
        manager.set_package_primary_field,
        manager.save_relation_type,
        manager.save_relation,
        manager.delete_ci,
        manager.delete_ci_type,
        manager.delete_relation_type,
        manager.delete_relation,
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
    ci_type = _ci_type(is_locked=True)
    ci = _FakeRecord(
        _json={},
        id="ci-1",
        tenant_id="tenant-a",
        ci_type_key="server",
        name="旧名称",
        project_area_id=None,
        accessory_project_id=None,
        attributes={"hostname": "old"},
        status="active",
        risk_status="normal",
    )
    relation_updates = []
    transaction = _install_transaction(monkeypatch)

    async def no_validation(*_args, **_kwargs):
        return None

    async def get_ci_type(*_args):
        return ci_type

    class RelationQuery:
        def __init__(self):
            self.connection = None

        def using_db(self, connection):
            self.connection = connection
            return self

        async def update(self, **kwargs):
            relation_updates.append((kwargs, self.connection))

    monkeypatch.setattr(manager, "validate_ci_attributes", no_validation)
    monkeypatch.setattr(manager, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(lambda **_kwargs: _FakeQuery(ci)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(lambda *_args, **_kwargs: RelationQuery()),
    )

    result = asyncio.run(
        manager.save_ci(
            "tenant-a",
            {
                "id": "ci-1",
                "ci_type_key": "server",
                "attributes": {"hostname": "new"},
            },
            "admin",
        )
    )
    delete_request = SimpleNamespace(
        json={"id": "ci-1"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    delete_result = asyncio.run(inspect.unwrap(views.ci_delete)(delete_request))

    assert result is ci
    assert ci.attributes == {"hostname": "new"}
    assert ci.deleted is True
    assert len(ci.save_calls) == 2
    assert ci.save_calls[-1] == {
        "using_db": transaction.connection,
        "update_fields": ["deleted", "utime"],
    }
    assert delete_result == {"id": "ci-1"}
    assert relation_updates == [
        ({"deleted": True}, transaction.connection)
    ]
