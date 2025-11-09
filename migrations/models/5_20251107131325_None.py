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
COMMENT ON COLUMN "account"."creator" IS '创建人';
COMMENT ON COLUMN "account"."expiration" IS 'Token过期时间';
COMMENT ON TABLE "account" IS '账号模型，包含以下属性：用户名、姓名、邮箱、手机号、密码、OAuth ID、是否禁用、角色';
CREATE TABLE IF NOT EXISTS "action_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "action_name" VARCHAR(255) NOT NULL,
    "action_description" VARCHAR(255) NOT NULL,
    "tool_name" VARCHAR(255) NOT NULL,
    "roles" JSONB NOT NULL,
    "users" JSONB NOT NULL
);
COMMENT ON COLUMN "action_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "action_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "action_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "action_permissions"."action_name" IS '动作名称';
COMMENT ON COLUMN "action_permissions"."action_description" IS '动作描述';
COMMENT ON COLUMN "action_permissions"."tool_name" IS '工具名称';
COMMENT ON COLUMN "action_permissions"."roles" IS '有权限执行的角色';
COMMENT ON COLUMN "action_permissions"."users" IS '有权限执行的用户';
COMMENT ON TABLE "action_permissions" IS '动作权限表';
CREATE TABLE IF NOT EXISTS "audit_log" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "username" VARCHAR(32) NOT NULL,
    "display_name" VARCHAR(32),
    "result" VARCHAR(20) NOT NULL,
    "client_ip" VARCHAR(50) NOT NULL,
    "client_ua" VARCHAR(255) NOT NULL,
    "uri" VARCHAR(255),
    "request_data" JSONB
);
CREATE INDEX IF NOT EXISTS "idx_audit_log_usernam_b6341e" ON "audit_log" ("username");
CREATE INDEX IF NOT EXISTS "idx_audit_log_client__41928e" ON "audit_log" ("client_ip");
COMMENT ON COLUMN "audit_log"."ctime" IS '创建时间';
COMMENT ON COLUMN "audit_log"."utime" IS '更新时间';
COMMENT ON COLUMN "audit_log"."deleted" IS '删除标识';
COMMENT ON COLUMN "audit_log"."username" IS '用户名';
COMMENT ON COLUMN "audit_log"."display_name" IS '用户姓名';
COMMENT ON COLUMN "audit_log"."result" IS '结果';
COMMENT ON COLUMN "audit_log"."client_ip" IS '客户端IP';
COMMENT ON COLUMN "audit_log"."client_ua" IS '客户端UA';
COMMENT ON COLUMN "audit_log"."uri" IS '请求URL';
COMMENT ON COLUMN "audit_log"."request_data" IS '请求数据';
COMMENT ON TABLE "audit_log" IS '操作审计记录';
CREATE TABLE IF NOT EXISTS "config" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "key" VARCHAR(255) NOT NULL UNIQUE,
    "value" VARCHAR(4000) NOT NULL,
    "type" VARCHAR(255),
    "module" VARCHAR(255) NOT NULL
);
COMMENT ON COLUMN "config"."ctime" IS '创建时间';
COMMENT ON COLUMN "config"."utime" IS '更新时间';
COMMENT ON COLUMN "config"."deleted" IS '删除标识';
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
    "condition" TEXT,
    "event_ingestion_id" VARCHAR(255),
    "topic" VARCHAR(255),
    "script_id" JSONB,
    "event_name" VARCHAR(255),
    "status" INT NOT NULL DEFAULT 0,
    "numbers" INT,
    "fields" JSONB
);
COMMENT ON COLUMN "event_related_script"."ctime" IS '创建时间';
COMMENT ON COLUMN "event_related_script"."utime" IS '更新时间';
COMMENT ON COLUMN "event_related_script"."deleted" IS '删除标识';
COMMENT ON COLUMN "event_related_script"."condition" IS '执行条件';
COMMENT ON COLUMN "event_related_script"."event_ingestion_id" IS '事件接入id';
COMMENT ON COLUMN "event_related_script"."topic" IS '主题名';
COMMENT ON COLUMN "event_related_script"."script_id" IS '剧本id';
COMMENT ON COLUMN "event_related_script"."event_name" IS '自定义事件名称';
COMMENT ON COLUMN "event_related_script"."status" IS '是否开启联动执行';
COMMENT ON COLUMN "event_related_script"."numbers" IS '去重数量';
COMMENT ON COLUMN "event_related_script"."fields" IS '去重字段';
COMMENT ON TABLE "event_related_script" IS '事件关联剧本表';
CREATE TABLE IF NOT EXISTS "events_ingestion" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "event_ingestion_name" VARCHAR(255),
    "event_ingestion_describe" VARCHAR(255),
    "event_source_type" VARCHAR(255),
    "transforms_type" VARCHAR(255),
    "enabled_status" INT,
    "linked_execution" INT,
    "syslog_config" VARCHAR(255),
    "file_path" VARCHAR(255),
    "vector_name" VARCHAR(255),
    "config_info" JSONB,
    "vector_config" JSONB,
    "vector_config_raw" JSONB,
    "enabled_health_status" VARCHAR(255),
    "execution_health_status" VARCHAR(255),
    "convert_health_status" VARCHAR(255),
    "retention_days" INT DEFAULT 7,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
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
COMMENT ON COLUMN "events_ingestion"."creator_id" IS '创建人';
COMMENT ON COLUMN "events_ingestion"."updator_id" IS '更新人';
COMMENT ON TABLE "events_ingestion" IS '事件接入列表';
CREATE TABLE IF NOT EXISTS "permissions_menu" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "model" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "Permissions" JSONB,
    "url" JSONB,
    "bp" VARCHAR(32)
);
COMMENT ON COLUMN "permissions_menu"."ctime" IS '创建时间';
COMMENT ON COLUMN "permissions_menu"."utime" IS '更新时间';
COMMENT ON COLUMN "permissions_menu"."deleted" IS '删除标识';
COMMENT ON TABLE "permissions_menu" IS '权限菜单';
CREATE TABLE IF NOT EXISTS "playbook" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "remark" TEXT NOT NULL,
    "internal" BOOL NOT NULL DEFAULT False,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_playbook_name_1a146f" UNIQUE ("name", "deleted")
);
COMMENT ON COLUMN "playbook"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook"."deleted" IS '删除标识';
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
    "title" VARCHAR(50) NOT NULL,
    "run_id" VARCHAR(20) NOT NULL,
    "content" TEXT NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "approve_type" INT NOT NULL DEFAULT 0,
    "expire_time" TIMESTAMPTZ NOT NULL,
    "audit_node" VARCHAR(36) NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "params" JSONB NOT NULL,
    "results" JSONB NOT NULL,
    "flow_id" VARCHAR(36) NOT NULL REFERENCES "playbook_version" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_approvement_title_2bd859" ON "approvement" ("title");
COMMENT ON COLUMN "approvement"."ctime" IS '创建时间';
COMMENT ON COLUMN "approvement"."utime" IS '更新时间';
COMMENT ON COLUMN "approvement"."deleted" IS '删除标识';
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
COMMENT ON COLUMN "approvement"."flow_id" IS '关联剧本';
COMMENT ON TABLE "approvement" IS '人工审核模型';
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
CREATE TABLE IF NOT EXISTS "playbook_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "permissions" JSONB,
    "pb_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "roles_id" VARCHAR(36) REFERENCES "role" ("id") ON DELETE CASCADE,
    "users_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_permissions"."pb_id" IS '剧本';
COMMENT ON COLUMN "playbook_permissions"."roles_id" IS '角色';
COMMENT ON COLUMN "playbook_permissions"."users_id" IS '用户';
COMMENT ON TABLE "playbook_permissions" IS '剧本权限表';
CREATE TABLE IF NOT EXISTS "room" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(50) NOT NULL,
    "run_id" JSONB,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room"."ctime" IS '创建时间';
COMMENT ON COLUMN "room"."utime" IS '更新时间';
COMMENT ON COLUMN "room"."deleted" IS '删除标识';
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
    "account_id" VARCHAR(255) NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "message_id" VARCHAR(36) NOT NULL REFERENCES "room_msg" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room_msg_account"."ctime" IS '创建时间';
COMMENT ON COLUMN "room_msg_account"."utime" IS '更新时间';
COMMENT ON COLUMN "room_msg_account"."deleted" IS '删除标识';
COMMENT ON COLUMN "room_msg_account"."account_id" IS '用户';
COMMENT ON COLUMN "room_msg_account"."status" IS '阅读状态，0代表未读，1代表已读';
COMMENT ON COLUMN "room_msg_account"."message_id" IS '消息';
COMMENT ON TABLE "room_msg_account" IS '消息与用户关系表';
CREATE TABLE IF NOT EXISTS "scene" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "remark" VARCHAR(255) NOT NULL,
    "status" INT NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "scene"."ctime" IS '创建时间';
COMMENT ON COLUMN "scene"."utime" IS '更新时间';
COMMENT ON COLUMN "scene"."deleted" IS '删除标识';
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
    "event_name" VARCHAR(255) NOT NULL,
    "event_process_status" VARCHAR(50) NOT NULL,
    "event_source" VARCHAR(255),
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
COMMENT ON COLUMN "events"."event_name" IS '事件名称';
COMMENT ON COLUMN "events"."event_process_status" IS '事件处理状态';
COMMENT ON COLUMN "events"."event_source" IS '事件来源';
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
    "name" VARCHAR(255) NOT NULL,
    "type" INT NOT NULL
);
COMMENT ON COLUMN "tag"."ctime" IS '创建时间';
COMMENT ON COLUMN "tag"."utime" IS '更新时间';
COMMENT ON COLUMN "tag"."deleted" IS '删除标识';
COMMENT ON COLUMN "tag"."name" IS '标签名称';
COMMENT ON COLUMN "tag"."type" IS '0为剧本，1为工具';
COMMENT ON TABLE "tag" IS '运行场景模型';
CREATE TABLE IF NOT EXISTS "tool" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
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
    "tool_name" VARCHAR(255),
    "version_number" VARCHAR(50),
    "action" JSONB,
    "playbook" JSONB
);
COMMENT ON COLUMN "tool_action"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_action"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_action"."deleted" IS '删除标识';
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
CREATE INDEX IF NOT EXISTS "idx_runlog_playboo_83741c" ON "runlog" ("playbook_name");
COMMENT ON COLUMN "runlog"."ctime" IS '创建时间';
COMMENT ON COLUMN "runlog"."utime" IS '更新时间';
COMMENT ON COLUMN "runlog"."deleted" IS '删除标识';
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
    "tool_name" VARCHAR(255) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "engine" VARCHAR(255),
    "resources" VARCHAR(3000) NOT NULL,
    "internal" BOOL NOT NULL DEFAULT False,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
COMMENT ON COLUMN "tool_resources"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources"."deleted" IS '删除标识';
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
    "tool_name" VARCHAR(255) NOT NULL,
    "version" VARCHAR(255),
    "resources" JSONB NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "health_check" INT NOT NULL DEFAULT 0
);
COMMENT ON COLUMN "tool_resources_model"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources_model"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources_model"."deleted" IS '删除标识';
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
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "value" JSONB NOT NULL,
    "type" VARCHAR(255),
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_variables_name_4779ca" ON "variables" ("name");
COMMENT ON COLUMN "variables"."ctime" IS '创建时间';
COMMENT ON COLUMN "variables"."utime" IS '更新时间';
COMMENT ON COLUMN "variables"."deleted" IS '删除标识';
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
CREATE TABLE IF NOT EXISTS "playbook_scene" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "scene_id" VARCHAR(36) NOT NULL REFERENCES "scene" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_playbook_sc_playboo_63ed52" ON "playbook_scene" ("playbook_id", "scene_id");
CREATE TABLE IF NOT EXISTS "playbook_tag" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "tag_id" VARCHAR(36) NOT NULL REFERENCES "tag" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_playbook_ta_playboo_34329c" ON "playbook_tag" ("playbook_id", "tag_id");
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
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_room_accoun_room_id_74467c" ON "room_accounts" ("room_id", "account_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "eJztXWuP20aW/StGf8oCvRmKbw0GA3QSZ8cbOw7sTnYwcSDwUezWWq8hpU4agf/71C2+im"
    "SRYlGkqqSuD2k4ZF2KPPW8577+vFlvQ7RKvr5D8TJ4vPnrqz9vNt4a4X/U7ty+uvF2u/I6"
    "XNh7/oo09co2frKPvWCPr0beKkH4UoiSIF7u9svtBl/dHFYruLgNcMPl5qG8dNgs/31Ai/"
    "32Ae0fUYxv/PobvrzchOgPlOT/u/u8iJZoFVZedRnCb5Pri/3zjlx7s9l/TxrCr/mLYLs6"
    "rDdl493z/nG7KVovN3u4+oA2KPb2CB6/jw/w+vB22XfmX5S+adkkfUVKJkSRd1jtqc/tiU"
    "Gw3QB++G0S8oEP8Cv/rc9Mx3QN23RxE/ImxRXnS/p55benggSBH+9vvpD73t5LWxAYS9ye"
    "UJzAKzXA+/bRi9noUSI1CPGL1yHMAevCML9QglgOnJFQXHt/LFZo87CHAa5bVgdmv9x9+P"
    "Yfdx++wq3+C75miwdzOsZ/zG7p6T0AtgQSpgYHiFnzywRwpmk9AMStWgEk96oA4l/co3QO"
    "VkH834/vf2SDSInUgPx5gz/w13AZ7G9frZbJ/jc5Ye1AEb4aXnqdJP9e0eB99e7un3Vcv3"
    "37/huCwjbZP8TkKeQB32CMYcmMPlOTHy74XvD5dy8OF407W33b1rZ5a62v61e8jfdAsIIv"
    "hu/LN5Eg2B42e+b+kt3q3mCoRsd2mJtPBzfU7U8Hy4icTwfb02f4347rfzpEkRbAdc3Cf0"
    "0dXzERsuCvhv9tBRbC7TXdgZYz79PBsXQXX9ENB9pr4aeDoWnwtHloVK/MNQ/LOr4/y6/Y"
    "uomfaTuGl79JJusH+N0cV8tavr877B9fvfmukLP1iLwdtJq7s/wt0rvuPNTxX93Rb2pj7A"
    "V99sgnhfZ1mnlUGLhMHz8r3PwtOmwCGMWvsrnhewn6+m8rb+2H3t//fjPWCm7YPRZww25d"
    "v+FW98ECfwX+RwPp7zBecKdlQc+FaoCHmdTX+T/OupzjeWXpM5gnuB0ep1aER+jcisz6BG"
    "xBPkZe+H6zes56vAP5+zfvXn+8v3v3U2Xl/+7u/jXcISN//Vy7+lW9l4qHvPq/N/f/eAX/"
    "++pf7398Xd8ginb3/7qBd/IO++1is/194YXU4Myv5nBV9uzDkE4+yNrJth2Z0L2+9oI7OX"
    "v5so/xMoQA/0Yvf7PdrpC3YXcyJVXrZh+LTdWzrccBS9dxp85tGzrY1fCe5OLdqF/XdnTl"
    "N+/fv6304jdv7mtL5s/vvnmNz8Kk+3Cj5T5VTTPFjJpKCYrJvzk2J1rmXFvUmHqEoffZhf"
    "T2XUivKxHhMtmtvOcFL5J1uUFoZlhdl16L1t5yxQNlITAKhucdkDOrl2JrdSi2VkOx3WEU"
    "uAZjIXCRCPYCsAO/Bnxekvy+jblO7bTMZVIsk8zldeQt0IZ8It9uXhUUvqGPwa+MuHUDOp"
    "/RM88ApUQucqvptU52LJPNVRIQ8fHrDxiZuZgal1VIt/hY/7jg4ztomQvcfiZZNvHhED6O"
    "WwWixNTQrG3qKF4v94sll82kInSRy+Ykw5NAwAFj3v58ByOiqI7GXU4CYhAjb7+NeXCkRM"
    "QOxho/aSLf60laTayNoz92SwwA077czRFWJUcgCscD+377GW0+HdwoABuLM4v4ycILIQdz"
    "RBoUMIdJsTHDFugJAQzNvTST//6HD2jV1vWZVeR18Qzp59iXfGjnV28YHOthB0M5vFxsaO"
    "p8ZGzIuCmwWeDnoYS9pPCj9IZ+mCh70qRj6UrwmnB8rZOHRYwCtHxCjO2fB6N3KEm8h/Ob"
    "rUIXbPWaDTuR4VmAk2ETtOYj45Qg/H6Xj5JlhMRzgXgdjItSvlqBNQFreZ9Pw+on6ilXsj"
    "zlwCyINpeAJ2GyyPWD05H6qXzqmbfA0mVmwuF04tkgR+mX0oXzSoZVvutdB1BnOE7F2+36"
    "NIw+ZE84KzC6EQEkhj/22MGaydkgGfXkTRAB3RPPrJlrgWYa4lnmumaA/waaAb4OIb7u2K"
    "458TKVBPjDT5x6H+EZV7gyXSw2Z1iMgMRh0esc0Nznj7iyYXOp0Ew4ap68eAmvnJw4m37J"
    "n3Ph2Jzotl8CG29XrBXqnbd5vt/CX0LWvsG/720Clg2h2AZXg1apqW2FbQjeZm++qAUm5N"
    "8Rw1DCc7GI9UljEVKotjFBGaz5GYSZ+bTogOxWJpXd3T/G28PDI30jzn4OA5q6T8LNb+8+"
    "fnv3HaFuFw08K9MC90p6hhmnC4cd7uTrwvQ7uLpwux7ahUUfnNCP3m4XbyHIboRuvEuftU"
    "bN4MYJexN2Rt8DcsU13HSJg8AOyz1toWvr49pHNrq6gme1r71SlLvLU9EFPZJ69XhjP1Nz"
    "tt+cBZZorAlLZIZ13ZEwN0CHZqGYAW/1RrfdoW/QnCbMSPseUXCW7sH8i6wATJWmAS7wlg"
    "bKod2Yiz2aq6grFXWloq5U1NVkytCL7WQVdSXGDzHbWnnDhWpigkMMajt3Gp3tzCOt5yQ6"
    "Q3KMFC/6tfnRrknLBbptBBFQ7xKBvsdTnntkV4QkgDiELAXWzHGkHNcthFl7xpJCQFS+kp"
    "tff2seZ2EXdrR59dBv67aTm5FS01F75gnuJXy07Cb1EFqu3igELrE3BhryXkSuGYoQYqnf"
    "Vb6oQ/GuNeyhcWdcV7psURxYmYyFoXcfF/q0gTsWeBz7vpVmaVEaudLIlUauNHKlkSuN/H"
    "o08v1yzwpP79BYcoGptJVBNq8U3Lk772nzmjq8Oj5sOCOBSwkJ1MCZA25smmUS9zYtPQm/"
    "+W6QItgHW70dW71/5sp79EdLBtr2zJUi0KXHbcFj5O6Els/pIM9c61//875bwyiW+rfvf/"
    "yfvHld7ajinuy9/YGh8LXm/S0Fjuf+HQt07Rjiju5DsAYJRoA0jcQnCBmp1Q03jUgnzDVQ"
    "+UzNThvNqo3mGiRtTEMS4bZevW3roLg7KAx7TphRkg43PBoW7Ejl1u6qi8nUaYHj07k1Z3"
    "XAZ5DE0ncQsz/MNMcmvi2mP0h0LVoMObTWRGU7utJ99JIjdCuz7xAu9/huyGfiqEjJtUm5"
    "ugvrmebPhx0BxtL6m47MHuMUcETVr0hKPZ/myM5j51IK8iXPqp0XeyyvrXa2uZSQjW52Zw"
    "j4R39O3NjNOfQy7Fm25fS0tZyb6se38CfxmV5KEWH4//mFiT99unN9oF2syLLkRD5a4XnC"
    "p0lSIhLsI5QqaRGjiu3ogbhdpGEyqULdxPn7bYyWD5sfUF+3UNFBnycA3uE5Gnu/F0YNeo"
    "Qx/Ti/TGaHmshtuyx2cKUu2+UHcrlrV9x5G86+DWfuMdy1062/294IZ+W324cblrExv3fb"
    "aWkkp+1V1qyHndE2g7Bw+CEd52Z/27aP3kLKpqhsisqmqGyKyqaobIrXYVOUo7bCCZbFej"
    "mmQeqKKrbQC96iwpUcIKe0BQ+8pYR4bdtBgKbtzAdp2BOYaldLoh1w5R2uCAlfC/DBXc8H"
    "q+Oh6M1PQ6Ad38MgQ+ngDYA2FRI/XOvY/nw3aNhO4Wp+iJdc21faXPjS6vqkFGFg6j9/eC"
    "sNmDHCX5bsF6Bh8XHIVbkRiORpwE65e3DnsHvamF+Qe/i32020ZJI12Z3bLqomKNv04GlS"
    "z4z0gDGfmRDAEtmoNQL7WHPFzShuRnEziptR3IziZq6Dm+EsnHVK0awRbWX03jx3nZ4Osm"
    "c42T55qwNjZXq9CeLnHUamHdlCUrwaRqNracYg7sDUtD4qLjRrxTe9KbDazpjKAQ1q6XMs"
    "zcDF574DX+BHKSF+xNq6CRbUwEJl1KbVMzB3InAlUbZI4YgPqY3/IwHthqF4MVrddilhpL"
    "jFIncdSEqJXiG64LVtIjjosJ1TWtUzHlGlqilVTalqSlVTqppS1a5DVcM/Gi7ZyZs6Ix1L"
    "IeGHYDqNiu3Ys3QzPxnkScIb0zNOUb+L0+maLS28A+gTVFqsyprZ1nKQsX2irFm7ZcCl4O"
    "UCEmBr+GnM+XAHhkkwTd+TOYDbbW0VIckMbeV5v+/IPXeYRjr9eT1zqlLCB3QzLqmigMmX"
    "D+5yI9JtW4fCeKYOwEaaRv4dlfqt59KbZ0/ARw5b3hzWPjO+ohVeSmIQvqOuGYYPq/MMXP"
    "BT4zz+dyQGyUwb5liNSwnZlmIK1jJDmjyLskw0XPp5LOrtSAb6si4xN8Gmz5x+dFqloSLP"
    "FHmmyDNFninyTJFn10GeCVPIRp1CkutfKVy7eBugJFm0aWPH4G7KC3f6r+BOZYs4zZA/fh"
    "BACmCyPcTBgHFeygmnHio8pWNb+C+ayzPQt79vWBXT2/W3QkCQ+kadfP3DcrVfbpKv4fcY"
    "R14gfkJI2uWGupGnIogiDVhNQ4Oe0FFAZ6OHpcgNqmFcuP3pWQsnZOaSw3rtxQzXt3ZLSk"
    "NQrkkyh5xc7tztuRKd25qy2j60hHu0I07LnG/vZU0VmKDsqWJbJGt8FDqjxnwoi9apFq00"
    "8yOkeYQUG+l2zW7L5keE7S3BapsMSw5ZlRxB/RuVKCT+WnMLhS87cR1JOLiNOSdfVUr4pB"
    "tWeH3qLJBUIdK+uFIi4kGlPBrxKSri4kcmhjYJMA6c2NIyUoFrOQbwjLbd1/wzMbiHXThg"
    "SahKCQeYZvfELgkNk09tGDdB5s7p9zF/zoWM4C/54DmSyI+esuxMfvX1dgQwe9bNlmWp7Y"
    "sltbUchzKbzCOg2T93ofjZ3xfK6lJ3HM3stHSxaA47XvVFs3qWPHPCzh5W8je5innTai4v"
    "m9wet5uXOitpzWlBp/XE3tb0diFlWVeWdWVZV5Z1ZVlXlvVrsqyXvPAwG3tTXrhG27qhy2"
    "p4LzFMv8U/qR/oZ0jbFxLWfadt6QvedAVMYbnQL6zxdBkuadDHv7ZJom28TrixZ4gKR96N"
    "7ICYFHUp0UYb+Oaw1dWnNTKgKSg8QKAtBMNEGpydUVqeMV96enbByIEDq+XmM0YNf1twYE"
    "eCtiLOEpUWc5nCXpLnBBwRyryDve0fdUHBq0n6PmlmVfDlQYY060i0XKHFzsM/wIFvRUj4"
    "Sk3n97Et18n3SzckaEfuIEveNEmqUAD8H+9BvSYmGPH0baQ8jaczHh+lo20T33YfwZqYKE"
    "/BliJp9EmEHutmhKBskcZpijqbv182atuW7/buaAhK1SH58L/YrljEHqO6Ws/uyIWl6hIS"
    "kU7qQId+nuSikkqPylJF67BQzPMSuzM/wz8ib7V/HOLu3/YA4Zs5+8wP2RDJX+Q7UibxKw"
    "73J/RJ+yOE9wpbK5C+V/AvPqF4P7xPWh8gvkdaDgWS90iM9miTkpzeMw9t0RQ8mwrtMDec"
    "wuMctoswjcGHQI25PuepHzy2zizMyXUy26k8Xq7CvAUnM1lK6y4owo9IghE8piORaCc3CQ"
    "btmF5uYvyy3qEk8cgW0fDHym/ddvlhEVfIddK7aktaItfWZy6pCQWYh66b6kJlguemHYRT"
    "VPliKV8s5YulfLGUL5byxboOXyz8o6CgNmHuTBCci0igFRDDWUaRUvu2NXOBT/D905MKTB"
    "JZvVzjM9BiAPoNQfF9YNkRKVFiOpfVB9zuPqf4+Iw76imUSyefNIB9d/BXSwhjj+As62qm"
    "t4uXT/jFwOYZzNJLf0mekz1ag3Tkk6q/0V+8zQarSAFa44FF6GsgSk1zkKI9fiHgZbKIUe"
    "CtVtx7Qk1Shn2B9mKxTegnyw5PzwEx4r6AQdstN5shYJdyskFdlP+y8/w0aQYaE7konzDl"
    "uB8pJ82InYKHMVo+IV4mryYm1/KVGstsy7DJGXhQYbarSAwwHcJpqSsny7WUha7mM8DVIR"
    "2NiSA8uNwyeMb+5JkEEAaAd8xXhOTqD8sIZ1A4WJuJHvFdce8EvwsmXk8GvHcEPD3SeoTA"
    "Z4vxdUA7bPXuHRBf3bhkyy9wsSv2aAkJ2o0LFO2fPCy8cqTWzpKZ8Pc/kFqCbC/zquFA+K"
    "g/DeMvZzC75BC1W18oEI8bYeju62OMoYeyiTRUTfSYZtJI9c6WwHj+ByjDjDLMKMOMMswo"
    "w4wyzFyHYSbbcznV3qqUeL233LcHsciq5FclYMyGZNquTxj7wkc2Zf2JfxEy0hNREVdAms"
    "LtWfW2FUZ6ertnt4zs7blOT6Gcg7sqJX5w86oEZ6Zw1qWj1Yn6MOWyJTO+fdXa6jiSyW3u"
    "JxSvl0mCEUjeoc3hhqHA1Zvcdmlwu7LxYp237qPBOSakXrYtvKy4RggGEcNqRLB3NlQamd"
    "LIlEamNDKlkSmN7Do0MrLec51Xc4FzbU7MaTNw/5lE96LfjAPImpjYIEaZ8KQOg0082yPj"
    "a2IyFQjuje65Y9cPMWPut0OcNVfQ9oDW3/EsBmnri1wDDL3PmV5vP9PrTJVfkK668p7xSe"
    "HzDUtJze/ddmqndKseWmklHUZXSFd7Q4ZW+utNnp8oPxH9phRVpagqRVUpqkpRVYrqBSqq"
    "vGn6xq1WPVBNrW3bEibpi9Haiz83gW2P1SolJDBWcea9PntA3GaP4o3HULK6400oMRlWh0"
    "p+WhL/Vk/lJtNKobLuqMww06I8ZmaYPt7GuU652HVRYzxux7kuWyPNzgv9oMWj6XtMz/0C"
    "qScUjwjTL+nTzgoRL7PVExhQHFiHVQ5U7vNHiCr7ONbIOZFJooKuvQfGSHvnbZ7vt/C35y"
    "J47z3IaEPpWO7Iiy9qRFn2GTEMJBQu6kMwRWobE4w/o+cMwGyVLNDP7hQDN729f4y3h4fH"
    "6pBOf4+5vuLriwaeVdc5KPY5Ru/1rYYqff8VH8LTg3TN1AF9WJSk5ejFXkwuvcN1kLq1jf"
    "A4v1vfi7m5XtrJiB0xcqy58kZSJK8ieRXJq0heRfJeB8nbqd+2+yXslOsHt3/CzuckyAoB"
    "GbgxPgVw+sQqK5RwZ1YpZcQnsJ+HEDqjO7ocgB4SFPMCSssIB/S0ELHJedudPwJlS/vDiL"
    "CnjcvVFstbjwTeMNLOynmLHJp9AaTnX5+kJisW6TIgq8mqzxFZ5GLZPzFJuSNIFb9VI8E7"
    "WBSKJ+/BoDxRrTnZE0c3XW6vuXYhpgcd7cmXv6ryolMEiyJYKg9RBIsiWBTBciEES/DobR"
    "7QAt68CXVHdvSqmHD1yjJCN59Jrk/mk62dnmp4Eh8w6pTT9zhAiYgnW+hTk2VETpp7A1/x"
    "wffO9vUZSc6dPEI2yiiE2jMzSOJnzUjSv5mWL3YO8jwoAW1Z+SMqiYrpn7FDKB+NvGE5KP"
    "qk6Tba03QbjTTdl5tdpXr8zbOr3JLEBuYciqAb0e0sz52S5hS1kNa3JPrIGVSiFd4yYE/e"
    "HhhpFVvRroudD3NDO4q6G4VaXqHTDV0r3/TLrT+fDZBkAmYPqR+JdUyBffD/CWvBaifgK0"
    "Ij0O+DOqO1HHFozmCka35a1QFe9lOlZmSOP2QiskzdR+EDSuCfbrDBClJy8sYyCYOv/Fyn"
    "I6BrHiu9bSRVMcHoCtbaVa3OqbDtYvYp0kogvy/O6/W2zutXp+RxZlr5tKtqp5dS7ZTKrL"
    "nbxdsnUuboRJ/3u/JJZx+5Uzh5M7f1+LBZbR9OA+pD8QzhfvC0+nwiXFNakoixjmE+yo14"
    "XVnbsxY97ESlfY52m2XYhtobKs9aOY6QyvDzgmwCyvDzAjpZGX5U+gTW1jQmgaGy/F0Ans"
    "rVe1KiuEERDY0zX+RH79OjqPmCzWV02RsvYjiraTFG3OkJvIhskafUp9RjT0uH0WrcabU4"
    "SD3yFKSaUae5TD60e8WcFv3fod5u1zdM9TatvNddlKyvemtGVkAM61DywodKeXQ1vU5nSB"
    "5RpQIrFVipwEoFViqwUoGVCizW8tzct0/LJGj18ZCz2j3krIaHXHzYMK377YpcKSFIh7v5"
    "9bfmZp+9FtmOHHZiDuX8M+J+QI6fJjL6lmy7VkeVMZVd+kyfZmt0o9AvfBADDQyIfghZHG"
    "3XlD5W9eL9MXgH+TV5YlzSuD63ywapRI6eTvfYeF08Q5AjQtkzJ9JrlcpEycMi50WGYyO8"
    "yiOgY2rAq2SjMaK8vl0gJZFFVgeImYjgbOdqpkfuzmUhK1sC3hVT2cJUbtdDmEq8GjSYSn"
    "KV5ooHJjlUdDNnJ2IU4sWYPZnLFA8ek3hOPdFY1HPho9ZBPpdt+lRRJauZ7WhzWLtcHa9a"
    "mj/P91ybuKRlO7IPNA/EezGo6KGPYUbql7okhGvQ/8x1eKxJbQ9xkF1QUfyKyVZMduUhis"
    "lWTLZisi+EyW5jWzuyzbWxrSJIvyJwvBodm279lqUTtkTT3nwnBylIVHROvGkZ4YSgiVwf"
    "/kZ2+kLiIaWOab2tMaWI+CFcnlhlGaSV0y4XpvKYuWg9QMJSWQQtAg8vxLmQXBA7geOzXY"
    "XEDOE93uy5h3BFSPhKa4VA5lkzx+E/OpxhBBOwBuSjqctJhTNv5Nu5cA6XCfjTDhvRdWGp"
    "EDeRHoLW4zpSrtMeIUW4ca+JiUdc99zC9UO+tSSDa+gobxGXCnXJx/nlZqWi6d0yK1VPYE"
    "fOd+TFrNJd7X5LeXuZIk9qkFozG9ZqI+CLdjib7xK+BR/TAL2r5mwuIXyBcKO5ARADu+ug"
    "0ICjxzy4BeICULddA3og0qI8HV5q47AMBJVq8ZWT+2SSLIVgltovhlC0VckReNpRl/NIA+"
    "PRPPD5adoLoWVzMDrJd7QJB3UuLSdZ1+aTz4peeNf+gYID06+rg56kZISvqBVjr6ZZslBq"
    "VZsxB7oNQeEQu6EJxgw01049yU6Yao8X5obgRDAPKE0ipb5Qr7swNLdhVVz42B4rI9Lk9C"
    "beInk9y2kZuYD254V3g4kinyjOfTW48/qPEwyboHM7PQuven0C5H1dm+nxdtyhnFXI5USQ"
    "+Quuy7iU9IW7ZUmtIP/x9f2rH39++1ZU9Z+0GDTDtbCoEt3uWVgUde6Vto1yBbAccIe2be"
    "I+3RXR3k9IxbIrD0DlAag8AJUHoPIAvA4PwItM53ZT3aMlVJFjtPZiRu70Lq4nlxDv3GMb"
    "4FjpRhIBehE2yvYVXifDVLfJ3ygPKYVyIbPmJShxNHdn6SUxxszryAogZ0kQVcRChfxPO4"
    "KvKfBfgkF77nh+wnZcR0B/eUw7DerxQtBzqmyMCGYBhesnC2Gmv6URwwwDkhW/XKuJUw9g"
    "TgdyI4K5kCpovZHCl+89ZuwyXL7tohf3Xu+oZUUuKnJRkYvXwzspcvEFdLIiF18iuXjStC"
    "F4Or6DpKQX2cGDrVxYS9jg2ZkwLc9PRdvEMxIsu1FEB52P92rwB5PZx5UWcooWgk/pA3QQ"
    "LNWhgWQn/7H0D+LZw9JAcpefDh2kaKK0EKWFKC3kRR1QlRbyAjpZaSFKC+HNt5N75yotZN"
    "TliA4bLpKXFMb3VBWpBMK26SvVRnnE9/l0Fz4v6v4xGYPH/7gh9GMFYYxfp6LUOja7AyME"
    "uaPwYENSsghwGvY8SDmPA4ft2+m5Ap07GjzLBMHdIXU52bqDSphCJ5W4pK65bO8tZpKJ2m"
    "5hGZaV5yKkBarslmmTRWwW0Y3E7BZ7GPYBo086gshKEfFno0qv+JYN3nJ2I0G0mM2hLS9F"
    "Z6SvLHkpKsAWeSnkADY5BAFKkgVAwQNvXU4CkGcQQa3PwfHTcAcVzJn1gXfWDu+sAS/akE"
    "/tv0yXAudbpmdMPJketrevivW5dKhtrMl5azHrcHtCjY6sC+3JNASsxKfkz5h6wcC/g+KN"
    "t+LkWGgxGUiWyvAmFYucyEajhPJOTL7kh2su00BFSPhaXfFnZB7Gh4z18f3Klc/+GXKLDE"
    "x1IeNQLtR8OSBWYRFnC4vYUVb2Ex35OZwPZBnI3EkV+oZHpNvWCKDe4zPGXfGwC9zq+kJc"
    "2eiPA6zCeVQ4z+WF86S5cMriaMPDecoibFeUOujU0J5ujyzQ1lgOWZkW1+GPlbfo4Y5VyS"
    "Lvg7XWMTWbXfW7R3PlgqVcsJQLlnLBUi5YygVLuWBJU61EQg+sAZ4+Ujn4MKvvgDE9GmQR"
    "m8DNx8MP5kA3by8c2rTYCJTqI87+pG68Kc/AxWrMdsELbkVIMMJv8btIiSz9nhzY1sSEj9"
    "9KATSVjmrQgYFdMocyJWYVRSh/PzNyilRUlu8ar2Z/TZu90uAfFurbBdPkoxpg2JJjNFMa"
    "FlQmkGYcE46LldX6iMUFyYErrdRIheujlzwunrzVgevYW5USj25xhoAXw+NXM+QpjXiFnh"
    "02qVhtGxpxTtJ82POc08s8jajHreH38H+coNNisoFecadBc5PHF+w8oGcxyn3d2bPmEnux"
    "l8kVToZ5Em/1NXOEty/ba/bQFuHekQYDIINevIUu2edLrnDUDpRZ1FusQaW9vdsmtCit/L"
    "ymITp6g9NMdExUmYyUyUiZjJTJSJmMlMnoOkxGZK8dVPJeGY+4jEeLzWHt81FATUnhWMtp"
    "PmrzCD0WjCyjAjfMBfRsSlu7T/PxUHwJ4XZQOMv97RzbrdWRw8cvh603iOsMDk2vER5UJD"
    "8b7hspvKriBO7Sk7tGfsgKDKfDgaEVlw1ujyrGcaUtp25MFxbuoQ+zmisdWOnASgdWOrDS"
    "gZUOrHRgWbIDSK4EX25yQPoIJCGw+OHLDWdWi1xCOJtQSesXzSCzkxn0tFaepbokddKuwv"
    "t6E8TPOwxSZ66hUlqucTyfmWFqjB+CtaFpfRgcaNZ+iCU3r97VJEuKg8jhRvfMIgt4pEV0"
    "l8i0GyofQOUDeOSELhxXidwdCurmHVy7OUbwpK1u+7M8i3UhcQLXky47tgNux5y8D0tUcU"
    "CKA1IckOKAFAekOCDFAclCVUjOAV14FC0dNmBpxAMY+cTNGtJT2w5gn5mQmfG20vRDB63R"
    "bsPvYjOGGPEn4zbK4+rJy8vLzdF+NKTRigIY4q7tk9BF6n8hgHHuWIi+2HPojxzM+Ii81f"
    "5xETyigOGu0op3XUwi1I8vQWkgadGM9IaJYCsoL56vNyRR03/x4iX0C9P9orx526WUP1Wa"
    "9dHEZ1CEwApMsBsYoQusa1ApA5hmyIa7mkV62Ie+gq0jjUqzAphFtqY70HLmVZ+TbvAGKZ"
    "FAX6cL7TTvQmzhV/nUnLvQFAYYLFQwjIwg5ybxq83zBxzL/3b9X6v4DsV3KL5D8R2K71B8"
    "x3XwHWIdMgbWw75pHgqk0apbslC0a9RtCSjOpk3f/Ppbc4OvQ9w7F8W59WgCF8cAztsL55"
    "HYx1dpxrGqD3C2+gAqMfiEicHHJxS+/Ad3whKF"
)
