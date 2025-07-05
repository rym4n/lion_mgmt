from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ADD "creator_id" VARCHAR(36);
        ALTER TABLE "playbook" ADD CONSTRAINT "fk_playbook_account_48c30a25" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        ALTER TABLE "playbook_permissions" ADD "users_id" VARCHAR(36);
        ALTER TABLE "playbook_permissions" ADD "roles_id" VARCHAR(36);
        ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_role_1245ee4e" FOREIGN KEY ("roles_id") REFERENCES "role" ("id") ON DELETE CASCADE;
        ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_account_f271118a" FOREIGN KEY ("users_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" ALTER COLUMN "roles_id" DROP NOT NULL;"""
