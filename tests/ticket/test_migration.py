# coding=utf-8
"""迁移脚本断言。现网迁移是手写 SQL 返回字符串，可离线断言。"""

import asyncio
import importlib

migration = importlib.import_module(
    "migrations.models.101_20260810120000_ticket_tables"
)

NINE_TABLES = (
    "ticket_type", "ticket_flow", "ticket_flow_version", "ticket_flow_permission",
    "ticket", "ticket_node_instance", "ticket_activity", "ticket_attachment",
    "ticket_link",
)


def test_upgrade_creates_nine_tables_and_through_table():
    sql = asyncio.run(migration.upgrade(None))
    for table in NINE_TABLES:
        assert f'CREATE TABLE IF NOT EXISTS "{table}"' in sql, f"缺建表 {table}"
    assert 'CREATE TABLE IF NOT EXISTS "ticket_node_approvers"' in sql


def test_upgrade_declares_base_columns_on_every_table():
    sql = asyncio.run(migration.upgrade(None))
    # 每张表都必须带 BaseModel 五字段，否则软删除/租户隔离查询直接报错
    assert sql.count('"deleted" BOOL NOT NULL DEFAULT False') >= len(NINE_TABLES)
    assert sql.count('"tenant_id" VARCHAR(255)') >= len(NINE_TABLES)


def test_ticket_no_unique_per_tenant():
    """单号唯一性是 DB 级兜底，应用层 advisory lock 之外的第二道保险。"""
    sql = asyncio.run(migration.upgrade(None))
    assert 'CREATE UNIQUE INDEX IF NOT EXISTS "uid_ticket_tenant_no"' in sql
    assert '"tenant_id", "ticket_no"' in sql


def test_node_approvers_unique_per_node_and_account():
    """同一审批人不能在同一节点的 M2M 中间表重复出现。"""
    sql = " ".join(asyncio.run(migration.upgrade(None)).split())
    assert (
        'CREATE UNIQUE INDEX IF NOT EXISTS "uid_ticket_node_approvers" '
        'ON "ticket_node_approvers" ("ticket_node_instance_id", "account_id");'
    ) in sql


def test_flow_permission_index_is_not_unique():
    """SPEC §3.1：flow 仅普通索引，不复制 PlaybookPermissions 的 unique 矛盾。"""
    sql = asyncio.run(migration.upgrade(None))
    assert 'CREATE INDEX IF NOT EXISTS "idx_ticket_flow_perm_flow"' in sql
    assert 'UNIQUE INDEX IF NOT EXISTS "idx_ticket_flow_perm_flow"' not in sql


def test_sweep_and_deadline_indexes_present():
    """SLA sweep 每 60 秒全表扫，没索引会拖垮库。"""
    sql = asyncio.run(migration.upgrade(None))
    assert 'CREATE INDEX IF NOT EXISTS "idx_ticket_node_deadline"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_ticket_sla_sweep"' in sql
    normalized = " ".join(sql.split())
    assert (
        'CREATE INDEX IF NOT EXISTS "idx_ticket_sla_sweep" '
        'ON "ticket" ("status", "sla_resolve_at") WHERE "deleted" = FALSE;'
    ) in normalized


def test_nullable_account_and_role_foreign_keys_set_null():
    """账号或角色硬删时保留工单与审计链，只清空可空引用。"""
    sql = asyncio.run(migration.upgrade(None))
    assert sql.count(
        'REFERENCES "account" ("id") ON DELETE SET NULL'
    ) == 8
    assert sql.count(
        'REFERENCES "role" ("id") ON DELETE SET NULL'
    ) == 1


def test_downgrade_drops_all_tables_children_first():
    """先删持有外键的子表，再删被引用的父表，否则 DROP 被外键阻塞。"""
    sql = asyncio.run(migration.downgrade(None))
    order = [sql.index(f'DROP TABLE IF EXISTS "{t}"') for t in (
        "ticket_node_approvers", "ticket_attachment", "ticket_activity",
        "ticket_node_instance", "ticket_link", "ticket",
        "ticket_flow_permission", "ticket_flow_version", "ticket_flow", "ticket_type",
    )]
    assert order == sorted(order), "downgrade 顺序必须子表先行"


def test_migration_runs_in_transaction():
    """建表要原子，中途失败不留半套表。"""
    assert migration.RUN_IN_TRANSACTION is True


def test_downgrade_does_not_touch_approvement():
    """删 approvement 是第二阶段不可逆迁移，不能混进本次可回滚迁移。"""
    sql = asyncio.run(migration.downgrade(None))
    assert "approvement" not in sql
    assert "approve_accounts" not in sql
