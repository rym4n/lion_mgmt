# coding=utf-8
import asyncio
import importlib
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
migration = importlib.import_module(
    "migrations.models.66_20260710110000_fix_dashboard_statistics_and_superadmin"
)


def test_dashboard_statistics_only_count_real_actions_and_external_playbook_runs():
    upgrade_sql = asyncio.run(migration.upgrade(None))
    init_sql = (WORKSPACE_ROOT / "aioe_package/aioe_install/init.sql").read_text()

    for sql in (upgrade_sql, init_sql):
        assert "INNER JOIN playbook_version" in sql
        assert "INNER JOIN playbook" in sql
        assert "MIN(r.ctime)::date" in sql
        assert "GROUP BY r.run_id, r.playbook_name, r.tenant_id" in sql
        assert "r.node_type = 'action-node'" in sql
        assert "r.action_display_name IS NOT NULL" in sql
        assert "COALESCE(p.internal, FALSE) = FALSE" in sql


def test_migration_restores_seeded_system_admin_wildcard_permission():
    upgrade_sql = asyncio.run(migration.upgrade(None))

    assert "id = '111111'" in upgrade_sql
    assert "'[\"*:*\"]'::jsonb" in upgrade_sql
    assert "? '*:*'" in upgrade_sql


def test_pending_approval_expiry_has_a_partial_lookup_index():
    upgrade_sql = asyncio.run(migration.upgrade(None))
    init_sql = (WORKSPACE_ROOT / "aioe_package/aioe_install/init.sql").read_text()

    for sql in (upgrade_sql, init_sql):
        assert "idx_approvement_pending_expiry" in sql
        assert "ON approvement (tenant_id, expire_time)" in sql
        assert "WHERE status = 0 AND deleted = FALSE" in sql
