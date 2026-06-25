from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "digital_employee_role" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "role_key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "template_dir" VARCHAR(512) NOT NULL,
    "default_skill_keys" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "default_rbac_role_id" VARCHAR(36),
    "license_required" BOOL NOT NULL DEFAULT False,
    "version" INT NOT NULL DEFAULT 1,
    "is_builtin" BOOL NOT NULL DEFAULT False,
    "enabled" BOOL NOT NULL DEFAULT True,
    "meta" JSONB NOT NULL DEFAULT '{}'::jsonb
);
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_digital_emp_tenant__role_k_3d7e7d" ON "digital_employee_role" ("tenant_id", "role_key");
        CREATE INDEX IF NOT EXISTS "idx_digital_emp_tenant__is_buil_9f57ea" ON "digital_employee_role" ("tenant_id", "is_builtin");
        CREATE TABLE IF NOT EXISTS "digital_employee_skill" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "skill_key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "skill_dir" VARCHAR(512) NOT NULL,
    "bindings" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "require_approval" BOOL NOT NULL DEFAULT False,
    "version" INT NOT NULL DEFAULT 1,
    "is_builtin" BOOL NOT NULL DEFAULT False,
    "enabled" BOOL NOT NULL DEFAULT True,
    "meta" JSONB NOT NULL DEFAULT '{}'::jsonb
);
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_digital_emp_tenant__skill__63f4c0" ON "digital_employee_skill" ("tenant_id", "skill_key");
        CREATE INDEX IF NOT EXISTS "idx_digital_emp_tenant__is_buil_d4f3dd" ON "digital_employee_skill" ("tenant_id", "is_builtin");
        CREATE TABLE IF NOT EXISTS "tenant_cognee_credential" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "cognee_user_id" VARCHAR(64) NOT NULL,
    "cognee_user_email" VARCHAR(255) NOT NULL,
    "cognee_user_password_enc" VARCHAR(255) NOT NULL,
    "cognee_jwt" VARCHAR(2048),
    "cognee_jwt_expires_at" TIMESTAMP
);
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_tenant_cog_tenant__86fc52" ON "tenant_cognee_credential" ("tenant_id");
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "role_id" VARCHAR(36);
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "role_key" VARCHAR(64);
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "skill_keys" JSONB NOT NULL DEFAULT '[]';
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "rbac_role_id" VARCHAR(36);
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "resource_scope" JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "workspace_synced_at" TIMESTAMP;
        ALTER TABLE "command_center_agent" ADD COLUMN IF NOT EXISTS "workspace_etag" VARCHAR(64);
        ALTER TABLE "command_center_agent" ADD CONSTRAINT "fk_command__digital__6b67e20c" FOREIGN KEY ("role_id") REFERENCES "digital_employee_role" ("id") ON DELETE SET NULL;
        CREATE TABLE IF NOT EXISTS "digital_employee_report" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "role_key" VARCHAR(64) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "severity" VARCHAR(20) NOT NULL DEFAULT 'info',
    "observation" TEXT,
    "analysis" TEXT,
    "actions" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "conclusion" TEXT,
    "evidence_urls" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "report_document_id" VARCHAR(36),
    "auto_generated" BOOL NOT NULL DEFAULT True,
    "employee_id" VARCHAR(36),
    "task_id" VARCHAR(36) NOT NULL REFERENCES "command_center_task" ("id") ON DELETE CASCADE
);
        ALTER TABLE "digital_employee_report" ADD CONSTRAINT "fk_digital__command__1991f123" FOREIGN KEY ("employee_id") REFERENCES "command_center_agent" ("id") ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS "idx_digital_emp_tenant__role_k_8d0dd5" ON "digital_employee_report" ("tenant_id", "role_key");
        CREATE INDEX IF NOT EXISTS "idx_digital_emp_tenant__task_id_b5c5b8" ON "digital_employee_report" ("tenant_id", "task_id");
        CREATE INDEX IF NOT EXISTS "idx_digital_emp_tenant__severi_334b18" ON "digital_employee_report" ("tenant_id", "severity");
        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            'digital-employee-permissions',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'digital_employee',
            '数字员工',
            '["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d","digital_employee:role:c","digital_employee:role:r","digital_employee:role:u","digital_employee:role:d","digital_employee:skill:c","digital_employee:skill:r","digital_employee:skill:u","digital_employee:skill:d","digital_employee:report:r","digital_employee:report:u","digital_employee:package:import","digital_employee:package:export","digital_employee:package:r","digital_employee:package:d"]',
            '[]',
            'digital_employee_bp'
        )
        ON CONFLICT ("model") DO UPDATE SET "Permissions" = EXCLUDED."Permissions", "description" = EXCLUDED."description", "bp" = EXCLUDED."bp", "utime" = CURRENT_TIMESTAMP;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu" WHERE "model" = 'digital_employee';
        DROP TABLE IF EXISTS "digital_employee_report";
        ALTER TABLE "command_center_agent" DROP CONSTRAINT IF EXISTS "fk_command__digital__6b67e20c";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "workspace_etag";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "workspace_synced_at";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "resource_scope";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "rbac_role_id";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "skill_keys";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "role_key";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "role_id";
        DROP TABLE IF EXISTS "tenant_cognee_credential";
        DROP TABLE IF EXISTS "digital_employee_skill";
        DROP TABLE IF EXISTS "digital_employee_role";"""
