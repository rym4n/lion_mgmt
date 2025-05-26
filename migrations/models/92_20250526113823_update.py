from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ADD "event_name" VARCHAR(255);
        COMMENT ON COLUMN "scene"."status" IS '是否启用，1启用，0禁用';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "scene"."status" IS '是否启用，2启用且为当前场景，1启用，0禁用';
        ALTER TABLE "event_related_script" DROP COLUMN "event_name";"""
