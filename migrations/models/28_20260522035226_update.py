from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "command_center_agent" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "agent_key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "capabilities" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL DEFAULT 'idle',
    "load" INT NOT NULL DEFAULT 0,
    "max_concurrent" INT NOT NULL DEFAULT 1,
    "current_task_id" VARCHAR(36),
    "heartbeat_at" TIMESTAMPTZ,
    "metadata" JSONB NOT NULL,
    CONSTRAINT "uid_command_cen_tenant__379118" UNIQUE ("tenant_id", "agent_key")
);
COMMENT ON COLUMN "command_center_agent"."ctime" IS '创建时间';
COMMENT ON COLUMN "command_center_agent"."utime" IS '更新时间';
COMMENT ON COLUMN "command_center_agent"."deleted" IS '删除标识';
COMMENT ON COLUMN "command_center_agent"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "command_center_agent"."agent_key" IS '智能体逻辑标识';
COMMENT ON COLUMN "command_center_agent"."name" IS '智能体名称';
COMMENT ON COLUMN "command_center_agent"."description" IS '智能体说明';
COMMENT ON COLUMN "command_center_agent"."capabilities" IS '能力标签列表';
COMMENT ON COLUMN "command_center_agent"."status" IS '状态: idle,busy,offline,error';
COMMENT ON COLUMN "command_center_agent"."load" IS '当前任务负载';
COMMENT ON COLUMN "command_center_agent"."max_concurrent" IS '最大并发任务数';
COMMENT ON COLUMN "command_center_agent"."current_task_id" IS '当前主任务ID';
COMMENT ON COLUMN "command_center_agent"."heartbeat_at" IS '最近心跳时间';
COMMENT ON COLUMN "command_center_agent"."metadata" IS '扩展元数据';
COMMENT ON TABLE "command_center_agent" IS '指挥部智能体登记表。';
        CREATE TABLE IF NOT EXISTS "command_center_task" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "title" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "source" VARCHAR(50) NOT NULL DEFAULT 'manual',
    "priority" INT NOT NULL DEFAULT 3,
    "status" VARCHAR(30) NOT NULL DEFAULT 'pending_dispatch',
    "progress" INT NOT NULL DEFAULT 0,
    "required_capabilities" JSONB NOT NULL,
    "payload" JSONB NOT NULL,
    "requested_agent_id" VARCHAR(64),
    "assigned_agent_key" VARCHAR(64),
    "dispatch_status" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "dispatch_reason" TEXT,
    "run_id" VARCHAR(36),
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "assigned_agent_id" VARCHAR(36) REFERENCES "command_center_agent" ("id") ON DELETE SET NULL,
    "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
    "room_id" VARCHAR(36) REFERENCES "room" ("id") ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS "idx_command_cen_tenant__ed9fd6" ON "command_center_task" ("tenant_id", "status");
CREATE INDEX IF NOT EXISTS "idx_command_cen_tenant__979b2d" ON "command_center_task" ("tenant_id", "assigned_agent_key");
CREATE INDEX IF NOT EXISTS "idx_command_cen_tenant__b370b1" ON "command_center_task" ("tenant_id", "dispatch_status");
COMMENT ON COLUMN "command_center_task"."ctime" IS '创建时间';
COMMENT ON COLUMN "command_center_task"."utime" IS '更新时间';
COMMENT ON COLUMN "command_center_task"."deleted" IS '删除标识';
COMMENT ON COLUMN "command_center_task"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "command_center_task"."title" IS '任务标题';
COMMENT ON COLUMN "command_center_task"."description" IS '任务描述';
COMMENT ON COLUMN "command_center_task"."source" IS '任务来源';
COMMENT ON COLUMN "command_center_task"."priority" IS '优先级，数值越小优先级越高';
COMMENT ON COLUMN "command_center_task"."status" IS '任务状态';
COMMENT ON COLUMN "command_center_task"."progress" IS '任务进度 0-100';
COMMENT ON COLUMN "command_center_task"."required_capabilities" IS '任务所需能力标签';
COMMENT ON COLUMN "command_center_task"."payload" IS '任务载荷';
COMMENT ON COLUMN "command_center_task"."requested_agent_id" IS '用户指定的智能体ID或agent_key';
COMMENT ON COLUMN "command_center_task"."assigned_agent_key" IS '实际分配的智能体逻辑标识';
COMMENT ON COLUMN "command_center_task"."dispatch_status" IS '调度状态';
COMMENT ON COLUMN "command_center_task"."dispatch_reason" IS '调度原因';
COMMENT ON COLUMN "command_center_task"."run_id" IS '后续执行系统run_id';
COMMENT ON COLUMN "command_center_task"."started_at" IS '开始时间';
COMMENT ON COLUMN "command_center_task"."finished_at" IS '结束时间';
COMMENT ON COLUMN "command_center_task"."assigned_agent_id" IS '实际分配的智能体';
COMMENT ON COLUMN "command_center_task"."creator_id" IS '创建人';
COMMENT ON COLUMN "command_center_task"."room_id" IS '关联作战室';
COMMENT ON TABLE "command_center_task" IS '指挥部任务表。';
        CREATE TABLE IF NOT EXISTS "command_center_task_event" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "event_type" VARCHAR(50) NOT NULL,
    "status" VARCHAR(30),
    "progress" INT,
    "agent_key" VARCHAR(64),
    "message" TEXT,
    "metadata" JSONB NOT NULL,
    "operator_id" VARCHAR(36),
    "task_id" VARCHAR(36) NOT NULL REFERENCES "command_center_task" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "command_center_task_event"."ctime" IS '创建时间';
COMMENT ON COLUMN "command_center_task_event"."utime" IS '更新时间';
COMMENT ON COLUMN "command_center_task_event"."deleted" IS '删除标识';
COMMENT ON COLUMN "command_center_task_event"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "command_center_task_event"."event_type" IS '事件类型';
COMMENT ON COLUMN "command_center_task_event"."status" IS '任务状态快照';
COMMENT ON COLUMN "command_center_task_event"."progress" IS '进度快照';
COMMENT ON COLUMN "command_center_task_event"."agent_key" IS '相关智能体';
COMMENT ON COLUMN "command_center_task_event"."message" IS '事件说明';
COMMENT ON COLUMN "command_center_task_event"."metadata" IS '事件元数据';
COMMENT ON COLUMN "command_center_task_event"."operator_id" IS '操作者ID';
COMMENT ON COLUMN "command_center_task_event"."task_id" IS '任务';
COMMENT ON TABLE "command_center_task_event" IS '指挥部任务事件流，用于展示任务进展与调度历史。';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "command_center_task_event";
        DROP TABLE IF EXISTS "command_center_agent";
        DROP TABLE IF EXISTS "command_center_task";"""


MODELS_STATE = (
    "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"
)
