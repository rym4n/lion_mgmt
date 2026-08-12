from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
DROP TABLE IF EXISTS "supply_chain_ci_relation" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci_relation_type" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci_reference" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci_unique_rule" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci_field_def" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci_type" CASCADE;
DROP TABLE IF EXISTS "supply_chain_ci_model_group" CASCADE;
DROP TABLE IF EXISTS "supply_chain_builtin_activation_event" CASCADE;
DROP TABLE IF EXISTS "supply_chain_builtin_activation_preview" CASCADE;
DROP TABLE IF EXISTS "supply_chain_builtin_activation" CASCADE;
DROP TABLE IF EXISTS "supply_chain_builtin_catalog_event" CASCADE;
DROP TABLE IF EXISTS "supply_chain_builtin_catalog_state" CASCADE;
DROP TABLE IF EXISTS "supply_chain_project_authorization" CASCADE;
DROP TABLE IF EXISTS "supply_chain_nda_record" CASCADE;
DROP TABLE IF EXISTS "supply_chain_unknown_terminal" CASCADE;
DROP TABLE IF EXISTS "supply_chain_boundary_log" CASCADE;
DROP TABLE IF EXISTS "supply_chain_exception_device_log" CASCADE;
DROP TABLE IF EXISTS "supply_chain_collection_task" CASCADE;
DROP TABLE IF EXISTS "supply_chain_collection_run" CASCADE;
DROP TABLE IF EXISTS "supply_chain_parse_mapping_template" CASCADE;
DROP TABLE IF EXISTS "supply_chain_preset_policy" CASCADE;
DROP TABLE IF EXISTS "supply_chain_actual_policy" CASCADE;
DROP TABLE IF EXISTS "supply_chain_policy_compare_run" CASCADE;
DROP TABLE IF EXISTS "supply_chain_policy_compare_item" CASCADE;
DROP TABLE IF EXISTS "supply_chain_policy_change" CASCADE;
DROP TABLE IF EXISTS "supply_chain_network_topology" CASCADE;
DROP TABLE IF EXISTS "supply_chain_review_issue" CASCADE;
DROP TABLE IF EXISTS "supply_chain_remediation_record" CASCADE;
DROP TABLE IF EXISTS "supply_chain_disposal_rule" CASCADE;
DROP TABLE IF EXISTS "supply_chain_disposal_task" CASCADE;
DROP TABLE IF EXISTS "supply_chain_attachment_ref" CASCADE;
DROP TABLE IF EXISTS "supply_chain_notification_event" CASCADE;

CREATE TABLE "asset_builtin_activation" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "subject_type" VARCHAR(20) NOT NULL DEFAULT 'model',
    "subject_key" VARCHAR(64) NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT False,
    "revision" INT NOT NULL DEFAULT 0,
    "updater" VARCHAR(255),
    CONSTRAINT "uid_asset_built_tenant__ae890f" UNIQUE ("tenant_id", "subject_type", "subject_key")
);
CREATE INDEX "idx_asset_built_tenant__4191b9" ON "asset_builtin_activation" ("tenant_id", "enabled");
COMMENT ON COLUMN "asset_builtin_activation"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_builtin_activation"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_builtin_activation"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_builtin_activation"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_builtin_activation"."subject_type" IS 'model';
COMMENT ON COLUMN "asset_builtin_activation"."subject_key" IS '模型稳定标识';
COMMENT ON COLUMN "asset_builtin_activation"."enabled" IS '是否启用';
COMMENT ON COLUMN "asset_builtin_activation"."revision" IS '状态版本';
COMMENT ON COLUMN "asset_builtin_activation"."updater" IS '更新人';
COMMENT ON TABLE "asset_builtin_activation" IS 'Tenant-owned activation state separated from builtin definitions.';
CREATE TABLE "asset_builtin_activation_event" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "request_id" VARCHAR(64) NOT NULL,
    "action" VARCHAR(16) NOT NULL,
    "scope" VARCHAR(16) NOT NULL,
    "subject_keys" JSONB NOT NULL,
    "before_state" JSONB NOT NULL,
    "after_state" JSONB NOT NULL,
    "impact" JSONB NOT NULL,
    "catalog_version" VARCHAR(64) NOT NULL,
    "tenant_revision" INT NOT NULL DEFAULT 0,
    "operator" VARCHAR(255),
    CONSTRAINT "uid_asset_built_tenant__c8ec79" UNIQUE ("tenant_id", "request_id")
);
CREATE INDEX "idx_asset_built_tenant__39cb37" ON "asset_builtin_activation_event" ("tenant_id", "ctime");
COMMENT ON COLUMN "asset_builtin_activation_event"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_builtin_activation_event"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_builtin_activation_event"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_builtin_activation_event"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_builtin_activation_event"."request_id" IS '幂等请求 ID';
COMMENT ON COLUMN "asset_builtin_activation_event"."action" IS 'enable/disable';
COMMENT ON COLUMN "asset_builtin_activation_event"."scope" IS 'model/group/selection/all';
COMMENT ON COLUMN "asset_builtin_activation_event"."subject_keys" IS '操作对象';
COMMENT ON COLUMN "asset_builtin_activation_event"."before_state" IS '变更前状态';
COMMENT ON COLUMN "asset_builtin_activation_event"."after_state" IS '变更后状态';
COMMENT ON COLUMN "asset_builtin_activation_event"."impact" IS '影响摘要';
COMMENT ON COLUMN "asset_builtin_activation_event"."catalog_version" IS '目录版本';
COMMENT ON COLUMN "asset_builtin_activation_event"."tenant_revision" IS '变更后版本';
COMMENT ON COLUMN "asset_builtin_activation_event"."operator" IS '操作人';
COMMENT ON TABLE "asset_builtin_activation_event" IS 'Idempotent audit event for a tenant activation transaction.';
CREATE TABLE "asset_builtin_activation_preview" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "token" VARCHAR(64) NOT NULL,
    "action" VARCHAR(16) NOT NULL,
    "scope" VARCHAR(16) NOT NULL,
    "subject_keys" JSONB NOT NULL,
    "catalog_version" VARCHAR(64) NOT NULL,
    "tenant_revision" INT NOT NULL DEFAULT 0,
    "impact" JSONB NOT NULL,
    "expires_at" TIMESTAMPTZ NOT NULL,
    "used" BOOL NOT NULL DEFAULT False,
    "creator" VARCHAR(255),
    CONSTRAINT "uid_asset_built_tenant__885386" UNIQUE ("tenant_id", "token")
);
CREATE INDEX "idx_asset_built_tenant__b46379" ON "asset_builtin_activation_preview" ("tenant_id", "expires_at");
COMMENT ON COLUMN "asset_builtin_activation_preview"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_builtin_activation_preview"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_builtin_activation_preview"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_builtin_activation_preview"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_builtin_activation_preview"."token" IS '一次性预览令牌';
COMMENT ON COLUMN "asset_builtin_activation_preview"."action" IS 'enable/disable';
COMMENT ON COLUMN "asset_builtin_activation_preview"."scope" IS 'model/group/selection/all';
COMMENT ON COLUMN "asset_builtin_activation_preview"."subject_keys" IS '操作对象';
COMMENT ON COLUMN "asset_builtin_activation_preview"."catalog_version" IS '目录版本';
COMMENT ON COLUMN "asset_builtin_activation_preview"."tenant_revision" IS '租户状态版本';
COMMENT ON COLUMN "asset_builtin_activation_preview"."impact" IS '影响摘要';
COMMENT ON COLUMN "asset_builtin_activation_preview"."expires_at" IS '过期时间';
COMMENT ON COLUMN "asset_builtin_activation_preview"."used" IS '是否已使用';
COMMENT ON COLUMN "asset_builtin_activation_preview"."creator" IS '创建人';
COMMENT ON TABLE "asset_builtin_activation_preview" IS 'Short-lived impact preview required before activation changes.';
CREATE TABLE "asset_builtin_catalog_event" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "request_id" VARCHAR(64) NOT NULL,
    "action" VARCHAR(20) NOT NULL DEFAULT 'reconcile',
    "status" VARCHAR(20) NOT NULL,
    "catalog_version" VARCHAR(64) NOT NULL,
    "catalog_digest" VARCHAR(80) NOT NULL,
    "before_state" JSONB NOT NULL,
    "after_state" JSONB NOT NULL,
    "changes" JSONB NOT NULL,
    "failure_reason" VARCHAR(255),
    "operator" VARCHAR(255),
    CONSTRAINT "uid_asset_built_tenant__90ac35" UNIQUE ("tenant_id", "request_id")
);
CREATE INDEX "idx_asset_built_tenant__619c1b" ON "asset_builtin_catalog_event" ("tenant_id", "ctime");
CREATE INDEX "idx_asset_built_tenant__6c922b" ON "asset_builtin_catalog_event" ("tenant_id", "status");
COMMENT ON COLUMN "asset_builtin_catalog_event"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_builtin_catalog_event"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_builtin_catalog_event"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_builtin_catalog_event"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_builtin_catalog_event"."request_id" IS '协调请求 ID';
COMMENT ON COLUMN "asset_builtin_catalog_event"."action" IS '协调动作';
COMMENT ON COLUMN "asset_builtin_catalog_event"."status" IS 'succeeded/unchanged/failed';
COMMENT ON COLUMN "asset_builtin_catalog_event"."catalog_version" IS '目录版本';
COMMENT ON COLUMN "asset_builtin_catalog_event"."catalog_digest" IS '目录摘要';
COMMENT ON COLUMN "asset_builtin_catalog_event"."before_state" IS '协调前状态';
COMMENT ON COLUMN "asset_builtin_catalog_event"."after_state" IS '协调后状态';
COMMENT ON COLUMN "asset_builtin_catalog_event"."changes" IS '变更数量';
COMMENT ON COLUMN "asset_builtin_catalog_event"."failure_reason" IS '失败类型';
COMMENT ON COLUMN "asset_builtin_catalog_event"."operator" IS '操作人';
COMMENT ON TABLE "asset_builtin_catalog_event" IS 'Audit event for builtin-catalog initialization and reconciliation.';
CREATE TABLE "asset_builtin_catalog_state" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "catalog_key" VARCHAR(64) NOT NULL,
    "catalog_version" VARCHAR(64) NOT NULL,
    "catalog_digest" VARCHAR(80) NOT NULL,
    "revision" INT NOT NULL DEFAULT 0,
    "status" VARCHAR(32) NOT NULL DEFAULT 'ready',
    "onboarding_completed" BOOL NOT NULL DEFAULT False,
    "updater" VARCHAR(255),
    CONSTRAINT "uid_asset_built_tenant__72c719" UNIQUE ("tenant_id", "catalog_key")
);
CREATE INDEX "idx_asset_built_tenant__4e6ce4" ON "asset_builtin_catalog_state" ("tenant_id", "status");
COMMENT ON COLUMN "asset_builtin_catalog_state"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_builtin_catalog_state"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_builtin_catalog_state"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_builtin_catalog_state"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_builtin_catalog_state"."catalog_key" IS '目录标识';
COMMENT ON COLUMN "asset_builtin_catalog_state"."catalog_version" IS '目录版本';
COMMENT ON COLUMN "asset_builtin_catalog_state"."catalog_digest" IS '目录摘要';
COMMENT ON COLUMN "asset_builtin_catalog_state"."revision" IS '租户目录状态版本';
COMMENT ON COLUMN "asset_builtin_catalog_state"."status" IS '目录状态';
COMMENT ON COLUMN "asset_builtin_catalog_state"."onboarding_completed" IS '首次启用引导是否已完成';
COMMENT ON COLUMN "asset_builtin_catalog_state"."updater" IS '更新人';
COMMENT ON TABLE "asset_builtin_catalog_state" IS 'Installed builtin-catalog revision for one tenant.';
CREATE TABLE "asset_field_definition" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "asset_model_id" VARCHAR(36) NOT NULL,
    "asset_model_key" VARCHAR(64) NOT NULL,
    "field_key" VARCHAR(64) NOT NULL,
    "source_field_key" VARCHAR(64),
    "label" VARCHAR(255) NOT NULL,
    "data_type" VARCHAR(20) NOT NULL,
    "enum_options" JSONB NOT NULL,
    "options" JSONB NOT NULL,
    "referenced_asset_model_id" VARCHAR(36),
    "referenced_asset_model_key" VARCHAR(64),
    "required" BOOL NOT NULL DEFAULT False,
    "unique" BOOL NOT NULL DEFAULT False,
    "editable" BOOL NOT NULL DEFAULT True,
    "is_derived" BOOL NOT NULL DEFAULT False,
    "field_group" VARCHAR(255) NOT NULL DEFAULT '其他',
    "field_group_sort" INT NOT NULL DEFAULT 0,
    "user_prompt" TEXT,
    "default_value" JSONB,
    "sort" INT NOT NULL DEFAULT 0,
    "is_locked" BOOL NOT NULL DEFAULT False,
    "is_builtin" BOOL NOT NULL DEFAULT False,
    "builtin_key" VARCHAR(160),
    "builtin_version" VARCHAR(64),
    "builtin_hash" VARCHAR(64),
    "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_asset_field_tenant__76501d" UNIQUE ("tenant_id", "asset_model_id", "field_key")
);
CREATE INDEX "idx_asset_field_tenant__cd0571" ON "asset_field_definition" ("tenant_id", "asset_model_id");
CREATE INDEX "idx_asset_field_tenant__3373b1" ON "asset_field_definition" ("tenant_id", "data_type");
CREATE INDEX "idx_asset_field_tenant__c58531" ON "asset_field_definition" ("tenant_id", "asset_model_id", "field_group_sort");
COMMENT ON COLUMN "asset_field_definition"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_field_definition"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_field_definition"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_field_definition"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_field_definition"."asset_model_id" IS '所属模型 ID';
COMMENT ON COLUMN "asset_field_definition"."asset_model_key" IS '所属模型 key';
COMMENT ON COLUMN "asset_field_definition"."field_key" IS '字段 key';
COMMENT ON COLUMN "asset_field_definition"."source_field_key" IS '来源系统字段 key';
COMMENT ON COLUMN "asset_field_definition"."label" IS '字段名称';
COMMENT ON COLUMN "asset_field_definition"."data_type" IS '字段类型';
COMMENT ON COLUMN "asset_field_definition"."enum_options" IS '枚举选项';
COMMENT ON COLUMN "asset_field_definition"."options" IS '类型选项';
COMMENT ON COLUMN "asset_field_definition"."referenced_asset_model_id" IS '引用模型 ID';
COMMENT ON COLUMN "asset_field_definition"."referenced_asset_model_key" IS '引用模型 key';
COMMENT ON COLUMN "asset_field_definition"."required" IS '是否必填';
COMMENT ON COLUMN "asset_field_definition"."unique" IS '是否唯一';
COMMENT ON COLUMN "asset_field_definition"."editable" IS '是否可编辑';
COMMENT ON COLUMN "asset_field_definition"."is_derived" IS '派生展示字段';
COMMENT ON COLUMN "asset_field_definition"."field_group" IS '字段分组';
COMMENT ON COLUMN "asset_field_definition"."field_group_sort" IS '字段分组顺序';
COMMENT ON COLUMN "asset_field_definition"."user_prompt" IS '字段提示';
COMMENT ON COLUMN "asset_field_definition"."default_value" IS '默认值';
COMMENT ON COLUMN "asset_field_definition"."sort" IS '排序';
COMMENT ON COLUMN "asset_field_definition"."is_locked" IS '锁定字段';
COMMENT ON COLUMN "asset_field_definition"."is_builtin" IS '产品内置字段';
COMMENT ON COLUMN "asset_field_definition"."builtin_key" IS '内置稳定标识';
COMMENT ON COLUMN "asset_field_definition"."builtin_version" IS '内置版本';
COMMENT ON COLUMN "asset_field_definition"."builtin_hash" IS '定义摘要';
COMMENT ON COLUMN "asset_field_definition"."owner" IS 'product/tenant';
COMMENT ON COLUMN "asset_field_definition"."creator" IS '创建人';
COMMENT ON COLUMN "asset_field_definition"."updater" IS '更新人';
COMMENT ON TABLE "asset_field_definition" IS 'Dynamic field definition for an asset model.';
CREATE TABLE "asset_instance" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "asset_model_id" VARCHAR(36) NOT NULL,
    "asset_model_key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "attributes" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "risk_status" VARCHAR(20) NOT NULL DEFAULT 'normal',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_asset_insta_tenant__bbfca4" ON "asset_instance" ("tenant_id", "asset_model_id");
CREATE INDEX "idx_asset_insta_tenant__979963" ON "asset_instance" ("tenant_id", "asset_model_id", "status");
CREATE INDEX "idx_asset_insta_tenant__4385d0" ON "asset_instance" ("tenant_id", "project_area_id");
CREATE INDEX "idx_asset_insta_tenant__941b87" ON "asset_instance" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_asset_insta_tenant__ba1c7a" ON "asset_instance" ("tenant_id", "asset_model_id", "project_area_id", "accessory_project_id");
COMMENT ON COLUMN "asset_instance"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_instance"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_instance"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_instance"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_instance"."asset_model_id" IS '模型 ID';
COMMENT ON COLUMN "asset_instance"."asset_model_key" IS '模型 key';
COMMENT ON COLUMN "asset_instance"."name" IS '实例名称';
COMMENT ON COLUMN "asset_instance"."project_area_id" IS '厂区/区域资产 ID';
COMMENT ON COLUMN "asset_instance"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "asset_instance"."attributes" IS '动态属性';
COMMENT ON COLUMN "asset_instance"."status" IS 'active/disabled/archived';
COMMENT ON COLUMN "asset_instance"."risk_status" IS 'normal/low/medium/high';
COMMENT ON COLUMN "asset_instance"."creator" IS '创建人';
COMMENT ON COLUMN "asset_instance"."updater" IS '更新人';
COMMENT ON TABLE "asset_instance" IS 'Asset instance with dynamic attributes.';
CREATE TABLE "asset_model" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "icon" VARCHAR(100),
    "group_id" VARCHAR(36) NOT NULL,
    "group_key" VARCHAR(64) NOT NULL,
    "primary_field" VARCHAR(64) NOT NULL DEFAULT '',
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "is_locked" BOOL NOT NULL DEFAULT False,
    "is_builtin" BOOL NOT NULL DEFAULT False,
    "builtin_key" VARCHAR(64),
    "builtin_version" VARCHAR(64),
    "builtin_hash" VARCHAR(64),
    "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
    "sort" INT NOT NULL DEFAULT 0,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_asset_model_tenant__a06f1a" UNIQUE ("tenant_id", "key")
);
CREATE INDEX "idx_asset_model_tenant__5119a8" ON "asset_model" ("tenant_id", "status");
CREATE INDEX "idx_asset_model_tenant__6ca4a7" ON "asset_model" ("tenant_id", "group_id", "sort");
CREATE INDEX "idx_asset_model_tenant__286d37" ON "asset_model" ("tenant_id", "is_builtin");
COMMENT ON COLUMN "asset_model"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_model"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_model"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_model"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_model"."key" IS '模型唯一标识';
COMMENT ON COLUMN "asset_model"."name" IS '模型名称';
COMMENT ON COLUMN "asset_model"."icon" IS '图标';
COMMENT ON COLUMN "asset_model"."group_id" IS '所属分组 ID';
COMMENT ON COLUMN "asset_model"."group_key" IS '所属分组 key';
COMMENT ON COLUMN "asset_model"."primary_field" IS '主展示字段';
COMMENT ON COLUMN "asset_model"."status" IS 'active/disabled';
COMMENT ON COLUMN "asset_model"."is_locked" IS '交付锁定标记';
COMMENT ON COLUMN "asset_model"."is_builtin" IS '产品内置模型';
COMMENT ON COLUMN "asset_model"."builtin_key" IS '内置稳定标识';
COMMENT ON COLUMN "asset_model"."builtin_version" IS '内置版本';
COMMENT ON COLUMN "asset_model"."builtin_hash" IS '定义摘要';
COMMENT ON COLUMN "asset_model"."owner" IS 'product/tenant';
COMMENT ON COLUMN "asset_model"."sort" IS '展示顺序';
COMMENT ON COLUMN "asset_model"."creator" IS '创建人';
COMMENT ON COLUMN "asset_model"."updater" IS '更新人';
COMMENT ON TABLE "asset_model" IS '资产 model definition for managed assets.';
CREATE TABLE "asset_model_group" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "sort" INT NOT NULL DEFAULT 0,
    "is_builtin" BOOL NOT NULL DEFAULT False,
    "is_locked" BOOL NOT NULL DEFAULT False,
    "builtin_version" VARCHAR(64),
    "builtin_hash" VARCHAR(64),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_asset_model_tenant__201f96" UNIQUE ("tenant_id", "key")
);
CREATE INDEX "idx_asset_model_tenant__8ae00f" ON "asset_model_group" ("tenant_id", "sort");
CREATE INDEX "idx_asset_model_tenant__c4c7fd" ON "asset_model_group" ("tenant_id", "is_builtin");
COMMENT ON COLUMN "asset_model_group"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_model_group"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_model_group"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_model_group"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_model_group"."key" IS '分组唯一标识';
COMMENT ON COLUMN "asset_model_group"."name" IS '分组名称';
COMMENT ON COLUMN "asset_model_group"."sort" IS '展示顺序';
COMMENT ON COLUMN "asset_model_group"."is_builtin" IS '产品内置分组';
COMMENT ON COLUMN "asset_model_group"."is_locked" IS '内置结构锁定';
COMMENT ON COLUMN "asset_model_group"."builtin_version" IS '内置版本';
COMMENT ON COLUMN "asset_model_group"."builtin_hash" IS '定义摘要';
COMMENT ON COLUMN "asset_model_group"."creator" IS '创建人';
COMMENT ON COLUMN "asset_model_group"."updater" IS '更新人';
COMMENT ON TABLE "asset_model_group" IS 'Tenant-visible grouping for builtin and custom asset models.';
CREATE TABLE "asset_reference" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "field_key" VARCHAR(64) NOT NULL,
    "position" INT NOT NULL DEFAULT 0,
    "source_asset_id" VARCHAR(36) NOT NULL REFERENCES "asset_instance" ("id") ON DELETE CASCADE,
    "target_asset_id" VARCHAR(36) NOT NULL REFERENCES "asset_instance" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_asset_refer_tenant__4d8572" UNIQUE ("tenant_id", "source_asset_id", "field_key", "target_asset_id")
);
CREATE INDEX "idx_asset_refer_tenant__ca38b2" ON "asset_reference" ("tenant_id", "source_asset_id");
CREATE INDEX "idx_asset_refer_tenant__9ba470" ON "asset_reference" ("tenant_id", "target_asset_id");
CREATE INDEX "idx_asset_refer_tenant__6efa15" ON "asset_reference" ("tenant_id", "source_asset_id", "field_key");
COMMENT ON COLUMN "asset_reference"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_reference"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_reference"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_reference"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_reference"."field_key" IS '引用字段 key';
COMMENT ON COLUMN "asset_reference"."position" IS '多值引用顺序';
COMMENT ON COLUMN "asset_reference"."source_asset_id" IS '持有引用的资产';
COMMENT ON COLUMN "asset_reference"."target_asset_id" IS '被引用的资产';
COMMENT ON TABLE "asset_reference" IS 'Derived index for model-reference fields on asset instances.';
CREATE TABLE "asset_unique_rule" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "asset_model_id" VARCHAR(36) NOT NULL,
    "asset_model_key" VARCHAR(64) NOT NULL,
    "key" VARCHAR(64) NOT NULL,
    "field_keys" JSONB NOT NULL,
    "sort" INT NOT NULL DEFAULT 0,
    "is_locked" BOOL NOT NULL DEFAULT False,
    "is_builtin" BOOL NOT NULL DEFAULT False,
    "builtin_key" VARCHAR(160),
    "builtin_version" VARCHAR(64),
    "builtin_hash" VARCHAR(64),
    "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_asset_uniqu_tenant__1933b4" UNIQUE ("tenant_id", "asset_model_id", "key")
);
CREATE INDEX "idx_asset_uniqu_tenant__068d00" ON "asset_unique_rule" ("tenant_id", "asset_model_id");
COMMENT ON COLUMN "asset_unique_rule"."ctime" IS '创建时间';
COMMENT ON COLUMN "asset_unique_rule"."utime" IS '更新时间';
COMMENT ON COLUMN "asset_unique_rule"."deleted" IS '删除标识';
COMMENT ON COLUMN "asset_unique_rule"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "asset_unique_rule"."asset_model_id" IS '所属模型 ID';
COMMENT ON COLUMN "asset_unique_rule"."asset_model_key" IS '所属模型 key';
COMMENT ON COLUMN "asset_unique_rule"."key" IS '规则稳定标识';
COMMENT ON COLUMN "asset_unique_rule"."field_keys" IS '联合唯一字段';
COMMENT ON COLUMN "asset_unique_rule"."sort" IS '规则顺序';
COMMENT ON COLUMN "asset_unique_rule"."is_locked" IS '锁定规则';
COMMENT ON COLUMN "asset_unique_rule"."is_builtin" IS '产品内置规则';
COMMENT ON COLUMN "asset_unique_rule"."builtin_key" IS '内置稳定标识';
COMMENT ON COLUMN "asset_unique_rule"."builtin_version" IS '内置版本';
COMMENT ON COLUMN "asset_unique_rule"."builtin_hash" IS '定义摘要';
COMMENT ON COLUMN "asset_unique_rule"."owner" IS 'product/tenant';
COMMENT ON COLUMN "asset_unique_rule"."creator" IS '创建人';
COMMENT ON COLUMN "asset_unique_rule"."updater" IS '更新人';
COMMENT ON TABLE "asset_unique_rule" IS 'Model-level compound uniqueness rule.';
CREATE TABLE "network_access_attachment_ref" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "object_type" VARCHAR(80) NOT NULL,
    "object_id" VARCHAR(36) NOT NULL,
    "file_id" VARCHAR(255) NOT NULL,
    "file_name" VARCHAR(255) NOT NULL,
    "file_type" VARCHAR(100),
    "file_size" INT NOT NULL DEFAULT 0,
    "uploaded_by" VARCHAR(255),
    "uploaded_at" TIMESTAMPTZ,
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__a40712" ON "network_access_attachment_ref" ("tenant_id", "object_type", "object_id");
CREATE INDEX "idx_network_acc_tenant__d03059" ON "network_access_attachment_ref" ("tenant_id", "file_id");
COMMENT ON COLUMN "network_access_attachment_ref"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_attachment_ref"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_attachment_ref"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_attachment_ref"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_attachment_ref"."object_type" IS '对象类型';
COMMENT ON COLUMN "network_access_attachment_ref"."object_id" IS '对象 ID';
COMMENT ON COLUMN "network_access_attachment_ref"."file_id" IS 'S3 路径';
COMMENT ON COLUMN "network_access_attachment_ref"."file_name" IS '文件名';
COMMENT ON COLUMN "network_access_attachment_ref"."file_type" IS '文件类型';
COMMENT ON COLUMN "network_access_attachment_ref"."file_size" IS '文件大小';
COMMENT ON COLUMN "network_access_attachment_ref"."uploaded_by" IS '上传人';
COMMENT ON COLUMN "network_access_attachment_ref"."uploaded_at" IS '上传时间';
COMMENT ON COLUMN "network_access_attachment_ref"."remark" IS '备注';
COMMENT ON COLUMN "network_access_attachment_ref"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_attachment_ref"."updater" IS '更新人';
COMMENT ON TABLE "network_access_attachment_ref" IS 'S3/RustFS object reference.';
CREATE TABLE "network_access_boundary_log" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "log_category" VARCHAR(30) NOT NULL,
    "operator_account" VARCHAR(255),
    "client_address" VARCHAR(100),
    "personnel_asset_id" VARCHAR(36),
    "endpoint_asset_id" VARCHAR(36),
    "occurred_at" TIMESTAMPTZ,
    "source_type" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "risk_flag" BOOL NOT NULL DEFAULT False,
    "detail" JSONB NOT NULL,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__762b89" ON "network_access_boundary_log" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__30d0b9" ON "network_access_boundary_log" ("tenant_id", "occurred_at");
CREATE INDEX "idx_network_acc_tenant__3f685c" ON "network_access_boundary_log" ("tenant_id", "accessory_project_id", "occurred_at");
CREATE INDEX "idx_network_acc_tenant__4e1954" ON "network_access_boundary_log" ("tenant_id", "risk_flag");
CREATE INDEX "idx_network_acc_tenant__14d9de" ON "network_access_boundary_log" ("tenant_id", "log_category");
COMMENT ON COLUMN "network_access_boundary_log"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_boundary_log"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_boundary_log"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_boundary_log"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_boundary_log"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_boundary_log"."log_category" IS 'file_operation/mail_outbound/system_access';
COMMENT ON COLUMN "network_access_boundary_log"."operator_account" IS '操作账号';
COMMENT ON COLUMN "network_access_boundary_log"."client_address" IS '客户端地址';
COMMENT ON COLUMN "network_access_boundary_log"."personnel_asset_id" IS '人员资产 ID';
COMMENT ON COLUMN "network_access_boundary_log"."endpoint_asset_id" IS '终端资产 ID';
COMMENT ON COLUMN "network_access_boundary_log"."occurred_at" IS '发生时间';
COMMENT ON COLUMN "network_access_boundary_log"."source_type" IS '来源';
COMMENT ON COLUMN "network_access_boundary_log"."risk_flag" IS '风险标记';
COMMENT ON COLUMN "network_access_boundary_log"."detail" IS '日志详情';
COMMENT ON COLUMN "network_access_boundary_log"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_boundary_log"."updater" IS '更新人';
COMMENT ON TABLE "network_access_boundary_log" IS 'Boundary file/mail/system access ledger.';
CREATE TABLE "network_access_collection_run" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "task_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "domain" VARCHAR(30) NOT NULL,
    "run_id" VARCHAR(64) NOT NULL,
    "playbook_run_id" VARCHAR(100),
    "action_run_id" VARCHAR(100),
    "timed_run_id" VARCHAR(100),
    "event_batch_id" VARCHAR(100),
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "raw_payload_ref" VARCHAR(255),
    "parsed_result_ref" VARCHAR(255),
    "total_count" INT NOT NULL DEFAULT 0,
    "success_count" INT NOT NULL DEFAULT 0,
    "failed_count" INT NOT NULL DEFAULT 0,
    "error_message" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__380674" ON "network_access_collection_run" ("tenant_id", "task_id");
CREATE INDEX "idx_network_acc_tenant__884cff" ON "network_access_collection_run" ("tenant_id", "domain");
CREATE INDEX "idx_network_acc_tenant__62122b" ON "network_access_collection_run" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__43a622" ON "network_access_collection_run" ("tenant_id", "run_id");
CREATE INDEX "idx_network_acc_tenant__a9796e" ON "network_access_collection_run" ("tenant_id", "status");
COMMENT ON COLUMN "network_access_collection_run"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_collection_run"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_collection_run"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_collection_run"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_collection_run"."task_id" IS '采集任务';
COMMENT ON COLUMN "network_access_collection_run"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_collection_run"."domain" IS '采集域';
COMMENT ON COLUMN "network_access_collection_run"."run_id" IS '运行 ID';
COMMENT ON COLUMN "network_access_collection_run"."playbook_run_id" IS '剧本运行 ID';
COMMENT ON COLUMN "network_access_collection_run"."action_run_id" IS '动作运行 ID';
COMMENT ON COLUMN "network_access_collection_run"."timed_run_id" IS '定时运行 ID';
COMMENT ON COLUMN "network_access_collection_run"."event_batch_id" IS '事件批次';
COMMENT ON COLUMN "network_access_collection_run"."status" IS 'pending/running/success/failed/partial';
COMMENT ON COLUMN "network_access_collection_run"."started_at" IS '开始时间';
COMMENT ON COLUMN "network_access_collection_run"."finished_at" IS '结束时间';
COMMENT ON COLUMN "network_access_collection_run"."raw_payload_ref" IS '原始结果引用';
COMMENT ON COLUMN "network_access_collection_run"."parsed_result_ref" IS '解析结果引用';
COMMENT ON COLUMN "network_access_collection_run"."total_count" IS '总数';
COMMENT ON COLUMN "network_access_collection_run"."success_count" IS '成功数';
COMMENT ON COLUMN "network_access_collection_run"."failed_count" IS '失败数';
COMMENT ON COLUMN "network_access_collection_run"."error_message" IS '错误信息';
COMMENT ON COLUMN "network_access_collection_run"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_collection_run"."updater" IS '更新人';
COMMENT ON TABLE "network_access_collection_run" IS 'Collection run with run_id plumbing for external execution.';
CREATE TABLE "network_access_collection_task" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "domain" VARCHAR(30) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "execution_mode" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "playbook_id" VARCHAR(100),
    "playbook_version_id" VARCHAR(100),
    "action_id" VARCHAR(100),
    "action_name" VARCHAR(255),
    "timed_task_id" VARCHAR(100),
    "event_ingestion_id" VARCHAR(100),
    "target_type" VARCHAR(50),
    "target_asset_ids" JSONB NOT NULL,
    "credential_ref" VARCHAR(255),
    "input_params" JSONB NOT NULL,
    "parser_template_id" VARCHAR(36),
    "mapping_template_id" VARCHAR(36),
    "schedule_enabled" BOOL NOT NULL DEFAULT False,
    "cron_expression" VARCHAR(100),
    "after_success_action" VARCHAR(30) NOT NULL DEFAULT 'none',
    "enabled" BOOL NOT NULL DEFAULT True,
    "last_run_at" TIMESTAMPTZ,
    "last_run_status" VARCHAR(20),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__94a879" ON "network_access_collection_task" ("tenant_id", "domain");
CREATE INDEX "idx_network_acc_tenant__4187e6" ON "network_access_collection_task" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__9ab135" ON "network_access_collection_task" ("tenant_id", "enabled");
COMMENT ON COLUMN "network_access_collection_task"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_collection_task"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_collection_task"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_collection_task"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_collection_task"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_collection_task"."domain" IS 'network/personnel_nda/boundary/exception_device';
COMMENT ON COLUMN "network_access_collection_task"."name" IS '任务名称';
COMMENT ON COLUMN "network_access_collection_task"."execution_mode" IS '采集方式';
COMMENT ON COLUMN "network_access_collection_task"."playbook_id" IS '剧本 ID';
COMMENT ON COLUMN "network_access_collection_task"."playbook_version_id" IS '剧本版本 ID';
COMMENT ON COLUMN "network_access_collection_task"."action_id" IS '动作 ID';
COMMENT ON COLUMN "network_access_collection_task"."action_name" IS '动作名称';
COMMENT ON COLUMN "network_access_collection_task"."timed_task_id" IS '定时任务 ID';
COMMENT ON COLUMN "network_access_collection_task"."event_ingestion_id" IS '事件接入 ID';
COMMENT ON COLUMN "network_access_collection_task"."target_type" IS '目标类型';
COMMENT ON COLUMN "network_access_collection_task"."target_asset_ids" IS '目标资产 ID 列表';
COMMENT ON COLUMN "network_access_collection_task"."credential_ref" IS '凭据引用';
COMMENT ON COLUMN "network_access_collection_task"."input_params" IS '输入参数';
COMMENT ON COLUMN "network_access_collection_task"."parser_template_id" IS '解析模板';
COMMENT ON COLUMN "network_access_collection_task"."mapping_template_id" IS '映射模板';
COMMENT ON COLUMN "network_access_collection_task"."schedule_enabled" IS '是否启用调度';
COMMENT ON COLUMN "network_access_collection_task"."cron_expression" IS 'Cron';
COMMENT ON COLUMN "network_access_collection_task"."after_success_action" IS 'none/policy_compare/risk_evaluate';
COMMENT ON COLUMN "network_access_collection_task"."enabled" IS '是否启用';
COMMENT ON COLUMN "network_access_collection_task"."last_run_at" IS '最近运行';
COMMENT ON COLUMN "network_access_collection_task"."last_run_status" IS '最近运行状态';
COMMENT ON COLUMN "network_access_collection_task"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_collection_task"."updater" IS '更新人';
COMMENT ON TABLE "network_access_collection_task" IS 'Business wrapper around host collection abilities.';
CREATE TABLE "network_access_disposal_rule" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "trigger_type" VARCHAR(60) NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True,
    "execution_mode" VARCHAR(30) NOT NULL DEFAULT 'manual_confirm',
    "actions" JSONB NOT NULL,
    "playbook_id" VARCHAR(100),
    "action_id" VARCHAR(100),
    "input_mapping" JSONB NOT NULL,
    "require_review_issue" BOOL NOT NULL DEFAULT False,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__654e0b" ON "network_access_disposal_rule" ("tenant_id", "project_area_id");
CREATE INDEX "idx_network_acc_tenant__4dd4bc" ON "network_access_disposal_rule" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__7e30a2" ON "network_access_disposal_rule" ("tenant_id", "trigger_type");
CREATE INDEX "idx_network_acc_tenant__039bf8" ON "network_access_disposal_rule" ("tenant_id", "enabled");
COMMENT ON COLUMN "network_access_disposal_rule"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_disposal_rule"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_disposal_rule"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_disposal_rule"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_disposal_rule"."project_area_id" IS '厂区资产 ID';
COMMENT ON COLUMN "network_access_disposal_rule"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_disposal_rule"."trigger_type" IS '触发类型';
COMMENT ON COLUMN "network_access_disposal_rule"."enabled" IS '是否启用';
COMMENT ON COLUMN "network_access_disposal_rule"."execution_mode" IS '执行模式';
COMMENT ON COLUMN "network_access_disposal_rule"."actions" IS '处置动作';
COMMENT ON COLUMN "network_access_disposal_rule"."playbook_id" IS '剧本 ID';
COMMENT ON COLUMN "network_access_disposal_rule"."action_id" IS '动作 ID';
COMMENT ON COLUMN "network_access_disposal_rule"."input_mapping" IS '输入映射';
COMMENT ON COLUMN "network_access_disposal_rule"."require_review_issue" IS '是否需要问题';
COMMENT ON COLUMN "network_access_disposal_rule"."risk_level" IS '风险等级';
COMMENT ON COLUMN "network_access_disposal_rule"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_disposal_rule"."updater" IS '更新人';
COMMENT ON TABLE "network_access_disposal_rule" IS 'Manual-confirm disposal rule.';
CREATE TABLE "network_access_disposal_task" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "rule_id" VARCHAR(36),
    "trigger_type" VARCHAR(60) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "personnel_asset_id" VARCHAR(36),
    "related_asset_ids" JSONB NOT NULL,
    "actions" JSONB NOT NULL,
    "status" VARCHAR(30) NOT NULL DEFAULT 'pending_confirm',
    "confirm_by" VARCHAR(255),
    "confirmed_at" TIMESTAMPTZ,
    "run_id" VARCHAR(64),
    "result_message" TEXT,
    "review_issue_id" VARCHAR(36),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__4278dd" ON "network_access_disposal_task" ("tenant_id", "project_area_id");
CREATE INDEX "idx_network_acc_tenant__5d064e" ON "network_access_disposal_task" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__fc0d56" ON "network_access_disposal_task" ("tenant_id", "personnel_asset_id");
CREATE INDEX "idx_network_acc_tenant__4f326a" ON "network_access_disposal_task" ("tenant_id", "status");
CREATE INDEX "idx_network_acc_tenant__af7f79" ON "network_access_disposal_task" ("tenant_id", "run_id");
COMMENT ON COLUMN "network_access_disposal_task"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_disposal_task"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_disposal_task"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_disposal_task"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_disposal_task"."rule_id" IS '规则';
COMMENT ON COLUMN "network_access_disposal_task"."trigger_type" IS '触发类型';
COMMENT ON COLUMN "network_access_disposal_task"."project_area_id" IS '厂区资产 ID';
COMMENT ON COLUMN "network_access_disposal_task"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_disposal_task"."personnel_asset_id" IS '人员资产 ID';
COMMENT ON COLUMN "network_access_disposal_task"."related_asset_ids" IS '关联资产 ID 列表';
COMMENT ON COLUMN "network_access_disposal_task"."actions" IS '动作';
COMMENT ON COLUMN "network_access_disposal_task"."status" IS 'pending_confirm/executing/success/failed/skipped/cancelled';
COMMENT ON COLUMN "network_access_disposal_task"."confirm_by" IS '确认人';
COMMENT ON COLUMN "network_access_disposal_task"."confirmed_at" IS '确认时间';
COMMENT ON COLUMN "network_access_disposal_task"."run_id" IS '运行 ID';
COMMENT ON COLUMN "network_access_disposal_task"."result_message" IS '结果';
COMMENT ON COLUMN "network_access_disposal_task"."review_issue_id" IS '审查问题';
COMMENT ON COLUMN "network_access_disposal_task"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_disposal_task"."updater" IS '更新人';
COMMENT ON TABLE "network_access_disposal_task" IS 'Manual-confirm disposal task with run_id tracking.';
CREATE TABLE "network_access_exception_device_log" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "exception_device_asset_id" VARCHAR(36) NOT NULL,
    "endpoint_asset_id" VARCHAR(36),
    "personnel_asset_id" VARCHAR(36),
    "log_type" VARCHAR(20) NOT NULL DEFAULT 'use',
    "detail" JSONB NOT NULL,
    "occurred_at" TIMESTAMPTZ,
    "source_type" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "abnormal_type" VARCHAR(40) NOT NULL DEFAULT 'none',
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "review_issue_id" VARCHAR(36),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__183c59" ON "network_access_exception_device_log" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__a102e9" ON "network_access_exception_device_log" ("tenant_id", "exception_device_asset_id");
CREATE INDEX "idx_network_acc_tenant__bc6735" ON "network_access_exception_device_log" ("tenant_id", "personnel_asset_id");
CREATE INDEX "idx_network_acc_tenant__17e730" ON "network_access_exception_device_log" ("tenant_id", "occurred_at");
CREATE INDEX "idx_network_acc_tenant__69039d" ON "network_access_exception_device_log" ("tenant_id", "accessory_project_id", "occurred_at");
CREATE INDEX "idx_network_acc_tenant__fd9f29" ON "network_access_exception_device_log" ("tenant_id", "abnormal_type");
CREATE INDEX "idx_network_acc_tenant__8ae66d" ON "network_access_exception_device_log" ("tenant_id", "status");
COMMENT ON COLUMN "network_access_exception_device_log"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_exception_device_log"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_exception_device_log"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_exception_device_log"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_exception_device_log"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_exception_device_log"."exception_device_asset_id" IS '例外设备资产 ID';
COMMENT ON COLUMN "network_access_exception_device_log"."endpoint_asset_id" IS '终端资产 ID';
COMMENT ON COLUMN "network_access_exception_device_log"."personnel_asset_id" IS '人员资产 ID';
COMMENT ON COLUMN "network_access_exception_device_log"."log_type" IS 'insert/remove/use';
COMMENT ON COLUMN "network_access_exception_device_log"."detail" IS '使用详情';
COMMENT ON COLUMN "network_access_exception_device_log"."occurred_at" IS '发生时间';
COMMENT ON COLUMN "network_access_exception_device_log"."source_type" IS 'file_parse/manual';
COMMENT ON COLUMN "network_access_exception_device_log"."abnormal_type" IS '异常类型';
COMMENT ON COLUMN "network_access_exception_device_log"."status" IS 'pending/confirmed/ignored/converted';
COMMENT ON COLUMN "network_access_exception_device_log"."review_issue_id" IS '审查问题';
COMMENT ON COLUMN "network_access_exception_device_log"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_exception_device_log"."updater" IS '更新人';
COMMENT ON TABLE "network_access_exception_device_log" IS 'Exception device usage ledger.';
CREATE TABLE "network_access_nda_record" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "personnel_asset_id" VARCHAR(36) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "nda_type" VARCHAR(50),
    "sign_status" VARCHAR(20) NOT NULL DEFAULT 'unsigned',
    "signed_at" TIMESTAMPTZ,
    "expires_at" TIMESTAMPTZ,
    "evidence_attachment_ids" JSONB NOT NULL,
    "source_type" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "source_record_id" VARCHAR(100),
    "last_sync_at" TIMESTAMPTZ,
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__0a2c43" ON "network_access_nda_record" ("tenant_id", "project_area_id");
CREATE INDEX "idx_network_acc_tenant__01320f" ON "network_access_nda_record" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__0cec80" ON "network_access_nda_record" ("tenant_id", "personnel_asset_id");
CREATE INDEX "idx_network_acc_tenant__90a3f7" ON "network_access_nda_record" ("tenant_id", "sign_status");
CREATE INDEX "idx_network_acc_tenant__77edb1" ON "network_access_nda_record" ("tenant_id", "expires_at");
COMMENT ON COLUMN "network_access_nda_record"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_nda_record"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_nda_record"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_nda_record"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_nda_record"."personnel_asset_id" IS '人员资产 ID';
COMMENT ON COLUMN "network_access_nda_record"."project_area_id" IS '厂区/区域资产 ID';
COMMENT ON COLUMN "network_access_nda_record"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_nda_record"."nda_type" IS 'NDA 类型';
COMMENT ON COLUMN "network_access_nda_record"."sign_status" IS 'signed/unsigned/expired';
COMMENT ON COLUMN "network_access_nda_record"."signed_at" IS '签署时间';
COMMENT ON COLUMN "network_access_nda_record"."expires_at" IS '到期时间';
COMMENT ON COLUMN "network_access_nda_record"."evidence_attachment_ids" IS '证明附件';
COMMENT ON COLUMN "network_access_nda_record"."source_type" IS 'hr/esign/manual';
COMMENT ON COLUMN "network_access_nda_record"."source_record_id" IS '来源记录';
COMMENT ON COLUMN "network_access_nda_record"."last_sync_at" IS '最近同步时间';
COMMENT ON COLUMN "network_access_nda_record"."remark" IS '备注';
COMMENT ON COLUMN "network_access_nda_record"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_nda_record"."updater" IS '更新人';
COMMENT ON TABLE "network_access_nda_record" IS 'Personnel NDA signing ledger.';
CREATE TABLE "network_access_notification_event" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "event_type" VARCHAR(80) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "content" TEXT,
    "risk_level" VARCHAR(20),
    "target_type" VARCHAR(80),
    "target_id" VARCHAR(36),
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "link" VARCHAR(255),
    "receivers" JSONB NOT NULL,
    "receiver_roles" JSONB NOT NULL,
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__f8c6fc" ON "network_access_notification_event" ("tenant_id", "event_type");
CREATE INDEX "idx_network_acc_tenant__87f8e3" ON "network_access_notification_event" ("tenant_id", "target_type", "target_id");
CREATE INDEX "idx_network_acc_tenant__b748a5" ON "network_access_notification_event" ("tenant_id", "status");
COMMENT ON COLUMN "network_access_notification_event"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_notification_event"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_notification_event"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_notification_event"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_notification_event"."event_type" IS '事件类型';
COMMENT ON COLUMN "network_access_notification_event"."title" IS '标题';
COMMENT ON COLUMN "network_access_notification_event"."content" IS '内容';
COMMENT ON COLUMN "network_access_notification_event"."risk_level" IS '风险等级';
COMMENT ON COLUMN "network_access_notification_event"."target_type" IS '目标类型';
COMMENT ON COLUMN "network_access_notification_event"."target_id" IS '目标 ID';
COMMENT ON COLUMN "network_access_notification_event"."project_area_id" IS '厂区资产 ID';
COMMENT ON COLUMN "network_access_notification_event"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_notification_event"."link" IS '链接';
COMMENT ON COLUMN "network_access_notification_event"."receivers" IS '接收人';
COMMENT ON COLUMN "network_access_notification_event"."receiver_roles" IS '接收角色';
COMMENT ON COLUMN "network_access_notification_event"."status" IS 'pending/sent/failed';
COMMENT ON COLUMN "network_access_notification_event"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_notification_event"."updater" IS '更新人';
COMMENT ON TABLE "network_access_notification_event" IS 'notify_config-compatible notification event payload.';
CREATE TABLE "network_access_parse_mapping_template" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "domain" VARCHAR(30) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "source_format" VARCHAR(20) NOT NULL DEFAULT 'json',
    "parser_type" VARCHAR(20) NOT NULL DEFAULT 'builtin',
    "parser_rules" JSONB NOT NULL,
    "mapping_rules" JSONB NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__db0449" ON "network_access_parse_mapping_template" ("tenant_id", "domain");
CREATE INDEX "idx_network_acc_tenant__eb8101" ON "network_access_parse_mapping_template" ("tenant_id", "enabled");
COMMENT ON COLUMN "network_access_parse_mapping_template"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_parse_mapping_template"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_parse_mapping_template"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_parse_mapping_template"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_parse_mapping_template"."domain" IS '采集域';
COMMENT ON COLUMN "network_access_parse_mapping_template"."name" IS '模板名称';
COMMENT ON COLUMN "network_access_parse_mapping_template"."source_format" IS 'json/csv/text/raw_config/log';
COMMENT ON COLUMN "network_access_parse_mapping_template"."parser_type" IS 'builtin/regex/json_path/script';
COMMENT ON COLUMN "network_access_parse_mapping_template"."parser_rules" IS '解析规则';
COMMENT ON COLUMN "network_access_parse_mapping_template"."mapping_rules" IS '映射规则';
COMMENT ON COLUMN "network_access_parse_mapping_template"."enabled" IS '是否启用';
COMMENT ON COLUMN "network_access_parse_mapping_template"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_parse_mapping_template"."updater" IS '更新人';
COMMENT ON TABLE "network_access_parse_mapping_template" IS 'Parser and mapping rules for collection results.';
CREATE TABLE "network_access_actual_policy" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "device_asset_id" VARCHAR(36),
    "device_key" VARCHAR(255) NOT NULL,
    "device_name" VARCHAR(255),
    "device_type" VARCHAR(30),
    "collection_run_id" VARCHAR(36),
    "raw_rule_id" VARCHAR(255),
    "policy_key" VARCHAR(255) NOT NULL,
    "policy_type" VARCHAR(50),
    "rule_name" VARCHAR(255),
    "source_zone" VARCHAR(100),
    "source_address" VARCHAR(255),
    "target_zone" VARCHAR(100),
    "target_address" VARCHAR(255),
    "protocol" VARCHAR(30),
    "port" VARCHAR(100),
    "action" VARCHAR(20) NOT NULL DEFAULT 'permit',
    "enabled" BOOL NOT NULL DEFAULT True,
    "raw_text" TEXT,
    "policy_data" JSONB NOT NULL,
    "parse_status" VARCHAR(20) NOT NULL DEFAULT 'parsed',
    "revision" INT NOT NULL DEFAULT 1,
    "last_synced_at" TIMESTAMPTZ,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__71d809" ON "network_access_actual_policy" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__9d5202" ON "network_access_actual_policy" ("tenant_id", "device_asset_id");
CREATE INDEX "idx_network_acc_tenant__9c52fb" ON "network_access_actual_policy" ("tenant_id", "device_key");
CREATE INDEX "idx_network_acc_tenant__e5cdd1" ON "network_access_actual_policy" ("tenant_id", "device_key", "policy_key");
CREATE INDEX "idx_network_acc_tenant__995683" ON "network_access_actual_policy" ("tenant_id", "collection_run_id");
CREATE INDEX "idx_network_acc_tenant__29a614" ON "network_access_actual_policy" ("tenant_id", "parse_status");
COMMENT ON COLUMN "network_access_actual_policy"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_actual_policy"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_actual_policy"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_actual_policy"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_actual_policy"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_actual_policy"."device_asset_id" IS '网络设备资产 ID';
COMMENT ON COLUMN "network_access_actual_policy"."device_key" IS '外部设备稳定标识';
COMMENT ON COLUMN "network_access_actual_policy"."device_name" IS '设备名称';
COMMENT ON COLUMN "network_access_actual_policy"."device_type" IS 'firewall/switch/other';
COMMENT ON COLUMN "network_access_actual_policy"."collection_run_id" IS '采集运行';
COMMENT ON COLUMN "network_access_actual_policy"."raw_rule_id" IS '原始规则 ID';
COMMENT ON COLUMN "network_access_actual_policy"."policy_key" IS '外部策略稳定标识';
COMMENT ON COLUMN "network_access_actual_policy"."policy_type" IS 'firewall/acl/other';
COMMENT ON COLUMN "network_access_actual_policy"."rule_name" IS '规则名称';
COMMENT ON COLUMN "network_access_actual_policy"."source_zone" IS '源区域';
COMMENT ON COLUMN "network_access_actual_policy"."source_address" IS '源地址';
COMMENT ON COLUMN "network_access_actual_policy"."target_zone" IS '目标区域';
COMMENT ON COLUMN "network_access_actual_policy"."target_address" IS '目标地址';
COMMENT ON COLUMN "network_access_actual_policy"."protocol" IS '协议';
COMMENT ON COLUMN "network_access_actual_policy"."port" IS '端口';
COMMENT ON COLUMN "network_access_actual_policy"."action" IS 'permit/deny';
COMMENT ON COLUMN "network_access_actual_policy"."enabled" IS '策略是否启用';
COMMENT ON COLUMN "network_access_actual_policy"."raw_text" IS '原始文本';
COMMENT ON COLUMN "network_access_actual_policy"."policy_data" IS '厂商策略完整结构化数据';
COMMENT ON COLUMN "network_access_actual_policy"."parse_status" IS 'parsed/unparsed';
COMMENT ON COLUMN "network_access_actual_policy"."revision" IS '策略版本';
COMMENT ON COLUMN "network_access_actual_policy"."last_synced_at" IS '最近同步时间';
COMMENT ON COLUMN "network_access_actual_policy"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_actual_policy"."updater" IS '更新人';
COMMENT ON TABLE "network_access_actual_policy" IS 'Current firewall, switch, or other network-device policy.';
CREATE TABLE "network_access_policy_change" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "device_asset_id" VARCHAR(36),
    "device_key" VARCHAR(255),
    "actual_policy_id" VARCHAR(36),
    "policy_key" VARCHAR(255),
    "batch_id" VARCHAR(64),
    "policy_name" VARCHAR(255),
    "change_type" VARCHAR(20) NOT NULL,
    "revision" INT NOT NULL DEFAULT 1,
    "before_data" JSONB NOT NULL,
    "after_data" JSONB NOT NULL,
    "sync_source" VARCHAR(100),
    "diff_text" TEXT,
    "detected_at" TIMESTAMPTZ,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'high',
    "external_approve_ref" VARCHAR(100),
    "external_approve_result" VARCHAR(100),
    "review_issue_id" VARCHAR(36),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__9594b4" ON "network_access_policy_change" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__6a3080" ON "network_access_policy_change" ("tenant_id", "device_asset_id");
CREATE INDEX "idx_network_acc_tenant__f9cb2d" ON "network_access_policy_change" ("tenant_id", "device_key");
CREATE INDEX "idx_network_acc_tenant__d98f72" ON "network_access_policy_change" ("tenant_id", "actual_policy_id");
CREATE INDEX "idx_network_acc_tenant__158444" ON "network_access_policy_change" ("tenant_id", "batch_id");
CREATE INDEX "idx_network_acc_tenant__69061b" ON "network_access_policy_change" ("tenant_id", "change_type");
CREATE INDEX "idx_network_acc_tenant__0f378b" ON "network_access_policy_change" ("tenant_id", "detected_at");
COMMENT ON COLUMN "network_access_policy_change"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_policy_change"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_policy_change"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_policy_change"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_policy_change"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_policy_change"."device_asset_id" IS '网络设备资产 ID';
COMMENT ON COLUMN "network_access_policy_change"."device_key" IS '外部设备稳定标识';
COMMENT ON COLUMN "network_access_policy_change"."actual_policy_id" IS '当前策略 ID';
COMMENT ON COLUMN "network_access_policy_change"."policy_key" IS '外部策略稳定标识';
COMMENT ON COLUMN "network_access_policy_change"."batch_id" IS '同步批次 ID';
COMMENT ON COLUMN "network_access_policy_change"."policy_name" IS '策略名称';
COMMENT ON COLUMN "network_access_policy_change"."change_type" IS 'added/modified/moved/deleted';
COMMENT ON COLUMN "network_access_policy_change"."revision" IS '策略写入后的版本';
COMMENT ON COLUMN "network_access_policy_change"."before_data" IS '写入前策略快照';
COMMENT ON COLUMN "network_access_policy_change"."after_data" IS '写入后策略快照';
COMMENT ON COLUMN "network_access_policy_change"."sync_source" IS '写入来源';
COMMENT ON COLUMN "network_access_policy_change"."diff_text" IS '差异文本';
COMMENT ON COLUMN "network_access_policy_change"."detected_at" IS '发现时间';
COMMENT ON COLUMN "network_access_policy_change"."risk_level" IS '风险等级';
COMMENT ON COLUMN "network_access_policy_change"."external_approve_ref" IS '外部审批单号';
COMMENT ON COLUMN "network_access_policy_change"."external_approve_result" IS '外部审批结果';
COMMENT ON COLUMN "network_access_policy_change"."review_issue_id" IS '审查问题';
COMMENT ON COLUMN "network_access_policy_change"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_policy_change"."updater" IS '更新人';
COMMENT ON TABLE "network_access_policy_change" IS 'Immutable before/after history for access-control policy writes.';
CREATE TABLE "network_access_policy_compare_item" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "compare_run_id" VARCHAR(36) NOT NULL,
    "preset_policy_id" VARCHAR(36),
    "actual_policy_id" VARCHAR(36),
    "compare_type" VARCHAR(20) NOT NULL,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "handle_status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "review_issue_id" VARCHAR(36),
    "diff" JSONB NOT NULL,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__f2fb69" ON "network_access_policy_compare_item" ("tenant_id", "compare_run_id");
CREATE INDEX "idx_network_acc_tenant__52226f" ON "network_access_policy_compare_item" ("tenant_id", "compare_type");
CREATE INDEX "idx_network_acc_tenant__2225f0" ON "network_access_policy_compare_item" ("tenant_id", "handle_status");
COMMENT ON COLUMN "network_access_policy_compare_item"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_policy_compare_item"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_policy_compare_item"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_policy_compare_item"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_policy_compare_item"."compare_run_id" IS '比对运行';
COMMENT ON COLUMN "network_access_policy_compare_item"."preset_policy_id" IS '预设策略';
COMMENT ON COLUMN "network_access_policy_compare_item"."actual_policy_id" IS '实际策略';
COMMENT ON COLUMN "network_access_policy_compare_item"."compare_type" IS 'matched/added/missing/changed/unparsed';
COMMENT ON COLUMN "network_access_policy_compare_item"."risk_level" IS '风险等级';
COMMENT ON COLUMN "network_access_policy_compare_item"."handle_status" IS 'pending/confirmed/ignored/converted';
COMMENT ON COLUMN "network_access_policy_compare_item"."review_issue_id" IS '审查问题';
COMMENT ON COLUMN "network_access_policy_compare_item"."diff" IS '差异';
COMMENT ON COLUMN "network_access_policy_compare_item"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_policy_compare_item"."updater" IS '更新人';
COMMENT ON TABLE "network_access_policy_compare_item" IS 'Single policy compare item.';
CREATE TABLE "network_access_policy_compare_run" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "collection_run_id" VARCHAR(36),
    "total" INT NOT NULL DEFAULT 0,
    "matched" INT NOT NULL DEFAULT 0,
    "added" INT NOT NULL DEFAULT 0,
    "missing" INT NOT NULL DEFAULT 0,
    "changed" INT NOT NULL DEFAULT 0,
    "unparsed" INT NOT NULL DEFAULT 0,
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__138841" ON "network_access_policy_compare_run" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__84372c" ON "network_access_policy_compare_run" ("tenant_id", "collection_run_id");
COMMENT ON COLUMN "network_access_policy_compare_run"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_policy_compare_run"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_policy_compare_run"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_policy_compare_run"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_policy_compare_run"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_policy_compare_run"."collection_run_id" IS '采集运行';
COMMENT ON COLUMN "network_access_policy_compare_run"."total" IS '总数';
COMMENT ON COLUMN "network_access_policy_compare_run"."matched" IS '匹配数';
COMMENT ON COLUMN "network_access_policy_compare_run"."added" IS '新增数';
COMMENT ON COLUMN "network_access_policy_compare_run"."missing" IS '缺失数';
COMMENT ON COLUMN "network_access_policy_compare_run"."changed" IS '变更数';
COMMENT ON COLUMN "network_access_policy_compare_run"."unparsed" IS '未解析数';
COMMENT ON COLUMN "network_access_policy_compare_run"."started_at" IS '开始时间';
COMMENT ON COLUMN "network_access_policy_compare_run"."finished_at" IS '结束时间';
COMMENT ON COLUMN "network_access_policy_compare_run"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_policy_compare_run"."updater" IS '更新人';
COMMENT ON TABLE "network_access_policy_compare_run" IS 'Policy compare run summary.';
CREATE TABLE "network_access_preset_policy" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "device_asset_id" VARCHAR(36),
    "rule_name" VARCHAR(255) NOT NULL,
    "source_zone" VARCHAR(100),
    "source_address" VARCHAR(255),
    "target_zone" VARCHAR(100),
    "target_address" VARCHAR(255),
    "protocol" VARCHAR(30),
    "port" VARCHAR(100),
    "action" VARCHAR(20) NOT NULL DEFAULT 'permit',
    "business_purpose" TEXT,
    "approval_status" VARCHAR(30),
    "effective_status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "valid_from" DATE,
    "valid_to" DATE,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__a42f60" ON "network_access_preset_policy" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__b2be12" ON "network_access_preset_policy" ("tenant_id", "device_asset_id");
CREATE INDEX "idx_network_acc_tenant__15e2ae" ON "network_access_preset_policy" ("tenant_id", "effective_status");
COMMENT ON COLUMN "network_access_preset_policy"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_preset_policy"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_preset_policy"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_preset_policy"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_preset_policy"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_preset_policy"."device_asset_id" IS '网络设备资产 ID';
COMMENT ON COLUMN "network_access_preset_policy"."rule_name" IS '规则名称';
COMMENT ON COLUMN "network_access_preset_policy"."source_zone" IS '源区域';
COMMENT ON COLUMN "network_access_preset_policy"."source_address" IS '源地址';
COMMENT ON COLUMN "network_access_preset_policy"."target_zone" IS '目标区域';
COMMENT ON COLUMN "network_access_preset_policy"."target_address" IS '目标地址';
COMMENT ON COLUMN "network_access_preset_policy"."protocol" IS '协议';
COMMENT ON COLUMN "network_access_preset_policy"."port" IS '端口';
COMMENT ON COLUMN "network_access_preset_policy"."action" IS 'permit/deny';
COMMENT ON COLUMN "network_access_preset_policy"."business_purpose" IS '业务用途';
COMMENT ON COLUMN "network_access_preset_policy"."approval_status" IS '外部审批状态';
COMMENT ON COLUMN "network_access_preset_policy"."effective_status" IS 'active/disabled';
COMMENT ON COLUMN "network_access_preset_policy"."valid_from" IS '生效日期';
COMMENT ON COLUMN "network_access_preset_policy"."valid_to" IS '失效日期';
COMMENT ON COLUMN "network_access_preset_policy"."risk_level" IS '风险等级';
COMMENT ON COLUMN "network_access_preset_policy"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_preset_policy"."updater" IS '更新人';
COMMENT ON TABLE "network_access_preset_policy" IS 'Expected network policy baseline.';
CREATE TABLE "network_access_project_authorization" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "personnel_asset_id" VARCHAR(36) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "secrecy_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "project_role" VARCHAR(100),
    "authorized_from" DATE,
    "authorized_to" DATE,
    "access_control_status" VARCHAR(20) NOT NULL DEFAULT 'normal',
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__3c7413" ON "network_access_project_authorization" ("tenant_id", "project_area_id");
CREATE INDEX "idx_network_acc_tenant__f8e3ea" ON "network_access_project_authorization" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__784b48" ON "network_access_project_authorization" ("tenant_id", "personnel_asset_id");
CREATE INDEX "idx_network_acc_tenant__ee80d3" ON "network_access_project_authorization" ("tenant_id", "status");
COMMENT ON COLUMN "network_access_project_authorization"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_project_authorization"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_project_authorization"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_project_authorization"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_project_authorization"."personnel_asset_id" IS '人员资产 ID';
COMMENT ON COLUMN "network_access_project_authorization"."project_area_id" IS '厂区/区域资产 ID';
COMMENT ON COLUMN "network_access_project_authorization"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_project_authorization"."secrecy_level" IS 'low/medium/high/critical';
COMMENT ON COLUMN "network_access_project_authorization"."project_role" IS '项目角色';
COMMENT ON COLUMN "network_access_project_authorization"."authorized_from" IS '授权开始日期';
COMMENT ON COLUMN "network_access_project_authorization"."authorized_to" IS '授权结束日期';
COMMENT ON COLUMN "network_access_project_authorization"."access_control_status" IS 'normal/abnormal/revoked';
COMMENT ON COLUMN "network_access_project_authorization"."status" IS 'active/expired/revoked';
COMMENT ON COLUMN "network_access_project_authorization"."remark" IS '备注';
COMMENT ON COLUMN "network_access_project_authorization"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_project_authorization"."updater" IS '更新人';
COMMENT ON TABLE "network_access_project_authorization" IS 'Personnel project authorization ledger.';
CREATE TABLE "network_access_remediation_record" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "review_issue_id" VARCHAR(36) NOT NULL,
    "action_type" VARCHAR(30) NOT NULL,
    "content" TEXT,
    "attachment_ids" JSONB NOT NULL,
    "operator" VARCHAR(255),
    "operated_at" TIMESTAMPTZ,
    "from_status" VARCHAR(30),
    "to_status" VARCHAR(30),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__900957" ON "network_access_remediation_record" ("tenant_id", "review_issue_id");
CREATE INDEX "idx_network_acc_tenant__e51ff3" ON "network_access_remediation_record" ("tenant_id", "action_type");
COMMENT ON COLUMN "network_access_remediation_record"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_remediation_record"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_remediation_record"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_remediation_record"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_remediation_record"."review_issue_id" IS '审查问题';
COMMENT ON COLUMN "network_access_remediation_record"."action_type" IS '动作类型';
COMMENT ON COLUMN "network_access_remediation_record"."content" IS '内容';
COMMENT ON COLUMN "network_access_remediation_record"."attachment_ids" IS '附件';
COMMENT ON COLUMN "network_access_remediation_record"."operator" IS '操作人';
COMMENT ON COLUMN "network_access_remediation_record"."operated_at" IS '操作时间';
COMMENT ON COLUMN "network_access_remediation_record"."from_status" IS '原状态';
COMMENT ON COLUMN "network_access_remediation_record"."to_status" IS '新状态';
COMMENT ON COLUMN "network_access_remediation_record"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_remediation_record"."updater" IS '更新人';
COMMENT ON TABLE "network_access_remediation_record" IS 'Status transition and remediation evidence record.';
CREATE TABLE "network_access_review_issue" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "domain" VARCHAR(30) NOT NULL,
    "source_object_type" VARCHAR(80),
    "source_object_id" VARCHAR(36),
    "issue_no" VARCHAR(64) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "remediation_requirement" TEXT,
    "responsible_person" VARCHAR(255),
    "due_date" DATE,
    "status" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "attachment_ids" JSONB NOT NULL,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_network_acc_tenant__d3a0e8" UNIQUE ("tenant_id", "issue_no")
);
CREATE INDEX "idx_network_acc_tenant__c72447" ON "network_access_review_issue" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__8d64db" ON "network_access_review_issue" ("tenant_id", "domain");
CREATE INDEX "idx_network_acc_tenant__d0e0e3" ON "network_access_review_issue" ("tenant_id", "status");
CREATE INDEX "idx_network_acc_tenant__23ae91" ON "network_access_review_issue" ("tenant_id", "risk_level");
COMMENT ON COLUMN "network_access_review_issue"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_review_issue"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_review_issue"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_review_issue"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_review_issue"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_review_issue"."domain" IS '问题域';
COMMENT ON COLUMN "network_access_review_issue"."source_object_type" IS '来源对象类型';
COMMENT ON COLUMN "network_access_review_issue"."source_object_id" IS '来源对象 ID';
COMMENT ON COLUMN "network_access_review_issue"."issue_no" IS '问题编号';
COMMENT ON COLUMN "network_access_review_issue"."title" IS '标题';
COMMENT ON COLUMN "network_access_review_issue"."description" IS '描述';
COMMENT ON COLUMN "network_access_review_issue"."risk_level" IS '风险等级';
COMMENT ON COLUMN "network_access_review_issue"."remediation_requirement" IS '整改要求';
COMMENT ON COLUMN "network_access_review_issue"."responsible_person" IS '责任人';
COMMENT ON COLUMN "network_access_review_issue"."due_date" IS '截止日期';
COMMENT ON COLUMN "network_access_review_issue"."status" IS 'pending/in_progress/pending_review/closed/returned';
COMMENT ON COLUMN "network_access_review_issue"."attachment_ids" IS '附件';
COMMENT ON COLUMN "network_access_review_issue"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_review_issue"."updater" IS '更新人';
COMMENT ON TABLE "network_access_review_issue" IS 'Review issue and remediation closure workflow.';
CREATE TABLE "network_access_network_topology" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "name" VARCHAR(255) NOT NULL,
    "version" VARCHAR(50) NOT NULL DEFAULT 'v1',
    "graph_data" JSONB NOT NULL,
    "change_summary" TEXT,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__658076" ON "network_access_network_topology" ("tenant_id", "project_area_id");
CREATE INDEX "idx_network_acc_tenant__c9d647" ON "network_access_network_topology" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__ae88ed" ON "network_access_network_topology" ("tenant_id", "status");
COMMENT ON COLUMN "network_access_network_topology"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_network_topology"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_network_topology"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_network_topology"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_network_topology"."project_area_id" IS '厂区/区域资产 ID';
COMMENT ON COLUMN "network_access_network_topology"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_network_topology"."name" IS '拓扑名称';
COMMENT ON COLUMN "network_access_network_topology"."version" IS '版本';
COMMENT ON COLUMN "network_access_network_topology"."graph_data" IS 'LogicFlow 数据';
COMMENT ON COLUMN "network_access_network_topology"."change_summary" IS '变更说明';
COMMENT ON COLUMN "network_access_network_topology"."status" IS 'active/archived';
COMMENT ON COLUMN "network_access_network_topology"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_network_topology"."updater" IS '更新人';
COMMENT ON TABLE "network_access_network_topology" IS 'Stored LogicFlow network topology.';
CREATE TABLE "network_access_unknown_terminal" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "mac_address" VARCHAR(64) NOT NULL,
    "ip_address" VARCHAR(64),
    "network_device_asset_id" VARCHAR(36),
    "port" VARCHAR(100),
    "first_seen_at" TIMESTAMPTZ,
    "last_seen_at" TIMESTAMPTZ,
    "discovery_source" VARCHAR(30) NOT NULL DEFAULT 'switch_mac',
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "endpoint_asset_id" VARCHAR(36),
    "review_issue_id" VARCHAR(36),
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
CREATE INDEX "idx_network_acc_tenant__f092e7" ON "network_access_unknown_terminal" ("tenant_id", "accessory_project_id");
CREATE INDEX "idx_network_acc_tenant__c078c0" ON "network_access_unknown_terminal" ("tenant_id", "mac_address");
CREATE INDEX "idx_network_acc_tenant__5f0bd0" ON "network_access_unknown_terminal" ("tenant_id", "status");
CREATE INDEX "idx_network_acc_tenant__9038fa" ON "network_access_unknown_terminal" ("tenant_id", "last_seen_at");
COMMENT ON COLUMN "network_access_unknown_terminal"."ctime" IS '创建时间';
COMMENT ON COLUMN "network_access_unknown_terminal"."utime" IS '更新时间';
COMMENT ON COLUMN "network_access_unknown_terminal"."deleted" IS '删除标识';
COMMENT ON COLUMN "network_access_unknown_terminal"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "network_access_unknown_terminal"."accessory_project_id" IS '配件项目资产 ID';
COMMENT ON COLUMN "network_access_unknown_terminal"."mac_address" IS 'MAC 地址';
COMMENT ON COLUMN "network_access_unknown_terminal"."ip_address" IS 'IP 地址';
COMMENT ON COLUMN "network_access_unknown_terminal"."network_device_asset_id" IS '网络设备资产 ID';
COMMENT ON COLUMN "network_access_unknown_terminal"."port" IS '交换机端口';
COMMENT ON COLUMN "network_access_unknown_terminal"."first_seen_at" IS '首次发现';
COMMENT ON COLUMN "network_access_unknown_terminal"."last_seen_at" IS '最近发现';
COMMENT ON COLUMN "network_access_unknown_terminal"."discovery_source" IS 'switch_mac/arp/scan';
COMMENT ON COLUMN "network_access_unknown_terminal"."status" IS 'pending/confirmed/converted';
COMMENT ON COLUMN "network_access_unknown_terminal"."endpoint_asset_id" IS '终端资产 ID';
COMMENT ON COLUMN "network_access_unknown_terminal"."review_issue_id" IS '审查问题';
COMMENT ON COLUMN "network_access_unknown_terminal"."remark" IS '备注';
COMMENT ON COLUMN "network_access_unknown_terminal"."creator" IS '创建人';
COMMENT ON COLUMN "network_access_unknown_terminal"."updater" IS '更新人';
COMMENT ON TABLE "network_access_unknown_terminal" IS 'Network-side unknown terminal discovery ledger.';

DELETE FROM "permissions_menu"
WHERE "model" = 'supply-chain-review'
   OR "bp" LIKE 'supply_chain_review%';

UPDATE "permissions_menu"
SET "Permissions" = '["asset_management:c","asset_management:r","asset_management:u","asset_management:d"]',
    "bp" = 'asset_management_bp',
    "url" = replace("url"::text, 'views/securityManagement/index.vue', 'views/assetManagement/index.vue')::jsonb,
    "utime" = CURRENT_TIMESTAMP
WHERE "model" IN ('asset-management-models', 'asset-management-catalog');

UPDATE "permissions_menu"
SET "Permissions" = '["network_access_control:c","network_access_control:r","network_access_control:u","network_access_control:d"]',
    "bp" = 'network_access_control_bp',
    "url" = replace("url"::text, 'views/securityManagement/network.vue', 'views/networkManagement/index.vue')::jsonb,
    "utime" = CURRENT_TIMESTAMP
WHERE "model" = 'network-management-access-control';

UPDATE "role" AS role_row
SET "permissions" = (
    SELECT COALESCE(jsonb_agg(permission ORDER BY permission), '[]'::jsonb)
    FROM (
        SELECT DISTINCT permission
        FROM jsonb_array_elements_text(COALESCE(role_row."permissions", '[]'::jsonb)) permission
        WHERE permission NOT LIKE 'supply_chain_review:%'
    ) retained
),
"utime" = CURRENT_TIMESTAMP
WHERE EXISTS (
    SELECT 1
    FROM jsonb_array_elements_text(COALESCE(role_row."permissions", '[]'::jsonb)) permission
    WHERE permission LIKE 'supply_chain_review:%'
);

UPDATE "role" AS role_row
SET "permissions" = (
    SELECT jsonb_agg(DISTINCT permission ORDER BY permission)
    FROM jsonb_array_elements_text(
        COALESCE(role_row."permissions", '[]'::jsonb) ||
        '["asset_management:c","asset_management:r","asset_management:u","asset_management:d","network_access_control:c","network_access_control:r","network_access_control:u","network_access_control:d"]'::jsonb
    ) permission
),
"utime" = CURRENT_TIMESTAMP
WHERE role_row."name" = '租户管理员';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return r"""
DROP TABLE IF EXISTS "network_access_unknown_terminal" CASCADE;
DROP TABLE IF EXISTS "network_access_network_topology" CASCADE;
DROP TABLE IF EXISTS "network_access_review_issue" CASCADE;
DROP TABLE IF EXISTS "network_access_remediation_record" CASCADE;
DROP TABLE IF EXISTS "network_access_project_authorization" CASCADE;
DROP TABLE IF EXISTS "network_access_preset_policy" CASCADE;
DROP TABLE IF EXISTS "network_access_policy_compare_run" CASCADE;
DROP TABLE IF EXISTS "network_access_policy_compare_item" CASCADE;
DROP TABLE IF EXISTS "network_access_policy_change" CASCADE;
DROP TABLE IF EXISTS "network_access_actual_policy" CASCADE;
DROP TABLE IF EXISTS "network_access_parse_mapping_template" CASCADE;
DROP TABLE IF EXISTS "network_access_notification_event" CASCADE;
DROP TABLE IF EXISTS "network_access_nda_record" CASCADE;
DROP TABLE IF EXISTS "network_access_exception_device_log" CASCADE;
DROP TABLE IF EXISTS "network_access_disposal_task" CASCADE;
DROP TABLE IF EXISTS "network_access_disposal_rule" CASCADE;
DROP TABLE IF EXISTS "network_access_collection_task" CASCADE;
DROP TABLE IF EXISTS "network_access_collection_run" CASCADE;
DROP TABLE IF EXISTS "network_access_boundary_log" CASCADE;
DROP TABLE IF EXISTS "network_access_attachment_ref" CASCADE;
DROP TABLE IF EXISTS "asset_unique_rule" CASCADE;
DROP TABLE IF EXISTS "asset_reference" CASCADE;
DROP TABLE IF EXISTS "asset_model_group" CASCADE;
DROP TABLE IF EXISTS "asset_model" CASCADE;
DROP TABLE IF EXISTS "asset_instance" CASCADE;
DROP TABLE IF EXISTS "asset_field_definition" CASCADE;
DROP TABLE IF EXISTS "asset_builtin_catalog_state" CASCADE;
DROP TABLE IF EXISTS "asset_builtin_catalog_event" CASCADE;
DROP TABLE IF EXISTS "asset_builtin_activation_preview" CASCADE;
DROP TABLE IF EXISTS "asset_builtin_activation_event" CASCADE;
DROP TABLE IF EXISTS "asset_builtin_activation" CASCADE;
    """
