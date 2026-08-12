# coding=utf-8
"""存量租户管理员工单权限迁移 SQL 契约断言。"""

import asyncio
import importlib


migration = importlib.import_module(
    "migrations.models.103_20260811132616_ticket_role_permissions"
)


def _upgrade_sql() -> str:
    return asyncio.run(migration.upgrade(None))


def test_upgrade_appends_both_ticket_permission_groups():
    sql = _upgrade_sql()

    assert "permissions::jsonb ||" in sql
    assert (
        "'[\"ticket:r,u,c\",\"ticket:view,detail,create,approve,reject,transfer,"
        "return,comment,add_approver,rm_approver,suspend,resume,close,withdraw\"]'::jsonb"
        in sql
    )


def test_upgrade_only_updates_active_tenant_administrators():
    sql = _upgrade_sql()

    assert 'UPDATE "role"' in sql
    assert 'WHERE "name" = \'租户管理员\'' in sql
    assert 'AND "deleted" = False' in sql
    assert "系统管理员" not in sql


def test_migration_runs_in_transaction():
    assert migration.RUN_IN_TRANSACTION is True


def test_downgrade_does_not_remove_preexisting_permissions():
    assert " ".join(asyncio.run(migration.downgrade(None)).split()) == "SELECT 1;"
