from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    """新增 OpenViking 租户凭证表（SPEC §5.11.4）。

    用于存储租户对应的 OpenViking account/user/key，支持轮换。
    api_key 字段在应用层使用 EncryptedCharField 自动加密/解密。
    """
    return """
        CREATE TABLE IF NOT EXISTS "tenant_openviking_credential" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(36) NOT NULL UNIQUE,
            "account_id" VARCHAR(128) NOT NULL,
            "user_id" VARCHAR(128) NOT NULL DEFAULT 'default',
            "api_key" VARCHAR(2048) NOT NULL,
            "credential_version" INT NOT NULL DEFAULT 1
        );
        COMMENT ON TABLE "tenant_openviking_credential" IS '租户 OpenViking 知识库凭证（阶段三）';
        COMMENT ON COLUMN "tenant_openviking_credential"."tenant_id" IS '租户 ID（外键）';
        COMMENT ON COLUMN "tenant_openviking_credential"."account_id" IS 'OpenViking account ID';
        COMMENT ON COLUMN "tenant_openviking_credential"."user_id" IS 'OpenViking user ID';
        COMMENT ON COLUMN "tenant_openviking_credential"."api_key" IS 'OpenViking API key（应用层加密）';
        COMMENT ON COLUMN "tenant_openviking_credential"."credential_version" IS '凭证版本号（轮换时递增）';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    """删除 OpenViking 租户凭证表。

    不可逆：加密的 API key 删除后无法恢复，需重新调用 OpenViking API 签发。
    """
    return """
        DROP TABLE IF EXISTS "tenant_openviking_credential";
    """
