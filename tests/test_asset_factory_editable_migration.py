# coding=utf-8
import asyncio
import importlib


migration = importlib.import_module(
    "migrations.models.86_20260722211500_make_asset_factory_models_editable"
)


def test_asset_factory_migration_unlocks_records_and_removes_custom_group():
    sql = asyncio.run(migration.upgrade(None))

    for table in (
        "supply_chain_ci_model_group",
        "supply_chain_ci_type",
        "supply_chain_ci_field_def",
        "supply_chain_ci_unique_rule",
        "supply_chain_ci_relation_type",
    ):
        assert f'UPDATE "{table}"' in sql
    assert 'SET "is_locked" = FALSE' in sql
    assert "custom.\"key\" = 'custom'" in sql
    assert '"group_id" = replacements.replacement_group_id' in sql
    assert '"group_key" = replacements.replacement_group_key' in sql
    assert 'SET\n            "deleted" = TRUE' in sql
    assert "candidate.\"key\" <> 'custom'" in sql


def test_asset_factory_migration_is_forward_only():
    assert asyncio.run(migration.downgrade(None)) == ""
