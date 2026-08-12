# coding=utf-8
import asyncio
import importlib


migration = importlib.import_module(
    "migrations.models.87_20260723120000_enforce_ci_field_label_uniqueness"
)


def test_ci_field_label_migration_deduplicates_and_adds_partial_unique_index():
    sql = asyncio.run(migration.upgrade(None))

    assert 'CREATE TEMP TABLE "tmp_ci_field_removals"' in sql
    assert "PARTITION BY\n                    COALESCE(field.\"tenant_id\", '')" in sql
    assert 'field."ci_type_id"' in sql
    assert "LOWER(REGEXP_REPLACE(BTRIM(field.\"label\"), '\\s+', ' ', 'g'))" in sql
    assert 'redundant."is_builtin" = TRUE' in sql
    assert 'redundant."is_derived" = TRUE' in sql
    assert 'UPDATE "supply_chain_ci" AS ci' in sql
    assert "JSONB_OBJECT_AGG(" in sql
    assert "removal.redundant_data_type = removal.canonical_data_type" in sql
    assert 'UPDATE "supply_chain_ci_type" AS ci_type' in sql
    assert 'UPDATE "supply_chain_ci_unique_rule" AS unique_rule' in sql
    assert 'UPDATE "supply_chain_ci_field_def" AS field' in sql
    assert '"deleted" = TRUE' in sql
    assert 'SET\n            "label"' not in sql
    assert 'CREATE UNIQUE INDEX IF NOT EXISTS "uid_ci_field_tenant_type_label"' in sql
    assert 'WHERE "deleted" = FALSE' in sql


def test_ci_field_label_migration_downgrade_only_removes_index():
    assert asyncio.run(migration.downgrade(None)) == (
        'DROP INDEX IF EXISTS "uid_ci_field_tenant_type_label";'
    )
