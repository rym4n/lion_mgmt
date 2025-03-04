from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS "user" (
    "id" BIGSERIAL NOT NULL PRIMARY KEY,
    "name" VARCHAR(20) NOT NULL DEFAULT '',
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN "user"."id" IS '用户id';
COMMENT ON COLUMN "user"."name" IS '姓名';
COMMENT ON COLUMN "user"."ctime" IS '创建时间';
COMMENT ON COLUMN "user"."utime" IS '更新时间';
COMMENT ON TABLE "user" IS '用户表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
