from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room_msg" ADD "image_content" TEXT NOT NULL;
        ALTER TABLE "room_msg" DROP COLUMN "content_type";
        COMMENT ON COLUMN "room_msg"."content" IS '文本消息内容';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room_msg" ADD "content_type" VARCHAR(10) NOT NULL DEFAULT 'text';
        ALTER TABLE "room_msg" DROP COLUMN "image_content";
        COMMENT ON COLUMN "room_msg"."content" IS '消息内容';"""
