# coding=utf-8
import asyncio
import importlib


migration = importlib.import_module(
    "migrations.models.89_20260723235900_replace_builtin_user_with_employee"
)


def test_employee_migration_replaces_builtin_user_definition_and_values():
    sql = asyncio.run(migration.upgrade(None))

    assert 'CREATE TEMP TABLE "tmp_builtin_employee_models"' in sql
    assert "\"key\" = 'user'" in sql
    assert '"is_builtin" = TRUE' in sql
    assert "\"owner\" = 'product'" in sql
    assert "ARRAY['inst_name', 'username']::text[]" in sql
    assert "'person_name', ci.\"attributes\" -> 'inst_name'" in sql
    assert "'employee_no', ci.\"attributes\" -> 'username'" in sql
    assert "('user.inst_name', 'user.username')" in sql
    assert "\"name\" = '员工'" in sql
    assert "\"primary_field\" = 'person_name'" in sql
    assert '"sort" = 235' in sql


def test_employee_migration_is_forward_only():
    assert asyncio.run(migration.downgrade(None)) == ""
