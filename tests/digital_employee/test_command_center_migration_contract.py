# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_run_and_approval_migration_is_expand_only():
    migration = _load_module(
        "migration_47_command_run_approval_contract_test",
        "migrations/models/47_20260629131500_add_command_task_run_and_approval.py",
    )

    sql = asyncio.run(migration.upgrade(None))

    assert 'CREATE TABLE IF NOT EXISTS "command_center_task_run"' in sql
    assert 'CREATE TABLE IF NOT EXISTS "command_center_approval"' in sql
    assert 'REFERENCES "command_center_task" ("id") ON DELETE CASCADE' in sql
    assert 'REFERENCES "command_center_agent" ("id") ON DELETE SET NULL' in sql
    assert '"runtime" VARCHAR(30) NOT NULL DEFAULT \'lion_agent\'' in sql
    assert '"policy_snapshot" JSONB NOT NULL DEFAULT \'{}\'::jsonb' in sql
    assert '"manifest_snapshot" JSONB NOT NULL DEFAULT \'{}\'::jsonb' in sql
    assert '"requested_action" JSONB NOT NULL DEFAULT \'{}\'::jsonb' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_command_task_run_tenant_task"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_command_approval_approver_status"' in sql
    assert 'ALTER TABLE "command_center_task"' not in sql
    assert 'DROP TABLE' not in sql


def test_command_run_and_approval_downgrade_drops_only_new_tables():
    migration = _load_module(
        "migration_47_command_run_approval_contract_test_downgrade",
        "migrations/models/47_20260629131500_add_command_task_run_and_approval.py",
    )

    sql = asyncio.run(migration.downgrade(None))

    assert 'DROP TABLE IF EXISTS "command_center_approval"' in sql
    assert 'DROP TABLE IF EXISTS "command_center_task_run"' in sql
    assert "command_center_task_event" not in sql
    assert "command_center_task;" not in sql
