import asyncio
import importlib

from models.user import Account


migration = importlib.import_module(
    "migrations.models.74_20260716183000_allow_reuse_soft_deleted_username"
)


def test_username_model_does_not_declare_global_uniqueness():
    assert Account._meta.fields_map["username"].unique is False


def test_username_migration_enforces_active_only_uniqueness():
    sql = asyncio.run(migration.upgrade(None))

    assert 'UPDATE "account" AS account' in sql
    assert 'account."type" = \'api\'' in sql
    assert 'FROM "tenant_member" AS membership' in sql
    assert 'membership."account_id" = account."id"' in sql
    assert 'DROP CONSTRAINT IF EXISTS "account_username_key"' in sql
    assert 'DROP INDEX IF EXISTS "uid_account_usernam_c7a6b4"' in sql
    assert 'CREATE UNIQUE INDEX "idx_unique_username_active"' in sql
    assert 'WHERE "deleted" IS FALSE' in sql
