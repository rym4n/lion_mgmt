from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "command_center_task_run" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "task_id" VARCHAR(36) NOT NULL REFERENCES "command_center_task" ("id") ON DELETE CASCADE,
    "employee_id" VARCHAR(36) REFERENCES "command_center_agent" ("id") ON DELETE SET NULL,
    "run_id" VARCHAR(64) NOT NULL,
    "session_id" VARCHAR(128),
    "runtime" VARCHAR(30) NOT NULL DEFAULT 'lion_agent',
    "status" VARCHAR(30) NOT NULL DEFAULT 'created',
    "lease_owner" VARCHAR(128),
    "lease_expires_at" TIMESTAMPTZ,
    "heartbeat_at" TIMESTAMPTZ,
    "workspace_etag" VARCHAR(64),
    "manifest_snapshot" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "policy_snapshot" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "tool_calls" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "resource_accesses" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "usage" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "cost" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "transcript_ref" VARCHAR(255),
    "evidence_refs" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "error" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT "uid_command_task_run_tenant_run" UNIQUE ("tenant_id", "run_id")
);
        CREATE INDEX IF NOT EXISTS "idx_command_task_run_tenant_task" ON "command_center_task_run" ("tenant_id", "task_id");
        CREATE INDEX IF NOT EXISTS "idx_command_task_run_tenant_status" ON "command_center_task_run" ("tenant_id", "status");
        CREATE INDEX IF NOT EXISTS "idx_command_task_run_employee_status" ON "command_center_task_run" ("tenant_id", "employee_id", "status");
        CREATE INDEX IF NOT EXISTS "idx_command_task_run_heartbeat" ON "command_center_task_run" ("tenant_id", "heartbeat_at");
        COMMENT ON TABLE "command_center_task_run" IS '指挥中心任务执行记录。';
        COMMENT ON COLUMN "command_center_task_run"."runtime" IS '运行时，固定为 lion_agent';
        COMMENT ON COLUMN "command_center_task_run"."policy_snapshot" IS '执行时 policy.json 快照';
        COMMENT ON COLUMN "command_center_task_run"."manifest_snapshot" IS '执行时 manifest.json 快照';

        CREATE TABLE IF NOT EXISTS "command_center_approval" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "task_id" VARCHAR(36) NOT NULL REFERENCES "command_center_task" ("id") ON DELETE CASCADE,
    "run_id" VARCHAR(36) REFERENCES "command_center_task_run" ("id") ON DELETE SET NULL,
    "employee_id" VARCHAR(36) REFERENCES "command_center_agent" ("id") ON DELETE SET NULL,
    "status" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "trigger_type" VARCHAR(50) NOT NULL,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "policy_snapshot" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "impacted_resources" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "requested_action" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "rollback_plan" TEXT,
    "requester_id" VARCHAR(36),
    "approver_id" VARCHAR(36),
    "decided_at" TIMESTAMPTZ,
    "expires_at" TIMESTAMPTZ,
    "decision_comment" TEXT,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb
);
        CREATE INDEX IF NOT EXISTS "idx_command_approval_tenant_status" ON "command_center_approval" ("tenant_id", "status");
        CREATE INDEX IF NOT EXISTS "idx_command_approval_tenant_task" ON "command_center_approval" ("tenant_id", "task_id");
        CREATE INDEX IF NOT EXISTS "idx_command_approval_approver_status" ON "command_center_approval" ("tenant_id", "approver_id", "status");
        COMMENT ON TABLE "command_center_approval" IS '指挥中心审批记录。';
        COMMENT ON COLUMN "command_center_approval"."policy_snapshot" IS '审批触发时 policy.json 快照';
        COMMENT ON COLUMN "command_center_approval"."requested_action" IS '审批请求动作';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "command_center_approval";
        DROP TABLE IF EXISTS "command_center_task_run";
    """
