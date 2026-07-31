# coding=utf-8
import asyncio
import importlib
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models import asset_management as asset_models
from models import network_access_control as network_models
from services.asset_management import manager, views
from tortoise.models import Model


class _Query:
    def __init__(self, rows=None, *, on_delete=None):
        if rows is None:
            rows = []
        self.rows = rows if isinstance(rows, list) else [rows]
        self.on_delete = on_delete

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self

    def order_by(self, *_fields):
        return self

    async def first(self):
        return self.rows[0] if self.rows else None

    async def all(self):
        return list(self.rows)

    async def exists(self):
        return bool(self.rows)

    async def delete(self):
        if self.on_delete:
            self.on_delete()
        return len(self.rows)


class _LockingQuery(_Query):
    def __init__(self, rows=None):
        super().__init__(rows)
        self.connection = None
        self.locked = False
        self.ordering = ()

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        self.locked = True
        return self

    def order_by(self, *fields):
        self.ordering = fields
        return self


def _ref_field(
    field_key,
    *,
    target_model="employee-model",
    multiple=False,
    reverse_unique=False,
):
    return SimpleNamespace(
        field_key=field_key,
        label=field_key,
        data_type="ref",
        referenced_asset_model_id=target_model,
        required=False,
        unique=False,
        default_value=None,
        options={
            "multiple": multiple,
            "reverse_unique": reverse_unique,
        },
    )


def test_reverse_unique_rejects_a_target_used_by_another_source(monkeypatch):
    field = _ref_field("manager", reverse_unique=True)

    async def fields_for_model(*_args):
        return [field]

    def asset_filter(**kwargs):
        if kwargs.get("id") == "employee-1":
            return _Query(SimpleNamespace(id="employee-1"))
        return _Query(
            SimpleNamespace(
                id="project-1",
                attributes={"manager": "employee-1"},
            )
        )

    monkeypatch.setattr(manager, "fields_for_model", fields_for_model)
    monkeypatch.setattr(manager.AssetInstance, "filter", staticmethod(asset_filter))
    monkeypatch.setattr(
        manager.AssetUniqueRule,
        "filter",
        staticmethod(lambda **_kwargs: _Query()),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.validate_asset_attributes(
                "tenant-a",
                "project-model",
                {"manager": "employee-1"},
                current_asset_id="project-2",
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS


def test_reference_validation_locks_targets_inside_the_write_transaction(monkeypatch):
    target_query = _LockingQuery(SimpleNamespace(id="employee-1"))
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(lambda **_kwargs: target_query),
    )

    asyncio.run(
        manager.validate_field_value(
            "tenant-a",
            _ref_field("manager"),
            "employee-1",
            "connection",
        )
    )

    assert target_query.connection == "connection"
    assert target_query.locked is True


def test_enabling_reverse_unique_rejects_existing_duplicate_targets(monkeypatch):
    source_query = _LockingQuery(
        [
            SimpleNamespace(id="project-1", attributes={"manager": "employee-1"}),
            SimpleNamespace(id="project-2", attributes={"manager": "employee-1"}),
        ]
    )
    target_queries = []

    def asset_filter(**kwargs):
        if kwargs.get("asset_model_id") == "project-model":
            return source_query
        query = _LockingQuery(SimpleNamespace(id=kwargs.get("id")))
        target_queries.append(query)
        return query

    monkeypatch.setattr(manager.AssetInstance, "filter", staticmethod(asset_filter))
    field = {
        "field_key": "manager",
        "data_type": "ref",
        "referenced_asset_model_id": "employee-model",
        "required": False,
        "unique": False,
        "options": {"multiple": False, "reverse_unique": True},
    }

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.validate_reference_field_instances(
                "tenant-a",
                "project-model",
                field,
                "connection",
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS
    assert source_query.connection == "connection"
    assert source_query.locked is True
    assert source_query.ordering == ("id",)
    assert target_queries and all(query.locked for query in target_queries)


@pytest.mark.parametrize("option", ["multiple", "reverse_unique"])
def test_reference_options_require_json_booleans(option):
    with pytest.raises(CustomError) as exc_info:
        manager.payload_dynamic_field(
            {
                "field_key": "manager",
                "label": "负责人",
                "data_type": "ref",
                "referenced_asset_model_id": "employee-model",
                "options": {option: "false"},
            }
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_sync_asset_references_replaces_scalar_and_multiple_index_rows(monkeypatch):
    deleted = []
    created = []

    class ReferenceRecord(SimpleNamespace):
        @staticmethod
        def filter(**kwargs):
            return _Query(on_delete=lambda: deleted.append(kwargs))

        @staticmethod
        async def bulk_create(rows, **kwargs):
            created.extend(rows)
            assert kwargs["using_db"] == "connection"

    monkeypatch.setattr(manager, "AssetReference", ReferenceRecord)
    asset = SimpleNamespace(
        id="project-1",
        attributes={
            "manager": "employee-1",
            "members": ["employee-2", "employee-3"],
        },
    )

    asyncio.run(
        manager.sync_asset_references(
            "tenant-a",
            asset,
            [
                _ref_field("manager"),
                _ref_field("members", multiple=True),
            ],
            "connection",
        )
    )

    assert deleted == [{"tenant_id": "tenant-a", "source_asset_id": "project-1"}]
    assert [(row.field_key, row.target_asset_id, row.position) for row in created] == [
        ("manager", "employee-1", 0),
        ("members", "employee-2", 0),
        ("members", "employee-3", 1),
    ]


def test_reference_labels_preserve_scalar_and_multiple_shapes(monkeypatch):
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(
            lambda **_kwargs: _Query(
                [
                    SimpleNamespace(id="employee-1", name="张三"),
                    SimpleNamespace(id="employee-2", name="李四"),
                ]
            )
        ),
    )
    asset = SimpleNamespace(
        id="project-1",
        asset_model_id="project-model",
        attributes={
            "manager": "employee-1",
            "members": ["employee-1", "employee-2"],
        },
    )

    labels = asyncio.run(
        manager.reference_labels_for_asset(
            "tenant-a",
            asset,
            [
                _ref_field("manager"),
                _ref_field("members", multiple=True),
            ],
        )
    )

    assert labels == {"manager": "张三", "members": ["张三", "李四"]}


def test_reference_labels_for_page_batch_resolve_shapes_and_missing_ids(monkeypatch):
    queries = []

    def asset_filter(**kwargs):
        queries.append(kwargs)
        return _Query(
            [
                SimpleNamespace(id="employee-1", name="张三"),
                SimpleNamespace(id="employee-2", name="李四"),
            ]
        )

    monkeypatch.setattr(manager.AssetInstance, "filter", staticmethod(asset_filter))
    labels = asyncio.run(
        manager.reference_labels_for_assets(
            "tenant-a",
            [
                SimpleNamespace(
                    id="project-1",
                    asset_model_id="project-model",
                    attributes={
                        "manager": "employee-1",
                        "members": ["employee-1", "employee-2"],
                    },
                ),
                SimpleNamespace(
                    id="project-2",
                    asset_model_id="project-model",
                    attributes={"manager": "missing-employee", "members": []},
                ),
            ],
            [
                _ref_field("manager"),
                _ref_field("members", multiple=True),
            ],
        )
    )

    assert len(queries) == 1
    assert queries[0]["id__in"] == [
        "employee-1",
        "employee-2",
        "missing-employee",
    ]
    assert labels == {
        "project-1": {"manager": "张三", "members": ["张三", "李四"]},
        "project-2": {"manager": "missing-employee"},
    }


def test_reference_graph_uses_field_label_for_edges(monkeypatch):
    root = SimpleNamespace(
        id="project-1",
        name="项目A",
        asset_model_id="project-model",
        asset_model_key="project",
        json={"id": "project-1"},
    )
    target = SimpleNamespace(
        id="employee-1",
        name="张三",
        asset_model_id="employee-model",
        asset_model_key="user",
        json={"id": "employee-1"},
    )
    reference = SimpleNamespace(
        id="reference-1",
        source_asset_id=root.id,
        target_asset_id=target.id,
        field_key="manager",
        json={"field_key": "manager"},
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _Query(reference)),
    )
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(lambda **_kwargs: _Query([root, target])),
    )
    monkeypatch.setattr(
        manager.AssetFieldDefinition,
        "filter",
        staticmethod(
            lambda *_args, **_kwargs: _Query(
                SimpleNamespace(
                    asset_model_id="project-model",
                    asset_model_key="project",
                    field_key="manager",
                    label="负责人",
                )
            )
        ),
    )

    graph = asyncio.run(
        manager.build_reference_graph("tenant-a", root_asset_id=root.id, depth=1)
    )

    assert {node["id"] for node in graph["nodes"]} == {root.id, target.id}
    assert graph["edges"] == [
        {
            "id": "reference-1",
            "sourceNodeId": root.id,
            "targetNodeId": target.id,
            "text": "负责人",
            "properties": {"field_key": "manager"},
        }
    ]


def test_delete_asset_instance_rejects_incoming_references(monkeypatch):
    asset = SimpleNamespace(id="employee-1", asset_model_id="employee-model")
    asset_model = SimpleNamespace(status="active")

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield "connection"

    monkeypatch.setattr(manager, "structure_write_transaction", transaction)
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(lambda **_kwargs: _Query(asset)),
    )
    monkeypatch.setattr(
        manager.AssetModel,
        "filter",
        staticmethod(lambda **_kwargs: _Query(asset_model)),
    )
    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _Query(SimpleNamespace(id="ref-1"))),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_asset_instance("tenant-a", asset.id))

    assert exc_info.value.enum is ErrorCode.ASSET_INSTANCE_HAS_REFERENCES


def test_legacy_relation_models_and_handlers_are_removed():
    assert not hasattr(asset_models, "AssetInstanceRelationType")
    assert not hasattr(asset_models, "AssetInstanceRelation")
    assert not hasattr(views, "ci_relation_type_list")
    assert not hasattr(views, "ci_relation_save")
    assert not hasattr(views, "ci_relation_graph")


def test_destructive_rename_migration_drops_legacy_tables_and_creates_new_tables():
    migration = importlib.import_module(
        "migrations.models.96_20260731203000_destructive_asset_network_rename"
    )
    sql = asyncio.run(migration.upgrade(None))

    assert migration.RUN_IN_TRANSACTION is True
    assert 'DROP TABLE IF EXISTS "supply_chain_ci_reference" CASCADE' in sql
    assert 'DROP TABLE IF EXISTS "supply_chain_actual_policy" CASCADE' in sql
    assert 'CREATE TABLE "asset_reference"' in sql
    assert 'CREATE TABLE "asset_model"' in sql
    assert 'CREATE TABLE "asset_instance"' in sql
    assert 'CREATE TABLE "network_access_actual_policy"' in sql
    assert sql.index('DROP TABLE IF EXISTS "supply_chain_ci_reference"') < sql.index(
        'CREATE TABLE "asset_reference"'
    )


def test_destructive_rename_migration_creates_exactly_the_renamed_orm_tables():
    migration = importlib.import_module(
        "migrations.models.96_20260731203000_destructive_asset_network_rename"
    )
    sql = asyncio.run(migration.upgrade(None))

    orm_tables = {
        model._meta.db_table
        for module in (asset_models, network_models)
        for model in vars(module).values()
        if isinstance(model, type)
        and issubclass(model, Model)
        and model.__module__ == module.__name__
    }
    created_tables = set(re.findall(r'CREATE TABLE "([^"]+)"', sql))

    assert created_tables == orm_tables


def test_destructive_rename_migration_does_not_copy_legacy_business_data():
    migration = importlib.import_module(
        "migrations.models.96_20260731203000_destructive_asset_network_rename"
    )
    sql = asyncio.run(migration.upgrade(None))

    assert "INSERT INTO" not in sql
    assert 'UPDATE "asset_' not in sql
    assert 'UPDATE "network_access_' not in sql
    assert 'FROM "supply_chain_' not in sql
    assert "JSONB_AGG" not in sql
    assert "JSONB_ARRAY_ELEMENTS" not in sql
