from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "supply_chain_ci_type" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "icon" VARCHAR(100),
    "color" VARCHAR(32),
    "category" VARCHAR(30) NOT NULL DEFAULT 'asset',
    "primary_field" VARCHAR(64) NOT NULL DEFAULT 'name',
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "is_locked" BOOL NOT NULL DEFAULT False,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_supply_chain_ci_type_tenant_key" UNIQUE ("tenant_id", "key")
);
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_type_tenant_status" ON "supply_chain_ci_type" ("tenant_id", "status");

        CREATE TABLE IF NOT EXISTS "supply_chain_ci_field_def" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "ci_type_key" VARCHAR(64) NOT NULL,
    "field_key" VARCHAR(64) NOT NULL,
    "label" VARCHAR(255) NOT NULL,
    "data_type" VARCHAR(20) NOT NULL,
    "enum_options" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "ref_ci_type" VARCHAR(64),
    "required" BOOL NOT NULL DEFAULT False,
    "unique" BOOL NOT NULL DEFAULT False,
    "searchable" BOOL NOT NULL DEFAULT False,
    "listable" BOOL NOT NULL DEFAULT True,
    "default_value" JSONB,
    "sort" INT NOT NULL DEFAULT 0,
    "is_locked" BOOL NOT NULL DEFAULT False,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_supply_chain_field_tenant_type_key" UNIQUE ("tenant_id", "ci_type_key", "field_key")
);
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_field_tenant_type" ON "supply_chain_ci_field_def" ("tenant_id", "ci_type_key");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_field_tenant_data_type" ON "supply_chain_ci_field_def" ("tenant_id", "data_type");

        CREATE TABLE IF NOT EXISTS "supply_chain_ci_relation_type" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "source_ci_types" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "target_ci_types" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "cardinality" VARCHAR(10) NOT NULL DEFAULT 'n-n',
    "relation_fields" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "is_locked" BOOL NOT NULL DEFAULT False,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_supply_chain_relation_type_tenant_key" UNIQUE ("tenant_id", "key")
);

        CREATE TABLE IF NOT EXISTS "supply_chain_ci" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "ci_type_key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "attributes" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "risk_status" VARCHAR(20) NOT NULL DEFAULT 'normal',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_tenant_type" ON "supply_chain_ci" ("tenant_id", "ci_type_key");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_tenant_type_status" ON "supply_chain_ci" ("tenant_id", "ci_type_key", "status");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_tenant_area" ON "supply_chain_ci" ("tenant_id", "project_area_id");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_tenant_project" ON "supply_chain_ci" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_scope" ON "supply_chain_ci" ("tenant_id", "ci_type_key", "project_area_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_attrs_gin" ON "supply_chain_ci" USING GIN ("attributes");

        CREATE TABLE IF NOT EXISTS "supply_chain_ci_relation" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "relation_type_key" VARCHAR(64) NOT NULL,
    "source_ci_id" VARCHAR(36) NOT NULL REFERENCES "supply_chain_ci" ("id") ON DELETE CASCADE,
    "target_ci_id" VARCHAR(36) NOT NULL REFERENCES "supply_chain_ci" ("id") ON DELETE CASCADE,
    "attributes" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_supply_chain_relation_unique" UNIQUE ("tenant_id", "relation_type_key", "source_ci_id", "target_ci_id")
);
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_relation_tenant_type" ON "supply_chain_ci_relation" ("tenant_id", "relation_type_key");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_relation_source" ON "supply_chain_ci_relation" ("tenant_id", "source_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_relation_target" ON "supply_chain_ci_relation" ("tenant_id", "target_ci_id");

        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            'supply-chain-review-permissions',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'supply-chain-review',
            '安全管理',
            '["supply_chain_review:model:c","supply_chain_review:model:r","supply_chain_review:model:u","supply_chain_review:model:d","supply_chain_review:field:c","supply_chain_review:field:r","supply_chain_review:field:u","supply_chain_review:field:d","supply_chain_review:relation:c","supply_chain_review:relation:r","supply_chain_review:relation:u","supply_chain_review:relation:d","supply_chain_review:ci:c","supply_chain_review:ci:r","supply_chain_review:ci:u","supply_chain_review:ci:d"]',
            '[]',
            'supply_chain_review_bp'
        )
        ON CONFLICT ("model") DO UPDATE SET "Permissions" = EXCLUDED."Permissions", "description" = EXCLUDED."description", "bp" = EXCLUDED."bp", "utime" = CURRENT_TIMESTAMP;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu" WHERE "model" = 'supply-chain-review';
        DROP TABLE IF EXISTS "supply_chain_ci_relation";
        DROP TABLE IF EXISTS "supply_chain_ci";
        DROP TABLE IF EXISTS "supply_chain_ci_relation_type";
        DROP TABLE IF EXISTS "supply_chain_ci_field_def";
        DROP TABLE IF EXISTS "supply_chain_ci_type";
    """
