from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    # 119 建的 tenant_embedding_config 是重复造轮子：既有的 llm_model_config
    # 表本来就有 purpose='embedding' + dimensions + 加密 api_key，正是知识库
    # 需要的全部信息。两处配置必然漂移 —— 实际表现为用户在「系统设置-模型配置」
    # 里配好了 embedding，知识空间页却报「服务不可用」，因为它读的是另一张空表。
    #
    # 改为直接读 llm_model_config；本表只保留「当前 collection 是用哪个向量
    # 空间指纹建的」这一项记账，用于判断换模型后是否需要重建索引。
    #
    # 119 建的表从未承载过有效数据（上线即为空），直接丢弃不做数据搬迁。
    return """
        DROP TABLE IF EXISTS "tenant_embedding_config";
        CREATE TABLE IF NOT EXISTS "tenant_embedding_index_state" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(36) NOT NULL UNIQUE,
            "indexed_fingerprint" VARCHAR(32) NOT NULL DEFAULT '',
            "rebuilt_at" TIMESTAMPTZ,
            "rebuilt_by" VARCHAR(36)
        );
        CREATE INDEX IF NOT EXISTS "idx_tenant_emb_state_tenant"
            ON "tenant_embedding_index_state" ("tenant_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "tenant_embedding_index_state";
    """
