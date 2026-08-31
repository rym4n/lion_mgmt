from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu"
        WHERE "model" = 'embedding_config';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    # This menu was an unauthorized legacy entry and has no supported route to restore.
    return """
        SELECT 1;
    """
