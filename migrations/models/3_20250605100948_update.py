from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "room_msg_account" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "status" INT NOT NULL DEFAULT 0,
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "message_id" VARCHAR(36) NOT NULL REFERENCES "room_msg" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room_msg_account"."ctime" IS '创建时间';
COMMENT ON COLUMN "room_msg_account"."utime" IS '更新时间';
COMMENT ON COLUMN "room_msg_account"."deleted" IS '删除标识';
COMMENT ON COLUMN "room_msg_account"."status" IS '阅读状态，0代表未读，1代表已读';
COMMENT ON COLUMN "room_msg_account"."account_id" IS '用户';
COMMENT ON COLUMN "room_msg_account"."message_id" IS '消息';
COMMENT ON TABLE "room_msg_account" IS '消息与用户关系表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "room_msg_account";"""
