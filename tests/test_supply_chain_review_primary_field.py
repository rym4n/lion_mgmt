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
    def __init__(self, row=None):
        self.row = row
        self.connection = None
        self.locked = False

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        self.locked = True
        return self

    async def first(self):
        return self.row

    async def exists(self):
        return self.row is not None


class _FakeTransaction:
    def __init__(self):
        self.connection = object()

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeRecord(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.save_calls = []

    @property
    def json(self):
        return dict(self._json)

    async def save(self, **kwargs):
        self.save_calls.append(kwargs)


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
        "searchable": False,
        "listable": True,
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
    assert filter_calls == [
        {"id": "field-2", "tenant_id": "tenant-a", "deleted": False}
    ]
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


def test_renaming_current_primary_field_updates_model_reference(monkeypatch):
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

    assert existing.field_key == "display_name"
    assert ci_type.primary_field == "display_name"


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

    async def get_ci_type(tenant, key_or_id):
        assert (tenant, key_or_id) == ("tenant-a", "type-1")
        return ci_type

    monkeypatch.setattr(manager, "get_ci_type_or_404", get_ci_type)

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
    assert ci_type.save_calls == [
        {
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


def test_model_save_rejects_create_to_update_race(monkeypatch):
    existing = _ci_type(primary_field="hostname")
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


def test_instance_name_does_not_fall_back_to_unselected_name_attribute(monkeypatch):
    ci_type = _ci_type(primary_field="")
    create_kwargs = {}

    async def get_ci_type(tenant, key):
        assert (tenant, key) == ("tenant-a", "server")
        return ci_type

    async def validate_attributes(*_args):
        return None

    async def create_ci(**kwargs):
        create_kwargs.update(kwargs)
        return _FakeRecord(_json=kwargs, id="ci-1", **kwargs)

    monkeypatch.setattr(manager, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(manager, "validate_ci_attributes", validate_attributes)
    monkeypatch.setattr(manager.SupplyChainCi, "create", staticmethod(create_ci))

    asyncio.run(
        manager.save_ci(
            "tenant-a",
            {"ci_type_key": "server", "attributes": {"name": "未勾选名称"}},
            "admin",
        )
    )

    assert create_kwargs["name"] == "server"


def test_legacy_numeric_primary_value_is_converted_to_display_text(monkeypatch):
    ci_type = _ci_type(primary_field="port")
    create_kwargs = {}

    async def get_ci_type(*_args):
        return ci_type

    async def validate_attributes(*_args):
        return None

    async def create_ci(**kwargs):
        create_kwargs.update(kwargs)
        return _FakeRecord(_json=kwargs, id="ci-1", **kwargs)

    monkeypatch.setattr(manager, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(manager, "validate_ci_attributes", validate_attributes)
    monkeypatch.setattr(manager.SupplyChainCi, "create", staticmethod(create_ci))

    asyncio.run(
        manager.save_ci(
            "tenant-a",
            {"ci_type_key": "server", "attributes": {"port": 443}},
            "admin",
        )
    )

    assert create_kwargs["name"] == "443"


def test_model_package_is_only_path_that_can_restore_primary_field(monkeypatch):
    save_calls = []
    primary_calls = []

    async def save_type(tenant, data, operator, **_kwargs):
        save_calls.append((tenant, data["key"], operator))
        return _ci_type(primary_field="")

    async def save_package_field(*_args, **_kwargs):
        return _field("field-1", "hostname")

    async def set_primary(tenant, ci_type_key, primary_field, operator):
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

    async def set_primary(*_args):
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

    async def set_primary(_tenant, ci_type_key, primary_field, _operator):
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


def test_model_save_permission_uses_normalized_business_key(monkeypatch):
    ci_type = _ci_type(primary_field="hostname")
    permission_calls = []
    filter_calls = []
    expected_exists_calls = []

    def filter_type(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(ci_type)

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

    monkeypatch.setattr(views.SupplyChainCiType, "filter", staticmethod(filter_type))
    monkeypatch.setattr(views, "_require_save_permission", require_permission)
    monkeypatch.setattr(views, "save_ci_type", save_type)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"key": " server ", "name": "服务器"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    asyncio.run(inspect.unwrap(views.ci_type_save)(request))

    assert filter_calls == [
        {"key": "server", "tenant_id": "tenant-a", "deleted": False}
    ]
    assert permission_calls == [
        (
            True,
            "supply_chain_review:model:c",
            "supply_chain_review:model:u",
        )
    ]
    assert expected_exists_calls == [True]


def test_field_save_permission_uses_normalized_business_keys(monkeypatch):
    field = _field("field-1", "hostname")
    ci_type = _ci_type(primary_field="hostname")
    permission_calls = []
    filter_calls = []
    expected_exists_calls = []

    def filter_field(**kwargs):
        filter_calls.append(kwargs)
        return _FakeQuery(field)

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

    monkeypatch.setattr(
        views.SupplyChainCiFieldDef,
        "filter",
        staticmethod(filter_field),
    )
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

    assert filter_calls == [
        {
            "tenant_id": "tenant-a",
            "ci_type_key": "server",
            "field_key": "hostname",
            "deleted": False,
        }
    ]
    assert permission_calls == [
        (
            True,
            "supply_chain_review:field:c",
            "supply_chain_review:field:u",
        )
    ]
    assert expected_exists_calls == [True]
