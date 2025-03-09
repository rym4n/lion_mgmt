from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "authentication_session";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
