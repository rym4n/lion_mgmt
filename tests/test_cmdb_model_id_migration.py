import asyncio
import importlib

from services.supply_chain_review.manager import normalize_model_package, payload_ci_type


migration = importlib.import_module(
    "migrations.models.77_20260717150000_use_model_ids_for_cmdb_associations"
)


def test_model_id_migration_backfills_every_runtime_reference_and_fails_closed():
    sql = asyncio.run(migration.upgrade(None))

    assert 'ADD COLUMN IF NOT EXISTS "ci_type_id"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "ref_ci_type_id"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "source_ci_type_ids"' in sql
    assert 'ADD COLUMN IF NOT EXISTS "target_ci_type_ids"' in sql
    assert 'model."tenant_id" = field."tenant_id"' in sql
    assert 'model."tenant_id" = ci."tenant_id"' in sql
    assert "CMDB model ID backfill failed" in sql
    assert 'FOREIGN KEY ("ci_type_id")' in sql
    assert 'ON DELETE RESTRICT' in sql


def test_new_model_key_is_internal_when_the_api_omits_it():
    payload = payload_ci_type({"name": "服务器"}, "admin")

    assert payload["key"] is None
    assert payload["name"] == "服务器"


def test_portable_model_package_discards_source_environment_model_ids():
    package = normalize_model_package(
        {
            "version": 1,
            "ci_types": [{"key": "server", "name": "服务器"}],
            "fields": [
                {
                    "ci_type_id": "source-model-id",
                    "ci_type_key": "server",
                    "field_key": "peer",
                    "label": "关联设备",
                    "data_type": "ref",
                    "ref_ci_type_id": "source-peer-id",
                    "ref_ci_type": "server",
                }
            ],
            "relation_types": [
                {
                    "key": "connects",
                    "name": "连接",
                    "source_ci_type_ids": ["source-model-id"],
                    "target_ci_type_ids": ["source-peer-id"],
                    "source_ci_types": ["server"],
                    "target_ci_types": ["server"],
                    "relation_fields": [
                        {
                            "field_key": "peer",
                            "label": "关联设备",
                            "data_type": "ref",
                            "ref_ci_type_id": "source-peer-id",
                            "ref_ci_type": "server",
                        }
                    ],
                }
            ],
        }
    )

    assert "ci_type_id" not in package["fields"][0]
    assert "ref_ci_type_id" not in package["fields"][0]
    relation = package["relation_types"][0]
    assert "source_ci_type_ids" not in relation
    assert "target_ci_type_ids" not in relation
    assert "ref_ci_type_id" not in relation["relation_fields"][0]
