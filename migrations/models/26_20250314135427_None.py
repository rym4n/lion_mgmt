from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS "account" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "username" VARCHAR(32) NOT NULL UNIQUE,
    "email" VARCHAR(150) UNIQUE,
    "phone" VARCHAR(15) UNIQUE,
    "password" VARCHAR(255) NOT NULL,
    "mfa_enable" BOOL NOT NULL DEFAULT False,
    "mfa_key" VARCHAR(50),
    "oauth_id" VARCHAR(255) UNIQUE,
    "disabled" BOOL NOT NULL DEFAULT False
);
COMMENT ON COLUMN "account"."ctime" IS '创建时间';
COMMENT ON COLUMN "account"."utime" IS '更新时间';
COMMENT ON COLUMN "account"."deleted" IS '删除标识';
COMMENT ON TABLE "account" IS '账号模型，包含以下属性：用户名、邮箱、手机号、密码、OAuth ID、是否禁用、角色';
CREATE TABLE IF NOT EXISTS "audit_log" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "username" VARCHAR(32) NOT NULL,
    "action" VARCHAR(50) NOT NULL,
    "module" VARCHAR(80) NOT NULL,
    "result" VARCHAR(20) NOT NULL,
    "client_ip" VARCHAR(50) NOT NULL,
    "client_ua" VARCHAR(255) NOT NULL,
    "uri" VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS "idx_audit_log_usernam_b6341e" ON "audit_log" ("username");
CREATE INDEX IF NOT EXISTS "idx_audit_log_module_a9ee07" ON "audit_log" ("module");
CREATE INDEX IF NOT EXISTS "idx_audit_log_client__41928e" ON "audit_log" ("client_ip");
COMMENT ON COLUMN "audit_log"."ctime" IS '创建时间';
COMMENT ON COLUMN "audit_log"."utime" IS '更新时间';
COMMENT ON COLUMN "audit_log"."deleted" IS '删除标识';
COMMENT ON COLUMN "audit_log"."username" IS '用户名';
COMMENT ON COLUMN "audit_log"."action" IS '操作';
COMMENT ON COLUMN "audit_log"."module" IS '模块';
COMMENT ON COLUMN "audit_log"."result" IS '结果';
COMMENT ON COLUMN "audit_log"."client_ip" IS '客户端IP';
COMMENT ON COLUMN "audit_log"."client_ua" IS '客户端UA';
COMMENT ON COLUMN "audit_log"."uri" IS '请求URL';
COMMENT ON TABLE "audit_log" IS '操作审计记录';
CREATE TABLE IF NOT EXISTS "events" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "event_name" VARCHAR(255),
    "event_process_status" VARCHAR(50),
    "event_source" VARCHAR(255),
    "event_level" VARCHAR(50) NOT NULL,
    "owner" JSONB NOT NULL,
    "created_id" VARCHAR(36) NOT NULL,
    "event_summary" TEXT,
    "log_data" TEXT NOT NULL
);
COMMENT ON COLUMN "events"."ctime" IS '创建时间';
COMMENT ON COLUMN "events"."utime" IS '更新时间';
COMMENT ON COLUMN "events"."deleted" IS '删除标识';
COMMENT ON COLUMN "events"."event_name" IS '事件名称';
COMMENT ON COLUMN "events"."event_process_status" IS '事件处理状态（如 ASSIGNED, RESOLVED）';
COMMENT ON COLUMN "events"."event_source" IS '事件来源';
COMMENT ON COLUMN "events"."event_level" IS '事件等级';
COMMENT ON COLUMN "events"."owner" IS '负责人（包括角色和用户）';
COMMENT ON COLUMN "events"."created_id" IS '创建人ID，关联用户表';
COMMENT ON COLUMN "events"."event_summary" IS '事件概要';
COMMENT ON COLUMN "events"."log_data" IS '日志数据';
COMMENT ON TABLE "events" IS '事件列表';
CREATE TABLE IF NOT EXISTS "role" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "permissions" JSONB
);
COMMENT ON COLUMN "role"."ctime" IS '创建时间';
COMMENT ON COLUMN "role"."utime" IS '更新时间';
COMMENT ON COLUMN "role"."deleted" IS '删除标识';
COMMENT ON TABLE "role" IS '角色权限';
CREATE TABLE IF NOT EXISTS "system_custom_configuration" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "background" VARCHAR(255),
    "webico" VARCHAR(255),
    "logo" VARCHAR(255),
    "copyright" VARCHAR(255),
    "system_name" VARCHAR(255),
    "system_version" VARCHAR(255)
);
COMMENT ON COLUMN "system_custom_configuration"."ctime" IS '创建时间';
COMMENT ON COLUMN "system_custom_configuration"."utime" IS '更新时间';
COMMENT ON COLUMN "system_custom_configuration"."deleted" IS '删除标识';
COMMENT ON COLUMN "system_custom_configuration"."background" IS '登录背景';
COMMENT ON COLUMN "system_custom_configuration"."webico" IS '网页ico';
COMMENT ON COLUMN "system_custom_configuration"."logo" IS '系统logo';
COMMENT ON COLUMN "system_custom_configuration"."copyright" IS '版权';
COMMENT ON COLUMN "system_custom_configuration"."system_name" IS '系统名';
COMMENT ON COLUMN "system_custom_configuration"."system_version" IS '系统版本';
COMMENT ON TABLE "system_custom_configuration" IS '系统自定义配置';
CREATE TABLE IF NOT EXISTS "system_status" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "systemname" VARCHAR(255),
    "instancename" VARCHAR(255),
    "uptime" VARCHAR(255),
    "checkTime" VARCHAR(255),
    "status" VARCHAR(255)
);
COMMENT ON COLUMN "system_status"."ctime" IS '创建时间';
COMMENT ON COLUMN "system_status"."utime" IS '更新时间';
COMMENT ON COLUMN "system_status"."deleted" IS '删除标识';
COMMENT ON COLUMN "system_status"."systemname" IS '系统名';
COMMENT ON COLUMN "system_status"."instancename" IS '实例名';
COMMENT ON COLUMN "system_status"."uptime" IS '启动时间';
COMMENT ON COLUMN "system_status"."checkTime" IS '检查时间';
COMMENT ON COLUMN "system_status"."status" IS '状态';
COMMENT ON TABLE "system_status" IS '系统状态表';
CREATE TABLE IF NOT EXISTS "variables" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL,
    "type" VARCHAR(10) NOT NULL,
    "value" VARCHAR(255) NOT NULL
);
COMMENT ON COLUMN "variables"."ctime" IS '创建时间';
COMMENT ON COLUMN "variables"."utime" IS '更新时间';
COMMENT ON COLUMN "variables"."deleted" IS '删除标识';
COMMENT ON COLUMN "variables"."name" IS '变量名';
COMMENT ON COLUMN "variables"."type" IS '变量类型';
COMMENT ON COLUMN "variables"."value" IS '变量值';
COMMENT ON TABLE "variables" IS '全局变量模型，包含以下属性：变量名、变量类型、变量值';
CREATE TABLE IF NOT EXISTS "account_role" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_account_rol_account_a7676b" ON "account_role" ("account_id", "role_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
