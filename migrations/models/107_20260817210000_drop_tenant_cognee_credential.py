from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    """删除 Cognee 租户凭证表（SPEC §5.9 第 5 步）。

    Cognee 后端已整体下线，这张表不再有读写方。历史迁移
    （40_/42_）中的 Cognee 语句保持原样——迁移必须可重放。

    不可逆：表里存的是 Cognee 用户名/密码/JWT，删除后无法恢复。
    Cognee 侧的实际用户与数据不受影响（那在 lion-memory 容器内）。
    """
    return """
        DROP TABLE IF EXISTS "tenant_cognee_credential";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    """重建空表结构，但**不恢复数据**。

    列定义与 40_20260624_add_digital_employee.py 保持一致，
    使 schema 可回退；凭证内容需重新注册 Cognee 用户才能得到。
    """
    return """
        CREATE TABLE IF NOT EXISTS "tenant_cognee_credential" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            "cognee_user_id" VARCHAR(64) NOT NULL,
            "cognee_user_email" VARCHAR(255) NOT NULL,
            "cognee_user_password_enc" VARCHAR(255) NOT NULL,
            "cognee_jwt" VARCHAR(2048),
            "cognee_jwt_expires_at" TIMESTAMPTZ
        );
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_tenant_cog_tenant__86fc52" ON "tenant_cognee_credential" ("tenant_id");
    """
