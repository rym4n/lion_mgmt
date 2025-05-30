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
    "roles_id" JSONB NOT NULL,
    "users_id" JSONB NOT NULL
);
COMMENT ON COLUMN "action_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "action_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "action_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "action_permissions"."action_name" IS '动作名称';
COMMENT ON COLUMN "action_permissions"."action_description" IS '动作描述';
COMMENT ON COLUMN "action_permissions"."tool_name" IS '工具名称';
COMMENT ON COLUMN "action_permissions"."roles_id" IS '有权限执行的角色';
COMMENT ON COLUMN "action_permissions"."users_id" IS '有权限执行的用户';
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
    "value" JSONB NOT NULL,
    "config_type" VARCHAR(255),
    "module" VARCHAR(255) NOT NULL
);
COMMENT ON COLUMN "config"."ctime" IS '创建时间';
COMMENT ON COLUMN "config"."utime" IS '更新时间';
COMMENT ON COLUMN "config"."deleted" IS '删除标识';
COMMENT ON COLUMN "config"."key" IS '配置项';
COMMENT ON COLUMN "config"."value" IS '配置值';
COMMENT ON COLUMN "config"."config_type" IS '配置状态';
COMMENT ON COLUMN "config"."module" IS '所属模块';
COMMENT ON TABLE "config" IS '通用配置表';
CREATE TABLE IF NOT EXISTS "event_related_script" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "condition" VARCHAR(255),
    "event_ingestion_id" VARCHAR(255),
    "topic" VARCHAR(255),
    "script_id" JSONB,
    "event_name" VARCHAR(255)
);
COMMENT ON COLUMN "event_related_script"."ctime" IS '创建时间';
COMMENT ON COLUMN "event_related_script"."utime" IS '更新时间';
COMMENT ON COLUMN "event_related_script"."deleted" IS '删除标识';
COMMENT ON COLUMN "event_related_script"."condition" IS '执行条件';
COMMENT ON COLUMN "event_related_script"."event_ingestion_id" IS '事件接入id';
COMMENT ON COLUMN "event_related_script"."topic" IS '主题名';
COMMENT ON COLUMN "event_related_script"."script_id" IS '剧本id';
COMMENT ON COLUMN "event_related_script"."event_name" IS '自定义事件名称';
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
    "enabled_health_status" VARCHAR(255),
    "execution_health_status" VARCHAR(255),
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
COMMENT ON COLUMN "events_ingestion"."enabled_health_status" IS '上游接入健康状态';
COMMENT ON COLUMN "events_ingestion"."execution_health_status" IS '联动执行健康状态';
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
    "Permissions" JSONB
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
    CONSTRAINT "uid_playbook_name_1a146f" UNIQUE ("name", "deleted")
);
COMMENT ON COLUMN "playbook"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook"."name" IS '剧本名称';
COMMENT ON COLUMN "playbook"."remark" IS '描述';
COMMENT ON COLUMN "playbook"."internal" IS '是否内置剧本';
COMMENT ON TABLE "playbook" IS '剧本模型';
CREATE TABLE IF NOT EXISTS "playbook_version" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "change_desc" TEXT NOT NULL,
    "version" VARCHAR(30) NOT NULL DEFAULT '0',
    "status" INT NOT NULL DEFAULT 0,
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
    "run_id" VARCHAR(50) NOT NULL,
    "content" TEXT NOT NULL,
    "status" INT NOT NULL DEFAULT 0,
    "expire_time" TIMESTAMPTZ NOT NULL,
    "audit_node_id" VARCHAR(50) NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "params" JSONB NOT NULL,
    "processor_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE,
    "workflow_id_id" VARCHAR(36) NOT NULL REFERENCES "playbook_version" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_approvement_title_2bd859" ON "approvement" ("title");
COMMENT ON COLUMN "approvement"."ctime" IS '创建时间';
COMMENT ON COLUMN "approvement"."utime" IS '更新时间';
COMMENT ON COLUMN "approvement"."deleted" IS '删除标识';
COMMENT ON COLUMN "approvement"."title" IS '审核标题';
COMMENT ON COLUMN "approvement"."run_id" IS '关联运行ID';
COMMENT ON COLUMN "approvement"."content" IS '审核描述内容';
COMMENT ON COLUMN "approvement"."status" IS '审核状态，0代表待审核，1代表审核通过，2代表审核拒绝，3代表审核过期';
COMMENT ON COLUMN "approvement"."expire_time" IS '审核过期时间';
COMMENT ON COLUMN "approvement"."audit_node_id" IS '审核节点ID';
COMMENT ON COLUMN "approvement"."created_at" IS '审核触发的时间';
COMMENT ON COLUMN "approvement"."params" IS '自定义参数';
COMMENT ON COLUMN "approvement"."processor_id" IS '实际审核处理人';
COMMENT ON COLUMN "approvement"."workflow_id_id" IS '关联剧本ID';
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
CREATE TABLE IF NOT EXISTS "room" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(50) NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "room"."ctime" IS '创建时间';
COMMENT ON COLUMN "room"."utime" IS '更新时间';
COMMENT ON COLUMN "room"."deleted" IS '删除标识';
COMMENT ON COLUMN "room"."name" IS '作战室名称';
COMMENT ON COLUMN "room"."creator_id" IS '房主';
COMMENT ON TABLE "room" IS '作战室房间模型';
CREATE TABLE IF NOT EXISTS "events" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "event_name" VARCHAR(255) NOT NULL UNIQUE,
    "event_process_status" VARCHAR(50) NOT NULL,
    "event_source" VARCHAR(255),
    "owner" JSONB,
    "created_id" VARCHAR(36) NOT NULL,
    "event_summary" TEXT,
    "log_data" TEXT NOT NULL,
    "event_ingestion_id" VARCHAR(255) NOT NULL,
    "room_id" VARCHAR(36) REFERENCES "room" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "events"."ctime" IS '创建时间';
COMMENT ON COLUMN "events"."utime" IS '更新时间';
COMMENT ON COLUMN "events"."deleted" IS '删除标识';
COMMENT ON COLUMN "events"."event_name" IS '事件名称';
COMMENT ON COLUMN "events"."event_process_status" IS '事件处理状态（如 ASSIGNED, RESOLVED）';
COMMENT ON COLUMN "events"."event_source" IS '事件来源';
COMMENT ON COLUMN "events"."owner" IS '负责人（包括角色和用户）';
COMMENT ON COLUMN "events"."created_id" IS '创建人ID，关联用户表';
COMMENT ON COLUMN "events"."event_summary" IS '事件概要';
COMMENT ON COLUMN "events"."log_data" IS '日志数据';
COMMENT ON COLUMN "events"."event_ingestion_id" IS '事件接入id，管理事件接入表';
COMMENT ON COLUMN "events"."room_id" IS '更新人';
COMMENT ON TABLE "events" IS '事件列表';
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
    "description" VARCHAR(255),
    "status" INT NOT NULL DEFAULT 0,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    "hash_value" VARCHAR(255),
    "internal" BOOL NOT NULL DEFAULT False,
    "tag" JSONB,
    "main" VARCHAR(255)
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
COMMENT ON COLUMN "tool"."internal" IS '是否内部工具';
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
    "playbook_input" JSONB,
    "action_input" JSONB,
    "status" INT NOT NULL,
    "tactics" VARCHAR(50) NOT NULL,
    "result" VARCHAR(50),
    "success_rate" VARCHAR(10),
    "enable" INT NOT NULL DEFAULT 1,
    "end_time" VARCHAR(50) NOT NULL,
    "action_id" VARCHAR(36) REFERENCES "tool_action" ("id") ON DELETE CASCADE,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_version_id" VARCHAR(36) REFERENCES "playbook_version" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "timed"."ctime" IS '创建时间';
COMMENT ON COLUMN "timed"."utime" IS '更新时间';
COMMENT ON COLUMN "timed"."deleted" IS '删除标识';
COMMENT ON COLUMN "timed"."name" IS '任务名称';
COMMENT ON COLUMN "timed"."type" IS '执行类型，0为执行剧本，1为执行动作';
COMMENT ON COLUMN "timed"."playbook_input" IS '剧本输入参数';
COMMENT ON COLUMN "timed"."action_input" IS '工具动作输入参数';
COMMENT ON COLUMN "timed"."status" IS '执行状态，0为单次执行，1为周期执行';
COMMENT ON COLUMN "timed"."tactics" IS '执行策略';
COMMENT ON COLUMN "timed"."result" IS '执行结果';
COMMENT ON COLUMN "timed"."success_rate" IS '成功率';
COMMENT ON COLUMN "timed"."enable" IS '是否启用, 0为禁用，1为启用';
COMMENT ON COLUMN "timed"."end_time" IS '结束时间';
COMMENT ON COLUMN "timed"."action_id" IS '关联工具动作';
COMMENT ON COLUMN "timed"."creator_id" IS '创建人';
COMMENT ON COLUMN "timed"."playbook_version_id" IS '关联剧本';
COMMENT ON COLUMN "timed"."updator_id" IS '更新人';
COMMENT ON TABLE "timed" IS '运行场景模型';
CREATE TABLE IF NOT EXISTS "runlog" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "run_id" VARCHAR(36) NOT NULL,
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
    "event_id_id" VARCHAR(36) REFERENCES "events" ("id") ON DELETE CASCADE,
    "playbook_version_id" VARCHAR(36) REFERENCES "playbook_version" ("id") ON DELETE CASCADE,
    "timer_id_id" VARCHAR(36) REFERENCES "timed" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "runlog"."ctime" IS '创建时间';
COMMENT ON COLUMN "runlog"."utime" IS '更新时间';
COMMENT ON COLUMN "runlog"."deleted" IS '删除标识';
COMMENT ON COLUMN "runlog"."run_id" IS '每次运行的唯一ID';
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
COMMENT ON COLUMN "runlog"."event_id_id" IS '关联事件';
COMMENT ON COLUMN "runlog"."playbook_version_id" IS '关联剧本';
COMMENT ON COLUMN "runlog"."timer_id_id" IS '关联定时任务';
COMMENT ON TABLE "runlog" IS '所有节点的执行记录';
CREATE TABLE IF NOT EXISTS "tool_resources" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_name" VARCHAR(255) NOT NULL,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "engine" VARCHAR(255),
    "resources" JSONB NOT NULL,
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
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_approve_acc_approve_13ec1c" ON "approve_accounts" ("approvement_id", "account_id");
CREATE TABLE IF NOT EXISTS "room_accounts" (
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE,
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "uidx_room_accoun_room_id_74467c" ON "room_accounts" ("room_id", "account_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
