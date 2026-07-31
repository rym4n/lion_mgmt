import asyncio
import importlib

from services.asset_management.manager import (
    normalize_asset_model_package,
    payload_asset_model,
)


migration = importlib.import_module(
    "migrations.models.77_20260717150000_use_model_ids_for_cmdb_associations"
)


def test_legacy_model_id_migration_backfills_references_and_fails_closed():
    sql = asyncio.run(migration.upgrade(None))

    assert 'ADD COLUMN IF NOT EXISTS "ci_type_id"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "ref_ci_type_id"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "source_ci_type_ids"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "target_ci_type_ids"' in sql
    assert 'model."tenant_id" = field."tenant_id"' in sql
    assert 'model."tenant_id" = ci."tenant_id"' in sql
    assert "CMDB model ID backfill failed" in sql
    assert 'FOREIGN KEY ("ci_type_id")' in sql
    assert "ON DELETE RESTRICT" in sql


def test_new_model_key_is_internal_when_the_api_omits_it():
    payload = payload_asset_model({"name": "服务器"}, "admin")

    assert payload["key"] is None
    assert payload["name"] == "服务器"


def test_portable_model_package_discards_source_environment_model_ids():
    package = normalize_asset_model_package(
        {
            "schema_version": 2,
            "asset_model_groups": [{"key": "asset", "name": "资产"}],
            "asset_models": [
                {
                    "key": "server",
                    "name": "服务器",
                    "group_key": "asset",
                }
            ],
            "asset_fields": [
                {
                    "asset_model_id": "source-model-id",
                    "asset_model_key": "server",
                    "field_key": "peer",
                    "label": "关联设备",
                    "data_type": "ref",
                    "referenced_asset_model_id": "source-peer-id",
                    "referenced_asset_model_key": "server",
                }
            ],
            "asset_unique_rules": [],
        }
    )

    assert "asset_model_id" not in package["asset_fields"][0]
    assert "referenced_asset_model_id" not in package["asset_fields"][0]
    assert package["asset_fields"][0]["referenced_asset_model_key"] == "server"
    assert "relation_types" not in package
