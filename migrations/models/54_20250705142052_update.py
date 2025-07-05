from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "playbook_permissions"."roles_id" IS '角色';
        COMMENT ON COLUMN "playbook_permissions"."users_id" IS '用户';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "playbook_permissions"."roles_id" IS '用户';
        COMMENT ON COLUMN "playbook_permissions"."users_id" IS '角色';"""
