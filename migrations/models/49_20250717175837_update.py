from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "system_custom_configuration";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
