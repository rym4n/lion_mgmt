from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """ALTER TABLE "playbook" DROP CONSTRAINT IF EXISTS "fk_playbook_account_48c30a25";
        ALTER TABLE "playbook" DROP COLUMN "creator_id";
                ALTER TABLE "playbook" ADD "creator_id" VARCHAR(36);
                ALTER TABLE "playbook" ADD CONSTRAINT "fk_playbook_account_48c30a25" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
                ALTER TABLE "playbook_permissions" ADD "users_id" VARCHAR(36);
                ALTER TABLE "playbook_permissions" ADD "roles_id" VARCHAR(36);
                ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_role_1245ee4e" FOREIGN KEY ("roles_id") REFERENCES "role" ("id") ON DELETE CASCADE;
                ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_account_f271118a" FOREIGN KEY ("users_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_permissions_user";
        DROP TABLE IF EXISTS "playbook_permissions_role";
        ALTER TABLE "playbook_permissions" ADD "users" JSONB NOT NULL;
        ALTER TABLE "playbook_permissions" ADD "roles" JSONB NOT NULL;
        ALTER TABLE "playbook_permissions" DROP COLUMN "perms";"""
