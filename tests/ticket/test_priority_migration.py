# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations/models/127_20260919_ticket_remove_priority.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_127_ticket_remove_priority_contract_test",
        MIGRATION_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_flattens_sla_and_drops_only_ticket_priority():
    sql = asyncio.run(_load_migration().upgrade(None))

    bucket_order = [
        sql.index('"sla_config"->\'1\''),
        sql.index('"sla_config"->\'0\''),
        sql.index('"sla_config"->\'2\''),
        sql.index('"sla_config"->\'3\''),
    ]
    assert bucket_order == sorted(bucket_order)
    assert "'respond'" in sql
    assert "'resolve'" in sql
    assert "'warn_before'" in sql
    assert 'ALTER TABLE "ticket" DROP COLUMN "priority"' in sql
    assert "command_center" not in sql


def test_downgrade_restores_priority_and_nests_flat_sla_in_default_bucket():
    sql = asyncio.run(_load_migration().downgrade(None))

    assert 'ADD COLUMN "priority" INT NOT NULL DEFAULT 1' in sql
    assert "jsonb_build_object(" in sql
    assert "'1'" in sql
    assert '"sla_config"->\'respond\'' in sql
    assert '"sla_config"->\'resolve\'' in sql
    assert '"sla_config"->\'warn_before\'' in sql
