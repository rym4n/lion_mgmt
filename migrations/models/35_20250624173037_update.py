from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ADD "fields" JSONB;
        ALTER TABLE "event_related_script" ADD "numbers" INT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" DROP COLUMN "fields";
        ALTER TABLE "event_related_script" DROP COLUMN "numbers";"""
