from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "employee_api_credential" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            "employee_id" VARCHAR(36) NOT NULL,
            "account_id" VARCHAR(36) NOT NULL,
            "api_token" VARCHAR(2048) NOT NULL,
            CONSTRAINT "uid_employee_ap_employe_8bdc9f" UNIQUE ("employee_id")
        );
        COMMENT ON COLUMN "employee_api_credential"."ctime" IS '创建时间';
        COMMENT ON COLUMN "employee_api_credential"."utime" IS '更新时间';
        COMMENT ON COLUMN "employee_api_credential"."deleted" IS '删除标识';
        COMMENT ON COLUMN "employee_api_credential"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "employee_api_credential"."employee_id" IS '数字员工 CommandAgent ID';
        COMMENT ON COLUMN "employee_api_credential"."account_id" IS 'AIOE API 账号 Account ID';
        COMMENT ON COLUMN "employee_api_credential"."api_token" IS 'aioe-auth token（account_id:jwt）';
        COMMENT ON TABLE "employee_api_credential" IS '数字员工到 AIOE 平台 API 账号的映射。';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "employee_api_credential";
    """
