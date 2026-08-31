# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
migration_path = (
    Path(__file__).resolve().parents[2]
    / "migrations/models/122_20260831100000_remove_embedding_config_menu.py"
)
migration_spec = importlib.util.spec_from_file_location(
    "remove_embedding_config_menu", migration_path
)
assert migration_spec is not None and migration_spec.loader is not None
migration = importlib.util.module_from_spec(migration_spec)
migration_spec.loader.exec_module(migration)


def test_embedding_menu_is_removed_from_seed_and_upgrades():
    upgrade_sql = asyncio.run(migration.upgrade(None))
    init_sql = (WORKSPACE_ROOT / "aioe_package/aioe_install/init.sql").read_text(encoding="utf-8")

    assert 'DELETE FROM "permissions_menu"' in upgrade_sql
    assert '"model" = \'embedding_config\'' in upgrade_sql
    assert "embedding_config" not in init_sql


def test_embedding_menu_retirement_downgrade_is_non_mutating():
    assert asyncio.run(migration.downgrade(None)) == """
        SELECT 1;
    """
