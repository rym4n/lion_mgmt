from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "network_topology_version" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "version" INT NOT NULL,
            "name" VARCHAR(200) NOT NULL,
            "topology_json" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "layout_json" JSONB,
            "source_hash" VARCHAR(64),
            "change_type" VARCHAR(32) NOT NULL,
            "change_summary" TEXT NOT NULL,
            "topology_id" VARCHAR(36) NOT NULL
                REFERENCES "network_topology" ("id") ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
            "idx_network_topology_version_identity"
            ON "network_topology_version" ("topology_id", "version");
        CREATE INDEX IF NOT EXISTS
            "idx_network_topology_version_tenant_topology"
            ON "network_topology_version" (
                "tenant_id", "topology_id", "version" DESC
            );

        INSERT INTO "network_topology_version" (
            "id",
            "ctime",
            "utime",
            "deleted",
            "tenant_id",
            "version",
            "name",
            "topology_json",
            "layout_json",
            "source_hash",
            "change_type",
            "change_summary",
            "topology_id"
        )
        SELECT
            MD5(
                'network-topology-baseline:' || "id" || ':' || "revision"::TEXT
            ),
            "utime",
            "utime",
            FALSE,
            "tenant_id",
            "revision",
            "name",
            "topology_json",
            "layout_json",
            "source_hash",
            'baseline',
            '历史拓扑当前状态基线',
            "id"
        FROM "network_topology"
        WHERE NOT "deleted"
        ON CONFLICT ("topology_id", "version") DO NOTHING;

        COMMENT ON TABLE "network_topology_version" IS
            '网络拓扑不可变版本快照，不保存设备原始配置';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "network_topology_version";
    """
