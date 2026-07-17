from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "role"
        SET
            "permissions" = '["*:*"]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "name" = '租户管理员'
          AND "deleted" = False
          AND NOT (COALESCE("permissions", '[]'::jsonb) ? '*:*');
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
