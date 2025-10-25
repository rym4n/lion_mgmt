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


MODELS_STATE = (
    "eJztXWuP2za6/iuBP/UAs11ZdxeLBaZtejanSROk0z2LbQNDF2rGJ7bslexpB0X++yGpGy"
    "VTEiWLpDzDD00TSSal5+XlfR++lz8Xu30ItunXd9794ptXfy68wwH+P7+6uHm1iL0dqK7g"
    "5+DVo+dv8eVj9u9NHII/QAqv/PoJ/nPnxd49COE/49N2Cy94fnpMvOAIr0TeNgXw0uHzOt"
    "qAbYi7LXrZhKi1U7z5zwn9+5ic0KMhiLzTFv148bfoFAfHzT5+lb+Q76Xg679tvZ0fen//"
    "e/UuYfVz9A75+xadh/462G9Pu7jqNNwH8B038X311vcgBol3xG0Vv8TvvD4+HfD7fvfgJT"
    "/gr4C3gn2MvnITH1P8UTvvj/UWxPfHB/hPw/6CvyQNks0BfUDVzeHp+LCPyzZhG4vsFau+"
    "sgbR3X/efvzuH7cfvzLs/0JP7SGqmSR+yu/o+NaXL41PWvx2cqNQg3+6ZvDbyXIM77eTbd"
    "sR/NPTl+iK66MWo1wulTD1HXGlktVh6z35+/3ntFtk+UeeSeX4kOxP9w9kS+t8NNEExiYd"
    "CCMcFeCYCef25+9uv3+NWlyTD+cvUJfkOy9+utujP7E830BRenEA0I/xSFs3JsKH/J0pgm"
    "8VdLRPfveScP0ZPNU+OxuAjWFA6cf3gs9kAxCu/LcJ2KKPK18S3kkXxTgvXi7/9C9IqHt9"
    "3xBz6B09qpzhdNuBmoxLofQK2Tsd9+t4/zvxo77ZWHY3akJ+D++iBlonZQK88H28fSrAOJ"
    "MVnCiWvvThn/Dr4OSwIvu308qKTIqMwry3r4u/LIhvXnshgUTLdL578+71z3e37z7ggZam"
    "/9nir7i9e43u6PjqU+PqV82pXzby6n/f3P3jFfrnq3+//wmP/MM+Pd4nuMfqubt/4wWikv"
    "FpGhkzLrgn+SKGC5+JhOtrL0XE2boY9gi5uFJJmXXeEu2PEuu3+/0WeHGbVFsmqg7lt7Jt"
    "JEtXc+Du5gc2RYpwBd12bKrfvn//tiadb9/cNXbXX959+/rjV0ssFvjQJtti3vx014AZ/3"
    "/gRGKFuGibs6qiWxZ9zmCEHd8BEHlTC+HfV5FGQZtNg4HdtKow+F4dWNw4J2CLtkcB+yY+"
    "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"
)
