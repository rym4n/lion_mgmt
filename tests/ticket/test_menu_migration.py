# coding=utf-8
"""工单菜单与权限迁移 SQL 契约断言。"""

import asyncio
import importlib

migration = importlib.import_module(
    "migrations.models.102_20260811012831_ticket_menu_permissions"
)


def _upgrade_sql() -> str:
    return asyncio.run(migration.upgrade(None))


def _insert_sql(sql: str, item_id: str) -> str:
    start = sql.index(f"'{item_id}'")
    end = sql.index(";", sql.index('ON CONFLICT ("model")', start)) + 1
    return sql[start:end]


def test_upgrade_inserts_exactly_two_ticket_menu_records():
    sql = _upgrade_sql()
    assert sql.count('INSERT INTO "permissions_menu"') == 2

    ticket_sql = _insert_sql(sql, "ticket-permissions")
    assert "'ticket'" in ticket_sql
    assert "'工单列表'" in ticket_sql
    assert "'ticket_bp'" in ticket_sql

    flow_sql = _insert_sql(sql, "ticket-flow-permissions")
    assert "'ticket_flow'" in flow_sql
    assert "'流程设计'" in flow_sql
    assert "'ticket_bp'" in flow_sql


def test_upgrade_uses_contract_permission_keys_in_new_format():
    sql = _upgrade_sql()
    assert '["ticket:c","ticket:r","ticket:u","ticket:all"]' in sql
    assert '["ticket_flow:r","ticket_flow:u","ticket_flow:d"]' in sql
    assert '"label"' not in sql
    assert '"value"' not in sql
    assert '"Permissions"' in sql


def test_upgrade_contains_four_languages_and_rank_60_for_each_record():
    sql = _upgrade_sql()
    for item_id in ("ticket-permissions", "ticket-flow-permissions"):
        record_sql = _insert_sql(sql, item_id)
        for language in ("CN", "TW", "HK", "EN"):
            assert f'"{language}":' in record_sql
        assert record_sql.count('"rank":60') == 4
        assert '"path":"/ticket"' in record_sql
        assert '"redirect":"/ticket/list"' in record_sql
        assert '"component":"layout/index.vue"' in record_sql
        assert '"icon":"ri:file-list-3-line"' in record_sql


def test_upgrade_contains_contract_child_routes_and_permissions():
    sql = _upgrade_sql()
    ticket_sql = _insert_sql(sql, "ticket-permissions")
    assert '"name":"TicketList"' in ticket_sql
    assert '"path":"/ticket/list"' in ticket_sql
    assert '"component":"views/ticket/list/index.vue"' in ticket_sql
    assert '"icon":"ri:list-check-2"' in ticket_sql
    assert '"auths":["ticket:r"]' in ticket_sql
    assert '"permissions":["ticket:r"]' in ticket_sql

    flow_sql = _insert_sql(sql, "ticket-flow-permissions")
    assert '"name":"TicketFlow"' in flow_sql
    assert '"path":"/ticket/flow"' in flow_sql
    assert '"component":"views/ticket/flow/index.vue"' in flow_sql
    assert '"icon":"ri:flow-chart"' in flow_sql
    assert '"auths":["ticket_flow:r"]' in flow_sql
    assert '"permissions":["ticket_flow:r"]' in flow_sql


def test_upgrade_is_idempotent_on_unique_model():
    sql = _upgrade_sql()
    assert sql.count('ON CONFLICT ("model") DO UPDATE SET') == 2
    for item_id in ("ticket-permissions", "ticket-flow-permissions"):
        record_sql = _insert_sql(sql, item_id)
        for column in ("description", "Permissions", "url", "bp"):
            assert f'"{column}" = EXCLUDED."{column}"' in record_sql


def test_downgrade_only_deletes_the_two_ticket_menu_records():
    sql = asyncio.run(migration.downgrade(None))
    normalized = " ".join(sql.split())
    assert normalized == (
        'DELETE FROM "permissions_menu" '
        'WHERE "model" IN (\'ticket\', \'ticket_flow\');'
    )
    assert "approve" not in sql.lower()


def test_migration_runs_in_transaction():
    assert migration.RUN_IN_TRANSACTION is True
