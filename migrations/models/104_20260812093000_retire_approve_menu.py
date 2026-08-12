from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'approve'
          AND "deleted" = FALSE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
