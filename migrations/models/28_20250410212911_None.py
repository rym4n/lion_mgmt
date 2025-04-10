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
    "mfa_bind" BOOL NOT NULL DEFAULT False,
    "oauth_id" VARCHAR(255) UNIQUE,
    "disabled" BOOL NOT NULL DEFAULT False,
    "permit_ip" VARCHAR(255)
);
COMMENT ON COLUMN "account"."ctime" IS '创建时间';
COMMENT ON COLUMN "account"."utime" IS '更新时间';
COMMENT ON COLUMN "account"."deleted" IS '删除标识';
COMMENT ON TABLE "account" IS '账号模型，包含以下属性：用户名、邮箱、手机号、密码、OAuth ID、是否禁用、角色';
CREATE TABLE IF NOT EXISTS "approvement" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "title" VARCHAR(50) NOT NULL,
    "content" TEXT NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "expire_time" TIMESTAMPTZ,
    "approvers" VARCHAR(255),
    "processor_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_approvement_title_2bd859" ON "approvement" ("title");
COMMENT ON COLUMN "approvement"."ctime" IS '创建时间';
COMMENT ON COLUMN "approvement"."utime" IS '更新时间';
COMMENT ON COLUMN "approvement"."deleted" IS '删除标识';
COMMENT ON COLUMN "approvement"."title" IS '审核标题';
COMMENT ON COLUMN "approvement"."content" IS '审核描述内容';
COMMENT ON COLUMN "approvement"."status" IS '审核状态，0代表待审核，1代表审核通过，2代表审核拒绝，3代表审核过期';
COMMENT ON COLUMN "approvement"."expire_time" IS '审核过期时间';
COMMENT ON COLUMN "approvement"."approvers" IS '审核人列表，多个用逗号分隔';
COMMENT ON COLUMN "approvement"."processor_id" IS '实际审核处理人';
COMMENT ON TABLE "approvement" IS '人工审核模型';
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
    "uri" VARCHAR(255),
    "request_data" JSONB
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
COMMENT ON COLUMN "audit_log"."request_data" IS '请求数据';
COMMENT ON TABLE "audit_log" IS '操作审计记录';
CREATE TABLE IF NOT EXISTS "event_related_script" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "condition" VARCHAR(255),
    "event_ingestion_id" VARCHAR(255),
    "topic" VARCHAR(255),
    "script_id" JSONB
);
COMMENT ON COLUMN "event_related_script"."ctime" IS '创建时间';
COMMENT ON COLUMN "event_related_script"."utime" IS '更新时间';
COMMENT ON COLUMN "event_related_script"."deleted" IS '删除标识';
COMMENT ON COLUMN "event_related_script"."condition" IS '执行条件';
COMMENT ON COLUMN "event_related_script"."event_ingestion_id" IS '事件接入id';
COMMENT ON COLUMN "event_related_script"."topic" IS '主题名';
COMMENT ON COLUMN "event_related_script"."script_id" IS '剧本id';
COMMENT ON TABLE "event_related_script" IS '事件关联剧本表';
CREATE TABLE IF NOT EXISTS "events" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "event_name" VARCHAR(255) NOT NULL UNIQUE,
    "event_process_status" VARCHAR(50) NOT NULL,
    "event_source" VARCHAR(255),
    "event_level" VARCHAR(50) NOT NULL,
    "owner" JSONB,
    "created_id" VARCHAR(36) NOT NULL,
    "event_summary" TEXT,
    "log_data" TEXT NOT NULL,
    "event_ingestion_id" VARCHAR(255) NOT NULL
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
COMMENT ON COLUMN "events"."event_ingestion_id" IS '事件接入id，管理事件接入表';
COMMENT ON TABLE "events" IS '事件列表';
CREATE TABLE IF NOT EXISTS "events_ingestion" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "event_ingestion_name" VARCHAR(255) UNIQUE,
    "event_ingestion_describe" VARCHAR(255),
    "event_source_type" VARCHAR(255),
    "transforms_type" VARCHAR(255),
    "enabled_status" INT,
    "linked_execution" INT,
    "created_id" VARCHAR(36) NOT NULL,
    "syslog_config" VARCHAR(36),
    "file_path" VARCHAR(255)
);
COMMENT ON COLUMN "events_ingestion"."ctime" IS '创建时间';
COMMENT ON COLUMN "events_ingestion"."utime" IS '更新时间';
COMMENT ON COLUMN "events_ingestion"."deleted" IS '删除标识';
COMMENT ON COLUMN "events_ingestion"."event_ingestion_name" IS '事件接入名称';
COMMENT ON COLUMN "events_ingestion"."event_ingestion_describe" IS '事件接入描述';
COMMENT ON COLUMN "events_ingestion"."event_source_type" IS '事件来源类型';
COMMENT ON COLUMN "events_ingestion"."transforms_type" IS '转换类型';
COMMENT ON COLUMN "events_ingestion"."enabled_status" IS '是否开启上游接入';
COMMENT ON COLUMN "events_ingestion"."linked_execution" IS '是否开启联动执行';
COMMENT ON COLUMN "events_ingestion"."created_id" IS '创建人ID，关联用户表';
COMMENT ON COLUMN "events_ingestion"."syslog_config" IS 'syslog端口';
COMMENT ON COLUMN "events_ingestion"."file_path" IS '配置文件路径';
COMMENT ON TABLE "events_ingestion" IS '事件接入列表';
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
CREATE TABLE IF NOT EXISTS "tool" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255),
    "version" VARCHAR(50),
    "path" VARCHAR(255),
    "description" VARCHAR(255),
    "status" INT NOT NULL DEFAULT 0,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    "hash_value" VARCHAR(255)
);
COMMENT ON COLUMN "tool"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool"."utime" IS '更新时间';
COMMENT ON COLUMN "tool"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool"."name" IS '工具名称';
COMMENT ON COLUMN "tool"."version" IS '工具版本号';
COMMENT ON COLUMN "tool"."path" IS '文件地址';
COMMENT ON COLUMN "tool"."description" IS '工具描述';
COMMENT ON COLUMN "tool"."status" IS '是否有剧本使用它 1:有 0:无';
COMMENT ON COLUMN "tool"."creator" IS '创建者';
COMMENT ON COLUMN "tool"."updater" IS '更新者';
COMMENT ON COLUMN "tool"."hash_value" IS '文件hash值';
COMMENT ON TABLE "tool" IS '工具管理表';
CREATE TABLE IF NOT EXISTS "tool_action" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_name" VARCHAR(255),
    "version_number" VARCHAR(50),
    "action" JSONB,
    "roles_id" JSONB,
    "users_id" JSONB
);
COMMENT ON COLUMN "tool_action"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_action"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_action"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_action"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_action"."version_number" IS '版本号';
COMMENT ON COLUMN "tool_action"."action" IS '动作';
COMMENT ON COLUMN "tool_action"."roles_id" IS '有权限执行的角色';
COMMENT ON COLUMN "tool_action"."users_id" IS '有权限执行的用户';
COMMENT ON TABLE "tool_action" IS '工具动作管理表';
CREATE TABLE IF NOT EXISTS "tool_resources" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_name" VARCHAR(255) NOT NULL,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255) NOT NULL,
    "product_supplier" VARCHAR(255),
    "product_name" VARCHAR(255),
    "resources" JSONB NOT NULL,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
COMMENT ON COLUMN "tool_resources"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_resources"."name" IS '资源名称';
COMMENT ON COLUMN "tool_resources"."description" IS '资源描述';
COMMENT ON COLUMN "tool_resources"."product_supplier" IS '供应商';
COMMENT ON COLUMN "tool_resources"."product_name" IS '产品名称';
COMMENT ON COLUMN "tool_resources"."resources" IS '资源配置';
COMMENT ON COLUMN "tool_resources"."creator" IS '创建者';
COMMENT ON COLUMN "tool_resources"."updater" IS '更新者';
COMMENT ON TABLE "tool_resources" IS '工具资源表';
CREATE TABLE IF NOT EXISTS "tool_resources_model" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_name" VARCHAR(255) NOT NULL,
    "resources" JSONB NOT NULL
);
COMMENT ON COLUMN "tool_resources_model"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources_model"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources_model"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources_model"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_resources_model"."resources" IS '资源模板';
COMMENT ON TABLE "tool_resources_model" IS '工具资源模板表';
CREATE TABLE IF NOT EXISTS "variables" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "value" JSONB NOT NULL,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_variables_name_4779ca" ON "variables" ("name");
COMMENT ON COLUMN "variables"."ctime" IS '创建时间';
COMMENT ON COLUMN "variables"."utime" IS '更新时间';
COMMENT ON COLUMN "variables"."deleted" IS '删除标识';
COMMENT ON COLUMN "variables"."name" IS '变量名';
COMMENT ON COLUMN "variables"."value" IS '变量值';
COMMENT ON COLUMN "variables"."updator_id" IS '更新人';
COMMENT ON TABLE "variables" IS '全局变量模型，包含以下属性：变量名、变量类型、变量值(必须是JSON格式）、更新人';
CREATE TABLE IF NOT EXISTS "account_role" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_account_rol_account_a7676b" ON "account_role" ("account_id", "role_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
