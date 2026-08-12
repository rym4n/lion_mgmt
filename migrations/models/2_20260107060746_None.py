from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


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
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "username" VARCHAR(32) NOT NULL UNIQUE,
    "display_name" VARCHAR(255),
    "email" VARCHAR(150) UNIQUE,
    "phone" VARCHAR(15) UNIQUE,
    "password" VARCHAR(255) NOT NULL,
    "mfa_enable" BOOL NOT NULL DEFAULT False,
    "mfa_key" VARCHAR(50),
    "mfa_bind" BOOL NOT NULL DEFAULT False,
    "oauth_id" VARCHAR(255) UNIQUE,
    "disabled" BOOL NOT NULL DEFAULT False,
    "permit_ip" VARCHAR(255),
    "type" VARCHAR(255) NOT NULL DEFAULT 'user',
    "creator" VARCHAR(32),
    "expiration" TIMESTAMPTZ
);
COMMENT ON COLUMN "account"."ctime" IS '创建时间';
COMMENT ON COLUMN "account"."utime" IS '更新时间';
COMMENT ON COLUMN "account"."deleted" IS '删除标识';
COMMENT ON COLUMN "account"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "account"."creator" IS '创建人';
COMMENT ON COLUMN "account"."expiration" IS 'Token过期时间';
COMMENT ON TABLE "account" IS '账号模型，包含以下属性：用户名、姓名、邮箱、手机号、密码、OAuth ID、是否禁用、角色';
CREATE TABLE IF NOT EXISTS "action_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "action_name" VARCHAR(255) NOT NULL,
    "action_description" VARCHAR(255) NOT NULL,
    "tool_name" VARCHAR(255) NOT NULL,
    "roles" JSONB NOT NULL,
    "users" JSONB NOT NULL
);
COMMENT ON COLUMN "action_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "action_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "action_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "action_permissions"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "action_permissions"."action_name" IS '动作名称';
COMMENT ON COLUMN "action_permissions"."action_description" IS '动作描述';
COMMENT ON COLUMN "action_permissions"."tool_name" IS '工具名称';
COMMENT ON COLUMN "action_permissions"."roles" IS '有权限执行的角色';
COMMENT ON COLUMN "action_permissions"."users" IS '有权限执行的用户';
COMMENT ON TABLE "action_permissions" IS '动作权限表';
CREATE TABLE IF NOT EXISTS "config" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "key" VARCHAR(255) NOT NULL UNIQUE,
    "value" VARCHAR(4000) NOT NULL,
    "type" VARCHAR(255),
    "module" VARCHAR(255) NOT NULL
);
COMMENT ON COLUMN "config"."ctime" IS '创建时间';
COMMENT ON COLUMN "config"."utime" IS '更新时间';
COMMENT ON COLUMN "config"."deleted" IS '删除标识';
COMMENT ON COLUMN "config"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "config"."key" IS '配置项';
COMMENT ON COLUMN "config"."value" IS '配置值';
COMMENT ON COLUMN "config"."type" IS '配置状态';
COMMENT ON COLUMN "config"."module" IS '所属模块';
COMMENT ON TABLE "config" IS '通用配置表';
CREATE TABLE IF NOT EXISTS "event_related_script" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "condition" TEXT,
    "event_ingestion_id" VARCHAR(255),
    "topic" VARCHAR(255),
    "script_id" JSONB,
    "event_name" VARCHAR(255),
    "status" INT NOT NULL DEFAULT 0,
    "numbers" INT,
    "concurrency" INT,
    "fields" JSONB
);
COMMENT ON COLUMN "event_related_script"."ctime" IS '创建时间';
COMMENT ON COLUMN "event_related_script"."utime" IS '更新时间';
COMMENT ON COLUMN "event_related_script"."deleted" IS '删除标识';
COMMENT ON COLUMN "event_related_script"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "event_related_script"."condition" IS '执行条件';
COMMENT ON COLUMN "event_related_script"."event_ingestion_id" IS '事件接入id';
COMMENT ON COLUMN "event_related_script"."topic" IS '主题名';
COMMENT ON COLUMN "event_related_script"."script_id" IS '剧本id';
COMMENT ON COLUMN "event_related_script"."event_name" IS '自定义事件名称';
COMMENT ON COLUMN "event_related_script"."status" IS '是否开启联动执行';
COMMENT ON COLUMN "event_related_script"."numbers" IS '去重数量';
COMMENT ON COLUMN "event_related_script"."concurrency" IS '并行数量';
COMMENT ON COLUMN "event_related_script"."fields" IS '去重字段';
COMMENT ON TABLE "event_related_script" IS '事件关联剧本表';
CREATE TABLE IF NOT EXISTS "events_ingestion" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "event_ingestion_name" VARCHAR(255),
    "event_ingestion_describe" VARCHAR(255),
    "event_source_type" VARCHAR(255),
    "transforms_type" VARCHAR(255),
    "enabled_status" INT,
    "linked_execution" INT,
    "syslog_config" VARCHAR(255) UNIQUE,
    "file_path" VARCHAR(255),
    "vector_name" VARCHAR(255) UNIQUE,
    "config_info" JSONB,
    "vector_config" JSONB,
    "vector_config_raw" JSONB,
    "enabled_health_status" VARCHAR(255),
    "execution_health_status" VARCHAR(255),
    "convert_health_status" VARCHAR(255),
    "retention_days" INT DEFAULT 7,
    "project_id" INT UNIQUE,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "events_ingestion"."ctime" IS '创建时间';
COMMENT ON COLUMN "events_ingestion"."utime" IS '更新时间';
COMMENT ON COLUMN "events_ingestion"."deleted" IS '删除标识';
COMMENT ON COLUMN "events_ingestion"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "events_ingestion"."event_ingestion_name" IS '事件接入名称';
COMMENT ON COLUMN "events_ingestion"."event_ingestion_describe" IS '事件接入描述';
COMMENT ON COLUMN "events_ingestion"."event_source_type" IS '事件来源类型';
COMMENT ON COLUMN "events_ingestion"."transforms_type" IS '转换类型';
COMMENT ON COLUMN "events_ingestion"."enabled_status" IS '是否开启上游接入';
COMMENT ON COLUMN "events_ingestion"."linked_execution" IS '是否开启联动执行';
COMMENT ON COLUMN "events_ingestion"."syslog_config" IS 'syslog端口';
COMMENT ON COLUMN "events_ingestion"."file_path" IS '配置文件路径';
COMMENT ON COLUMN "events_ingestion"."vector_name" IS 'vector名称';
COMMENT ON COLUMN "events_ingestion"."config_info" IS '转换配置信息';
COMMENT ON COLUMN "events_ingestion"."vector_config" IS 'vector配置信息';
COMMENT ON COLUMN "events_ingestion"."vector_config_raw" IS '未进行配置剧本接入的vector配置信息';
COMMENT ON COLUMN "events_ingestion"."enabled_health_status" IS '上游接入健康状态';
COMMENT ON COLUMN "events_ingestion"."execution_health_status" IS '联动执行健康状态';
COMMENT ON COLUMN "events_ingestion"."convert_health_status" IS '转换配置健康状态';
COMMENT ON COLUMN "events_ingestion"."retention_days" IS '数据保存天数';
COMMENT ON COLUMN "events_ingestion"."project_id" IS '关联VL的项目id';
COMMENT ON COLUMN "events_ingestion"."creator_id" IS '创建人';
COMMENT ON COLUMN "events_ingestion"."updator_id" IS '更新人';
COMMENT ON TABLE "events_ingestion" IS '事件接入列表';
CREATE TABLE IF NOT EXISTS "permissions_menu" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "model" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "Permissions" JSONB,
    "url" JSONB,
    "bp" VARCHAR(32)
);
COMMENT ON COLUMN "permissions_menu"."ctime" IS '创建时间';
COMMENT ON COLUMN "permissions_menu"."utime" IS '更新时间';
COMMENT ON COLUMN "permissions_menu"."deleted" IS '删除标识';
COMMENT ON COLUMN "permissions_menu"."tenant_id" IS '租户ID';
COMMENT ON TABLE "permissions_menu" IS '权限菜单';
CREATE TABLE IF NOT EXISTS "playbook" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "remark" TEXT NOT NULL,
    "internal" BOOL NOT NULL DEFAULT False,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_playbook_name_1a146f" UNIQUE ("name", "deleted")
);
COMMENT ON COLUMN "playbook"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook"."name" IS '剧本名称';
COMMENT ON COLUMN "playbook"."remark" IS '描述';
COMMENT ON COLUMN "playbook"."internal" IS '是否内置剧本';
COMMENT ON COLUMN "playbook"."creator_id" IS '创建人';
COMMENT ON TABLE "playbook" IS '剧本模型';
CREATE TABLE IF NOT EXISTS "playbook_version" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "change_desc" TEXT,
    "version" VARCHAR(30) NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "flow_timeout" INT NOT NULL DEFAULT 300,
    "flow_json" JSONB NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_playbook_ve_playboo_808e98" UNIQUE ("playbook_id", "version")
);
COMMENT ON COLUMN "playbook_version"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_version"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_version"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_version"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook_version"."change_desc" IS '变更说明';
COMMENT ON COLUMN "playbook_version"."version" IS '版本号，每次publish都会生成新纪录，用于版本回溯';
COMMENT ON COLUMN "playbook_version"."status" IS '剧本状态,0草稿,1已发布';
COMMENT ON COLUMN "playbook_version"."flow_timeout" IS '剧本运行超时时间，单位秒';
COMMENT ON COLUMN "playbook_version"."flow_json" IS '流程图flow数据，包含edges和nodes';
COMMENT ON COLUMN "playbook_version"."creator_id" IS '创建人';
COMMENT ON COLUMN "playbook_version"."updator_id" IS '更新人';
COMMENT ON TABLE "playbook_version" IS '剧本版本模型';
CREATE TABLE IF NOT EXISTS "approvement" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "title" VARCHAR(50) NOT NULL,
    "run_id" VARCHAR(20) NOT NULL,
    "content" TEXT NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "approve_type" INT NOT NULL DEFAULT 0,
    "expire_time" TIMESTAMPTZ NOT NULL,
    "audit_node" VARCHAR(36) NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "params" JSONB,
    "results" JSONB NOT NULL,
    "callback_token" VARCHAR(16) NOT NULL,
    "flow_id" VARCHAR(36) NOT NULL REFERENCES "playbook_version" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_approvement_title_2bd859" ON "approvement" ("title");
COMMENT ON COLUMN "approvement"."ctime" IS '创建时间';
COMMENT ON COLUMN "approvement"."utime" IS '更新时间';
COMMENT ON COLUMN "approvement"."deleted" IS '删除标识';
COMMENT ON COLUMN "approvement"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "approvement"."title" IS '审核标题';
COMMENT ON COLUMN "approvement"."run_id" IS '关联运行ID';
COMMENT ON COLUMN "approvement"."content" IS '审核描述内容';
COMMENT ON COLUMN "approvement"."status" IS '审核状态，0代表待处理，1代表通过，2代表拒绝';
COMMENT ON COLUMN "approvement"."approve_type" IS '审核类型，1代表或签，2代表会签';
COMMENT ON COLUMN "approvement"."expire_time" IS '审核过期时间';
COMMENT ON COLUMN "approvement"."audit_node" IS '审核节点ID';
COMMENT ON COLUMN "approvement"."created_at" IS '审核触发的时间';
COMMENT ON COLUMN "approvement"."params" IS '自定义参数';
COMMENT ON COLUMN "approvement"."results" IS '处理记录';
COMMENT ON COLUMN "approvement"."callback_token" IS '外部回调token';
COMMENT ON COLUMN "approvement"."flow_id" IS '关联剧本';
COMMENT ON TABLE "approvement" IS '人工审核模型';
CREATE TABLE IF NOT EXISTS "playbook_webhook" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "webhook_token" VARCHAR(16) NOT NULL UNIQUE,
    "description" TEXT,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_version_id" VARCHAR(36) NOT NULL REFERENCES "playbook_version" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook_webhook"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_webhook"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_webhook"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_webhook"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook_webhook"."webhook_token" IS 'webhook唯一标识令牌';
COMMENT ON COLUMN "playbook_webhook"."description" IS '描述';
COMMENT ON COLUMN "playbook_webhook"."creator_id" IS '创建人';
COMMENT ON COLUMN "playbook_webhook"."playbook_version_id" IS '剧本版本';
COMMENT ON TABLE "playbook_webhook" IS '剧本Webhook模型';
CREATE TABLE IF NOT EXISTS "role" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "permissions" JSONB
);
COMMENT ON COLUMN "role"."ctime" IS '创建时间';
COMMENT ON COLUMN "role"."utime" IS '更新时间';
COMMENT ON COLUMN "role"."deleted" IS '删除标识';
COMMENT ON COLUMN "role"."tenant_id" IS '租户ID';
COMMENT ON TABLE "role" IS '角色权限';
CREATE TABLE IF NOT EXISTS "playbook_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "permissions" JSONB,
    "pb_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "roles_id" VARCHAR(36) REFERENCES "role" ("id") ON DELETE CASCADE,
    "users_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_permissions"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook_permissions"."pb_id" IS '剧本';
COMMENT ON COLUMN "playbook_permissions"."roles_id" IS '角色';
COMMENT ON COLUMN "playbook_permissions"."users_id" IS '用户';
COMMENT ON TABLE "playbook_permissions" IS '剧本权限表';
CREATE TABLE IF NOT EXISTS "room" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(50) NOT NULL,
    "run_id" JSONB,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room"."ctime" IS '创建时间';
COMMENT ON COLUMN "room"."utime" IS '更新时间';
COMMENT ON COLUMN "room"."deleted" IS '删除标识';
COMMENT ON COLUMN "room"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "room"."name" IS '作战室名称';
COMMENT ON COLUMN "room"."run_id" IS 'run_id列表';
COMMENT ON COLUMN "room"."creator_id" IS '房主';
COMMENT ON COLUMN "room"."updator_id" IS '房间内进行调试的用户';
COMMENT ON TABLE "room" IS '作战室房间模型';
CREATE TABLE IF NOT EXISTS "room_msg" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "content" TEXT NOT NULL,
    "image_content" TEXT NOT NULL,
    "type" VARCHAR(20) NOT NULL,
    "is_recalled" BOOL NOT NULL DEFAULT False,
    "is_pinned" BOOL NOT NULL DEFAULT False,
    "receiver_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE,
    "sender_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room_msg"."ctime" IS '创建时间';
COMMENT ON COLUMN "room_msg"."utime" IS '更新时间';
COMMENT ON COLUMN "room_msg"."deleted" IS '删除标识';
COMMENT ON COLUMN "room_msg"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "room_msg"."content" IS '文本消息内容';
COMMENT ON COLUMN "room_msg"."image_content" IS '图片消息内容';
COMMENT ON COLUMN "room_msg"."type" IS '消息类型，public群聊private私聊/system系统/announcement公告';
COMMENT ON COLUMN "room_msg"."is_recalled" IS '是否撤回';
COMMENT ON COLUMN "room_msg"."is_pinned" IS '是否置顶（用于群公告）';
COMMENT ON COLUMN "room_msg"."receiver_id" IS '消息接收方';
COMMENT ON COLUMN "room_msg"."room_id" IS '消息所在房间（若为群聊）';
COMMENT ON COLUMN "room_msg"."sender_id" IS '消息发送方';
COMMENT ON TABLE "room_msg" IS '作战室消息模型';
CREATE TABLE IF NOT EXISTS "room_msg_account" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "account_id" VARCHAR(255) NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "message_id" VARCHAR(36) NOT NULL REFERENCES "room_msg" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room_msg_account"."ctime" IS '创建时间';
COMMENT ON COLUMN "room_msg_account"."utime" IS '更新时间';
COMMENT ON COLUMN "room_msg_account"."deleted" IS '删除标识';
COMMENT ON COLUMN "room_msg_account"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "room_msg_account"."account_id" IS '用户';
COMMENT ON COLUMN "room_msg_account"."status" IS '阅读状态，0代表未读，1代表已读';
COMMENT ON COLUMN "room_msg_account"."message_id" IS '消息';
COMMENT ON TABLE "room_msg_account" IS '消息与用户关系表';
CREATE TABLE IF NOT EXISTS "scene" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "remark" VARCHAR(255) NOT NULL,
    "status" INT NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "scene"."ctime" IS '创建时间';
COMMENT ON COLUMN "scene"."utime" IS '更新时间';
COMMENT ON COLUMN "scene"."deleted" IS '删除标识';
COMMENT ON COLUMN "scene"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "scene"."name" IS '场景名称';
COMMENT ON COLUMN "scene"."remark" IS '描述';
COMMENT ON COLUMN "scene"."status" IS '是否启用，1启用，0禁用';
COMMENT ON COLUMN "scene"."creator_id" IS '创建人';
COMMENT ON COLUMN "scene"."updator_id" IS '更新人';
COMMENT ON TABLE "scene" IS '运行场景模型';
CREATE TABLE IF NOT EXISTS "events" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "event_name" VARCHAR(255) NOT NULL,
    "event_process_status" VARCHAR(50) NOT NULL,
    "event_source" VARCHAR(255),
    "download_url" VARCHAR(255),
    "owner" JSONB,
    "event_summary" TEXT,
    "log_data" TEXT NOT NULL,
    "event_ingestion_id" VARCHAR(255),
    "close_time" TIMESTAMPTZ,
    "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE,
    "room_id" VARCHAR(36) REFERENCES "room" ("id") ON DELETE CASCADE,
    "scene_id" VARCHAR(36) REFERENCES "scene" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_events_event_p_976b90" ON "events" ("event_process_status");
COMMENT ON COLUMN "events"."ctime" IS '创建时间';
COMMENT ON COLUMN "events"."utime" IS '更新时间';
COMMENT ON COLUMN "events"."deleted" IS '删除标识';
COMMENT ON COLUMN "events"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "events"."event_name" IS '事件名称';
COMMENT ON COLUMN "events"."event_process_status" IS '事件处理状态';
COMMENT ON COLUMN "events"."event_source" IS '事件来源';
COMMENT ON COLUMN "events"."download_url" IS '事件报告下载链接';
COMMENT ON COLUMN "events"."owner" IS '负责人（包括角色和用户）';
COMMENT ON COLUMN "events"."event_summary" IS '事件概要';
COMMENT ON COLUMN "events"."log_data" IS '日志数据';
COMMENT ON COLUMN "events"."event_ingestion_id" IS '事件接入id，管理事件接入表';
COMMENT ON COLUMN "events"."close_time" IS '关闭时间';
COMMENT ON COLUMN "events"."creator_id" IS '创建人';
COMMENT ON COLUMN "events"."room_id" IS '关联房间';
COMMENT ON COLUMN "events"."scene_id" IS '关联场景';
COMMENT ON COLUMN "events"."updator_id" IS '更新人';
COMMENT ON TABLE "events" IS '事件列表';
CREATE TABLE IF NOT EXISTS "tag" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255) NOT NULL,
    "type" INT NOT NULL
);
COMMENT ON COLUMN "tag"."ctime" IS '创建时间';
COMMENT ON COLUMN "tag"."utime" IS '更新时间';
COMMENT ON COLUMN "tag"."deleted" IS '删除标识';
COMMENT ON COLUMN "tag"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tag"."name" IS '标签名称';
COMMENT ON COLUMN "tag"."type" IS '0为剧本，1为工具';
COMMENT ON TABLE "tag" IS '运行场景模型';
CREATE TABLE IF NOT EXISTS "tenant" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) UNIQUE,
    "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "tenant"."ctime" IS '创建时间';
COMMENT ON COLUMN "tenant"."utime" IS '更新时间';
COMMENT ON COLUMN "tenant"."deleted" IS '删除标识';
COMMENT ON COLUMN "tenant"."name" IS '工作空间名称';
COMMENT ON COLUMN "tenant"."creator_id" IS '创建人';
COMMENT ON TABLE "tenant" IS '工作空间管理';
CREATE TABLE IF NOT EXISTS "tool" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255),
    "version" VARCHAR(50),
    "path" VARCHAR(255),
    "logo_path" VARCHAR(255),
    "description" VARCHAR(255),
    "status" INT NOT NULL DEFAULT 0,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    "hash_value" VARCHAR(255),
    "internal" BOOL NOT NULL DEFAULT False,
    "maintain" BOOL NOT NULL DEFAULT False,
    "tag" JSONB,
    "main" VARCHAR(255)
);
COMMENT ON COLUMN "tool"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool"."utime" IS '更新时间';
COMMENT ON COLUMN "tool"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool"."name" IS '工具名称';
COMMENT ON COLUMN "tool"."version" IS '工具版本号';
COMMENT ON COLUMN "tool"."path" IS '文件地址';
COMMENT ON COLUMN "tool"."logo_path" IS 'Logo地址';
COMMENT ON COLUMN "tool"."description" IS '工具描述';
COMMENT ON COLUMN "tool"."status" IS '是否有剧本使用它 1:有 0:无';
COMMENT ON COLUMN "tool"."creator" IS '创建者';
COMMENT ON COLUMN "tool"."updater" IS '更新者';
COMMENT ON COLUMN "tool"."hash_value" IS '文件hash值';
COMMENT ON COLUMN "tool"."internal" IS '是否支持下载';
COMMENT ON COLUMN "tool"."maintain" IS '是否内置应用';
COMMENT ON COLUMN "tool"."tag" IS '工具标签';
COMMENT ON COLUMN "tool"."main" IS '入口文件';
COMMENT ON TABLE "tool" IS '工具管理表';
CREATE TABLE IF NOT EXISTS "tool_action" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "tool_name" VARCHAR(255),
    "version_number" VARCHAR(50),
    "action" JSONB,
    "playbook" JSONB
);
COMMENT ON COLUMN "tool_action"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_action"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_action"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_action"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool_action"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_action"."version_number" IS '版本号';
COMMENT ON COLUMN "tool_action"."action" IS '动作';
COMMENT ON COLUMN "tool_action"."playbook" IS '绑定的剧本列表';
COMMENT ON TABLE "tool_action" IS '工具动作管理表';
CREATE TABLE IF NOT EXISTS "timed" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255) NOT NULL,
    "type" INT NOT NULL,
    "playbook_version" VARCHAR(50),
    "playbook_input" JSONB,
    "action_input" JSONB,
    "status" INT NOT NULL,
    "tactics" VARCHAR(50) NOT NULL,
    "result" VARCHAR(50),
    "success_rate" VARCHAR(10),
    "enable" INT NOT NULL DEFAULT 1,
    "end_time" VARCHAR(50) NOT NULL,
    "internal" BOOL NOT NULL DEFAULT False,
    "action_id" VARCHAR(36) REFERENCES "tool_action" ("id") ON DELETE CASCADE,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "timed"."ctime" IS '创建时间';
COMMENT ON COLUMN "timed"."utime" IS '更新时间';
COMMENT ON COLUMN "timed"."deleted" IS '删除标识';
COMMENT ON COLUMN "timed"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "timed"."name" IS '任务名称';
COMMENT ON COLUMN "timed"."type" IS '执行类型，0为执行剧本，1为执行动作';
COMMENT ON COLUMN "timed"."playbook_version" IS '剧本版本';
COMMENT ON COLUMN "timed"."playbook_input" IS '剧本输入参数';
COMMENT ON COLUMN "timed"."action_input" IS '工具动作输入参数';
COMMENT ON COLUMN "timed"."status" IS '执行状态，0为单次执行，1为周期执行';
COMMENT ON COLUMN "timed"."tactics" IS '执行策略';
COMMENT ON COLUMN "timed"."result" IS '执行结果';
COMMENT ON COLUMN "timed"."success_rate" IS '成功率';
COMMENT ON COLUMN "timed"."enable" IS '是否启用, 0为禁用，1为启用';
COMMENT ON COLUMN "timed"."end_time" IS '结束时间';
COMMENT ON COLUMN "timed"."internal" IS '是否内置定时任务';
COMMENT ON COLUMN "timed"."action_id" IS '关联工具动作';
COMMENT ON COLUMN "timed"."creator_id" IS '创建人';
COMMENT ON COLUMN "timed"."playbook_id" IS '关联剧本';
COMMENT ON COLUMN "timed"."updator_id" IS '更新人';
COMMENT ON TABLE "timed" IS '运行场景模型';
CREATE TABLE IF NOT EXISTS "runlog" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "run_id" VARCHAR(36) NOT NULL,
    "event_id" VARCHAR(36),
    "node_id" VARCHAR(36) NOT NULL,
    "node_name" VARCHAR(255) NOT NULL,
    "node_type" VARCHAR(36) NOT NULL,
    "tool_name" VARCHAR(255),
    "tool_version" VARCHAR(255),
    "tool_display_name" VARCHAR(255),
    "action_name" VARCHAR(255),
    "action_display_name" VARCHAR(255),
    "status" INT NOT NULL DEFAULT 0,
    "args" JSONB,
    "result" TEXT,
    "start_time" TIMESTAMPTZ,
    "end_time" TIMESTAMPTZ,
    "executor" VARCHAR(36),
    "resource_name" VARCHAR(36),
    "playbook_name" VARCHAR(255),
    "playbook_version_id" VARCHAR(36) REFERENCES "playbook_version" ("id") ON DELETE SET NULL,
    "timer_id" VARCHAR(36) REFERENCES "timed" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_runlog_run_id_b706a0" UNIQUE ("run_id", "node_id", "node_name", "resource_name")
);
CREATE INDEX IF NOT EXISTS "idx_runlog_action__ebd90f" ON "runlog" ("action_display_name");
CREATE INDEX IF NOT EXISTS "idx_runlog_playboo_83741c" ON "runlog" ("playbook_name");
CREATE INDEX IF NOT EXISTS "idx_runlog_playboo_d168c6" ON "runlog" ("playbook_name", "run_id", "status");
COMMENT ON COLUMN "runlog"."ctime" IS '创建时间';
COMMENT ON COLUMN "runlog"."utime" IS '更新时间';
COMMENT ON COLUMN "runlog"."deleted" IS '删除标识';
COMMENT ON COLUMN "runlog"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "runlog"."run_id" IS '每次运行的唯一ID';
COMMENT ON COLUMN "runlog"."event_id" IS '事件id';
COMMENT ON COLUMN "runlog"."node_id" IS '节点ID';
COMMENT ON COLUMN "runlog"."node_name" IS '节点名称';
COMMENT ON COLUMN "runlog"."node_type" IS '节点类型';
COMMENT ON COLUMN "runlog"."tool_name" IS '工具标识';
COMMENT ON COLUMN "runlog"."tool_version" IS '工具版本';
COMMENT ON COLUMN "runlog"."tool_display_name" IS '工具中文名称';
COMMENT ON COLUMN "runlog"."action_name" IS '动作标识';
COMMENT ON COLUMN "runlog"."action_display_name" IS '动作中文名称';
COMMENT ON COLUMN "runlog"."status" IS '执行状态';
COMMENT ON COLUMN "runlog"."args" IS '执行入参';
COMMENT ON COLUMN "runlog"."result" IS '输出结果,啥格式都有可能';
COMMENT ON COLUMN "runlog"."start_time" IS '开始时间';
COMMENT ON COLUMN "runlog"."end_time" IS '结束时间';
COMMENT ON COLUMN "runlog"."executor" IS '执行者ID';
COMMENT ON COLUMN "runlog"."resource_name" IS '资源名称';
COMMENT ON COLUMN "runlog"."playbook_name" IS '剧本名称';
COMMENT ON COLUMN "runlog"."playbook_version_id" IS '关联剧本版本';
COMMENT ON COLUMN "runlog"."timer_id" IS '关联定时任务';
COMMENT ON TABLE "runlog" IS '所有节点的执行记录';
CREATE TABLE IF NOT EXISTS "tool_resources" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "tool_name" VARCHAR(255) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "engine" VARCHAR(255),
    "resources" VARCHAR(5000) NOT NULL,
    "internal" BOOL NOT NULL DEFAULT False,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
COMMENT ON COLUMN "tool_resources"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool_resources"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_resources"."name" IS '资源名称';
COMMENT ON COLUMN "tool_resources"."engine" IS '执行引擎';
COMMENT ON COLUMN "tool_resources"."resources" IS '资源配置';
COMMENT ON COLUMN "tool_resources"."internal" IS '是否为维护模式资源';
COMMENT ON COLUMN "tool_resources"."creator" IS '创建者';
COMMENT ON COLUMN "tool_resources"."updater" IS '更新者';
COMMENT ON TABLE "tool_resources" IS '工具资源表';
CREATE TABLE IF NOT EXISTS "tool_resources_model" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "tool_name" VARCHAR(255) NOT NULL,
    "version" VARCHAR(255),
    "resources" JSONB NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "health_check" INT NOT NULL DEFAULT 0
);
COMMENT ON COLUMN "tool_resources_model"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources_model"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources_model"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources_model"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool_resources_model"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_resources_model"."version" IS '支持健康检查的工具版本';
COMMENT ON COLUMN "tool_resources_model"."resources" IS '资源模板';
COMMENT ON COLUMN "tool_resources_model"."status" IS '是否必填 1:必填 0:非必填';
COMMENT ON COLUMN "tool_resources_model"."health_check" IS '是否支持健康检查 1:支持 0:不支持';
COMMENT ON TABLE "tool_resources_model" IS '工具资源模板表';
CREATE TABLE IF NOT EXISTS "variables" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "value" JSONB NOT NULL,
    "type" VARCHAR(255),
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_variables_name_4779ca" ON "variables" ("name");
COMMENT ON COLUMN "variables"."ctime" IS '创建时间';
COMMENT ON COLUMN "variables"."utime" IS '更新时间';
COMMENT ON COLUMN "variables"."deleted" IS '删除标识';
COMMENT ON COLUMN "variables"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "variables"."name" IS '变量名';
COMMENT ON COLUMN "variables"."value" IS '变量值';
COMMENT ON COLUMN "variables"."type" IS '变量类型';
COMMENT ON COLUMN "variables"."updator_id" IS '更新人';
COMMENT ON TABLE "variables" IS '全局变量模型，包含以下属性：变量名、变量类型、变量值(必须是JSON格式）、更新人';
CREATE TABLE IF NOT EXISTS "account_role" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_account_rol_account_a7676b" ON "account_role" ("account_id", "role_id");
CREATE TABLE IF NOT EXISTS "account_star_room" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_account_sta_account_ed3ab3" ON "account_star_room" ("account_id", "room_id");
CREATE TABLE IF NOT EXISTS "playbook_tag" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "tag_id" VARCHAR(36) NOT NULL REFERENCES "tag" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_playbook_ta_playboo_34329c" ON "playbook_tag" ("playbook_id", "tag_id");
CREATE TABLE IF NOT EXISTS "playbook_scene" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "scene_id" VARCHAR(36) NOT NULL REFERENCES "scene" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_playbook_sc_playboo_63ed52" ON "playbook_scene" ("playbook_id", "scene_id");
CREATE TABLE IF NOT EXISTS "approve_accounts" (
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE,
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "approve_accounts" IS '审核人员';
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_approve_acc_approve_13ec1c" ON "approve_accounts" ("approvement_id", "account_id");
CREATE TABLE IF NOT EXISTS "room_accounts" (
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE,
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_room_accoun_room_id_74467c" ON "room_accounts" ("room_id", "account_id");
CREATE TABLE IF NOT EXISTS "tenant_member" (
    "tenant_id" VARCHAR(36) NOT NULL REFERENCES "tenant" ("id") ON DELETE CASCADE,
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "tenant_member" IS '工作空间成员';
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_tenant_memb_tenant__366a8b" ON "tenant_member" ("tenant_id", "account_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
)
