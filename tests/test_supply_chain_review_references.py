# coding=utf-8
import asyncio
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models import supply_chain_review as supply_chain_models
from services.supply_chain_review import manager, views


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
        ref_ci_type_id=target_model,
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

    async def fields_for_type(*_args):
        return [field]

    def ci_filter(**kwargs):
        if kwargs.get("id") == "employee-1":
            return _Query(SimpleNamespace(id="employee-1"))
        return _Query(
            SimpleNamespace(
                id="project-1",
                attributes={"manager": "employee-1"},
            )
        )

    monkeypatch.setattr(manager, "fields_for_type", fields_for_type)
    monkeypatch.setattr(manager.SupplyChainCi, "filter", staticmethod(ci_filter))
    monkeypatch.setattr(
        manager.SupplyChainCiUniqueRule,
        "filter",
        staticmethod(lambda **_kwargs: _Query()),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.validate_ci_attributes(
                "tenant-a",
                "project-model",
                {"manager": "employee-1"},
                current_ci_id="project-2",
            )
        )

    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS


def test_reference_validation_locks_targets_inside_the_write_transaction(monkeypatch):
    target_query = _LockingQuery(SimpleNamespace(id="employee-1"))
    monkeypatch.setattr(
        manager.SupplyChainCi,
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

    def ci_filter(**kwargs):
        if kwargs.get("ci_type_id") == "project-model":
            return source_query
        query = _LockingQuery(SimpleNamespace(id=kwargs.get("id")))
        target_queries.append(query)
        return query

    monkeypatch.setattr(manager.SupplyChainCi, "filter", staticmethod(ci_filter))
    field = {
        "field_key": "manager",
        "data_type": "ref",
        "ref_ci_type_id": "employee-model",
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
                "ref_ci_type_id": "employee-model",
                "options": {option: "false"},
            }
        )

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_sync_ci_references_replaces_scalar_and_multiple_index_rows(monkeypatch):
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

    monkeypatch.setattr(manager, "SupplyChainCiReference", ReferenceRecord)
    ci = SimpleNamespace(
        id="project-1",
        attributes={
            "manager": "employee-1",
            "members": ["employee-2", "employee-3"],
        },
    )

    asyncio.run(
        manager.sync_ci_references(
            "tenant-a",
            ci,
            [
                _ref_field("manager"),
                _ref_field("members", multiple=True),
            ],
            "connection",
        )
    )

    assert deleted == [{"tenant_id": "tenant-a", "source_ci_id": "project-1"}]
    assert [
        (row.field_key, row.target_ci_id, row.position) for row in created
    ] == [
        ("manager", "employee-1", 0),
        ("members", "employee-2", 0),
        ("members", "employee-3", 1),
    ]


def test_reference_labels_preserve_scalar_and_multiple_shapes(monkeypatch):
    monkeypatch.setattr(
        manager.SupplyChainCi,
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
    ci = SimpleNamespace(
        ci_type_id="project-model",
        attributes={
            "manager": "employee-1",
            "members": ["employee-1", "employee-2"],
        },
    )

    labels = asyncio.run(
        manager.reference_labels_for_ci(
            "tenant-a",
            ci,
            [
                _ref_field("manager"),
                _ref_field("members", multiple=True),
            ],
        )
    )

    assert labels == {"manager": "张三", "members": ["张三", "李四"]}


def test_reference_graph_uses_field_label_for_edges(monkeypatch):
    root = SimpleNamespace(
        id="project-1",
        name="项目A",
        ci_type_id="project-model",
        ci_type_key="project",
        json={"id": "project-1"},
    )
    target = SimpleNamespace(
        id="employee-1",
        name="张三",
        ci_type_id="employee-model",
        ci_type_key="user",
        json={"id": "employee-1"},
    )
    reference = SimpleNamespace(
        id="reference-1",
        source_ci_id=root.id,
        target_ci_id=target.id,
        field_key="manager",
        json={"field_key": "manager"},
    )
    monkeypatch.setattr(
        manager.SupplyChainCiReference,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _Query(reference)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(lambda **_kwargs: _Query([root, target])),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiFieldDef,
        "filter",
        staticmethod(
            lambda *_args, **_kwargs: _Query(
                SimpleNamespace(
                    ci_type_id="project-model",
                    ci_type_key="project",
                    field_key="manager",
                    label="负责人",
                )
            )
        ),
    )

    graph = asyncio.run(
        manager.build_reference_graph("tenant-a", root_ci_id=root.id, depth=1)
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


def test_delete_ci_rejects_incoming_references(monkeypatch):
    ci = SimpleNamespace(id="employee-1", ci_type_id="employee-model")
    ci_type = SimpleNamespace(status="active")

    @asynccontextmanager
    async def transaction(*_args, **_kwargs):
        yield "connection"

    monkeypatch.setattr(manager, "structure_write_transaction", transaction)
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(lambda **_kwargs: _Query(ci)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _Query(ci_type)),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiReference,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _Query(SimpleNamespace(id="ref-1"))),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_ci("tenant-a", ci.id))

    assert exc_info.value.enum is ErrorCode.ASSET_INSTANCE_HAS_REFERENCES


def test_legacy_relation_models_and_handlers_are_removed():
    assert not hasattr(supply_chain_models, "SupplyChainCiRelationType")
    assert not hasattr(supply_chain_models, "SupplyChainCiRelation")
    assert not hasattr(views, "ci_relation_type_list")
    assert not hasattr(views, "ci_relation_save")
    assert not hasattr(views, "ci_relation_graph")


def test_relation_replacement_migration_is_transactional_and_fail_closed():
    migration = importlib.import_module(
        "migrations.models.95_20260730183000_replace_asset_relations_with_references"
    )
    sql = asyncio.run(migration.upgrade(None))

    assert migration.RUN_IN_TRANSACTION is True
    assert 'CREATE TABLE "supply_chain_ci_reference"' in sql
    assert 'FOREIGN KEY ("source_ci_id")' in sql
    assert 'FOREIGN KEY ("target_ci_id")' in sql
    assert sql.count("ON DELETE CASCADE") >= 2
    assert 'UNIQUE ("tenant_id", "source_ci_id", "field_key", "target_ci_id")' in sql
    assert "Asset relations with attributes must be migrated" in sql
    assert "Asset relation instances with attributes must be migrated" in sql
    assert "Asset relation type endpoints could not be mapped" in sql
    assert "Asset relation instances could not be mapped" in sql
    assert "Asset relation reference fields could not be created" in sql
    assert sql.index('CREATE TABLE "supply_chain_ci_reference"') < sql.index(
        'DROP TABLE "supply_chain_ci_relation"'
    )


def test_relation_replacement_migration_preserves_reference_semantics_and_values():
    migration = importlib.import_module(
        "migrations.models.95_20260730183000_replace_asset_relations_with_references"
    )
    sql = asyncio.run(migration.upgrade(None))

    assert "'multiple', mapping.source_to_target = 'many'" in sql
    assert "'reverse_unique', mapping.target_to_source = 'one'" in sql
    assert "JSONB_AGG(relation.\"target_ci_id\"" in sql
    assert "WHEN multiple THEN target_ids ELSE target_ids -> 0" in sql
    assert '"attributes" = COALESCE(source_ci."attributes", \'{}\'::jsonb) || patch.patch' in sql
    assert "employee.\"attributes\" ->> 'employee_no'" in sql
    assert "employee.\"attributes\" ->> 'person_name'" in sql
    assert "WHEN resolved.target_ci_id IS NULL\n                    THEN COALESCE(project_ci.\"attributes\", '{}'::jsonb)" in sql
    assert "- 'manager'" not in sql
    assert sql.index('UPDATE "supply_chain_ci" AS project_ci') < sql.index(
        'INSERT INTO "supply_chain_ci_reference"'
    )
    assert "FROM JSONB_ARRAY_ELEMENTS(" in sql
    assert "permission ->> 'permission'" in sql
    assert "permission ->> 'value'" in sql
