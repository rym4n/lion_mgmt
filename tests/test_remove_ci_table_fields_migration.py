# coding=utf-8
import asyncio
import importlib


migration = importlib.import_module(
    "migrations.models.88_20260723231000_remove_ci_table_fields"
)


def test_remove_ci_table_fields_migration_cleans_definitions_and_values():
    sql = asyncio.run(migration.upgrade(None))

    assert 'CREATE TEMP TABLE "tmp_removed_ci_table_fields"' in sql
    assert "field.\"data_type\" = 'table'" in sql
    assert 'UPDATE "supply_chain_ci" AS ci' in sql
    assert 'UPDATE "supply_chain_ci_unique_rule" AS unique_rule' in sql
    assert 'CREATE TEMP TABLE "tmp_removed_relation_table_fields"' in sql
    assert 'UPDATE "supply_chain_ci_relation" AS relation' in sql
    assert 'UPDATE "supply_chain_ci_relation_type" AS relation_type' in sql
    assert "field.value ->> 'data_type' IS DISTINCT FROM 'table'" in sql
    assert "COALESCE(field.value ->> 'field_key', '') <> ''" not in sql
    assert "COALESCE(field.field_key, '') <> ''" in sql
    assert '"default_value" = NULL' in sql
    assert 'UPDATE "supply_chain_ci_field_def" AS field' in sql
    assert '"deleted" = TRUE' in sql


def test_remove_ci_table_fields_migration_is_forward_only():
    assert asyncio.run(migration.downgrade(None)) == ""
