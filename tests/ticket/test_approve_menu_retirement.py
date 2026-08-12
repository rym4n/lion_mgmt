# coding=utf-8
"""Legacy approval menu retirement and soft-delete read contracts."""

import asyncio
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT.parent
migration = importlib.import_module(
    "migrations.models.104_20260812093000_retire_approve_menu"
)


def test_upgrade_soft_deletes_legacy_approve_menu():
    sql = " ".join(asyncio.run(migration.upgrade(None)).split())

    assert 'UPDATE "permissions_menu"' in sql
    assert 'SET "deleted" = TRUE' in sql
    assert 'WHERE "model" = \'approve\'' in sql
    assert 'AND "deleted" = FALSE' in sql
    assert 'DELETE FROM "permissions_menu"' not in sql


def test_downgrade_does_not_resurrect_retired_menu():
    assert " ".join(asyncio.run(migration.downgrade(None)).split()) == "SELECT 1;"


def test_new_install_seed_does_not_create_approve_menu():
    init_sql = (WORKSPACE_ROOT / "aioe_package/aioe_install/init.sql").read_text(
        encoding="utf-8"
    )

    assert "'ticket-permissions'" in init_sql
    assert "'ticket-flow-permissions'" in init_sql
    assert "'approve'" not in init_sql
    assert '"model" = \'approve\'' not in init_sql


def test_all_permission_menu_reads_filter_soft_deleted_rows():
    for relative_path in ("services/license/views.py", "services/role/views.py"):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "PermissionsMenu.all().values()" not in source
        assert "PermissionsMenu.filter(deleted=False).values()" in source


def test_authorization_description_lookup_filters_soft_deleted_rows():
    source = (ROOT / "libs/security/authorization.py").read_text(encoding="utf-8")

    assert 'model=request.path.split("/")[1], deleted=False' in source


def test_migration_runs_in_transaction():
    assert migration.RUN_IN_TRANSACTION is True
