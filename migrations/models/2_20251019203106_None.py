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
CREATE INDEX IF NOT EXISTS "idx_account_ctime_14704e" ON "account" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_account_utime_f6462a" ON "account" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_action_perm_ctime_b175f5" ON "action_permissions" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_action_perm_utime_850ea0" ON "action_permissions" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_audit_log_ctime_fba851" ON "audit_log" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_audit_log_utime_204f58" ON "audit_log" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_config_ctime_73e03e" ON "config" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_config_utime_34b4ca" ON "config" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_event_relat_ctime_187d61" ON "event_related_script" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_event_relat_utime_8da293" ON "event_related_script" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_events_inge_ctime_01d0ab" ON "events_ingestion" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_events_inge_utime_1ac87b" ON "events_ingestion" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_permissions_ctime_5e524f" ON "permissions_menu" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_permissions_utime_35bc56" ON "permissions_menu" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_playbook_ctime_e8fdb9" ON "playbook" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_playbook_utime_fd4ee7" ON "playbook" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_playbook_ve_ctime_4c93f1" ON "playbook_version" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_playbook_ve_utime_539931" ON "playbook_version" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_approvement_ctime_cba21e" ON "approvement" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_approvement_utime_f51a54" ON "approvement" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_role_ctime_313ff5" ON "role" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_role_utime_e9d879" ON "role" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_playbook_pe_ctime_17e36b" ON "playbook_permissions" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_playbook_pe_utime_21bd8b" ON "playbook_permissions" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_room_ctime_ab8bd8" ON "room" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_room_utime_175a0b" ON "room" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_room_msg_ctime_7b2b20" ON "room_msg" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_room_msg_utime_71a677" ON "room_msg" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_room_msg_ac_ctime_06a754" ON "room_msg_account" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_room_msg_ac_utime_0bb763" ON "room_msg_account" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_scene_ctime_4ccfdc" ON "scene" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_scene_utime_3fb86d" ON "scene" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_events_ctime_26c029" ON "events" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_events_utime_7eff22" ON "events" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_tag_ctime_263ed5" ON "tag" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_tag_utime_ee2f5f" ON "tag" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_tool_ctime_40e216" ON "tool" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_tool_utime_222712" ON "tool" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_tool_action_ctime_cc0593" ON "tool_action" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_tool_action_utime_1c3794" ON "tool_action" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_timed_ctime_ee353c" ON "timed" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_timed_utime_91bc4f" ON "timed" ("utime");
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
    "playbook_name" VARCHAR(36),
    "playbook_version_id" VARCHAR(36) REFERENCES "playbook_version" ("id") ON DELETE SET NULL,
    "timer_id" VARCHAR(36) REFERENCES "timed" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_runlog_run_id_b706a0" UNIQUE ("run_id", "node_id", "node_name", "resource_name")
);
CREATE INDEX IF NOT EXISTS "idx_runlog_ctime_7c5f78" ON "runlog" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_runlog_utime_8e8b48" ON "runlog" ("utime");
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
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS "idx_tool_resour_ctime_95d686" ON "tool_resources" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_tool_resour_utime_706e69" ON "tool_resources" ("utime");
COMMENT ON COLUMN "tool_resources"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_resources"."name" IS '资源名称';
COMMENT ON COLUMN "tool_resources"."engine" IS '执行引擎';
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
    "version" VARCHAR(255),
    "resources" JSONB NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "health_check" INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_tool_resour_ctime_5e2f7c" ON "tool_resources_model" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_tool_resour_utime_a35d7e" ON "tool_resources_model" ("utime");
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
CREATE INDEX IF NOT EXISTS "idx_variables_ctime_c05d5a" ON "variables" ("ctime");
CREATE INDEX IF NOT EXISTS "idx_variables_utime_ef4175" ON "variables" ("utime");
CREATE INDEX IF NOT EXISTS "idx_variables_name_4779ca" ON "variables" ("name");
COMMENT ON COLUMN "variables"."ctime" IS '创建时间';
COMMENT ON COLUMN "variables"."utime" IS '更新时间';
COMMENT ON COLUMN "variables"."deleted" IS '删除标识';
COMMENT ON COLUMN "variables"."name" IS '变量名';
COMMENT ON COLUMN "variables"."value" IS '变量值';
COMMENT ON COLUMN "variables"."type" IS '变量类型';
COMMENT ON COLUMN "variables"."updator_id" IS '更新人';
COMMENT ON TABLE "variables" IS '全局变量模型，包含以下属性：变量名、变量类型、变量值(必须是JSON格式）、更新人';
CREATE TABLE IF NOT EXISTS "account_star_room" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_account_sta_account_ed3ab3" ON "account_star_room" ("account_id", "room_id");
CREATE TABLE IF NOT EXISTS "account_role" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_account_rol_account_a7676b" ON "account_role" ("account_id", "role_id");
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
