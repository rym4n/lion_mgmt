"""Network policy auto-collection tables and the internal sync permission."""

from __future__ import annotations

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

_BASE = (
    '"id" VARCHAR(36) NOT NULL PRIMARY KEY, '
    '"ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, '
    '"utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, '
    '"deleted" BOOL NOT NULL DEFAULT FALSE, '
    '"tenant_id" VARCHAR(255) NOT NULL'
)


def _table(name: str, columns: tuple[str, ...]) -> str:
    return f'CREATE TABLE IF NOT EXISTS "{name}" (\n    ' + ",\n    ".join((_BASE, *columns)) + "\n);"


PERMISSIONS = """[
    "network_access_control:c",
    "network_access_control:r",
    "network_access_control:u",
    "network_access_control:d",
    {"label": "同步策略", "value": "network_access_control:sync"}
]"""

LEGACY_PERMISSIONS = (
    '["network_access_control:c","network_access_control:r",'
    '"network_access_control:u","network_access_control:d"]'
)

_DDL = (
    _table("network_access_policy_collection_plan", (
        '"enabled" BOOL NOT NULL DEFAULT FALSE',
        '"schedule_cron" VARCHAR(100) NOT NULL DEFAULT \'0 0 3 * * *\'',
        '"concurrency" INT NOT NULL DEFAULT 8', '"per_item_timeout_seconds" INT NOT NULL DEFAULT 300',
        '"run_timeout_seconds" INT NOT NULL DEFAULT 3600', '"notify_enabled" BOOL NOT NULL DEFAULT FALSE',
        '"notify_config" JSONB', '"timed_id" VARCHAR(36)', '"last_run_id" VARCHAR(36)',
        '"consecutive_failures" INT NOT NULL DEFAULT 0', '"revision" INT NOT NULL DEFAULT 1',
        '"updater" VARCHAR(255)',
    )),
    _table("network_access_policy_collection_item", (
        '"plan_id" VARCHAR(36) NOT NULL', '"app_name" VARCHAR(255) NOT NULL', '"app_version" VARCHAR(50)',
        '"action_func" VARCHAR(255) NOT NULL', '"action_input" JSONB',
        '"resources" JSONB NOT NULL DEFAULT \'[]\'::jsonb', '"enabled" BOOL NOT NULL DEFAULT TRUE',
        '"order" INT NOT NULL DEFAULT 0',
    )),
    _table("network_access_policy_collection_run", (
        '"plan_id" VARCHAR(36) NOT NULL', '"trigger" VARCHAR(16) NOT NULL', '"triggered_by" VARCHAR(255)',
        '"state" VARCHAR(20) NOT NULL DEFAULT \'PENDING\'', '"flow_run_id" VARCHAR(64)',
        '"frozen_targets" JSONB', '"raw_outputs" TEXT', '"item_results" JSONB',
        '"device_total" INT NOT NULL DEFAULT 0', '"device_succeeded" INT NOT NULL DEFAULT 0',
        '"agent_job_id" VARCHAR(36)', '"compare_run_id" VARCHAR(36)', '"compare_error" VARCHAR(64)',
        '"error_code" VARCHAR(64)', '"error_message" TEXT', '"started_at" TIMESTAMPTZ',
        '"finished_at" TIMESTAMPTZ', '"heartbeat_at" TIMESTAMPTZ',
    )),
    _table("network_access_policy_agent_job", (
        '"collection_run_id" VARCHAR(36) NOT NULL', '"attempt" INT NOT NULL',
        '"state" VARCHAR(16) NOT NULL DEFAULT \'QUEUED\'', '"resource_ids" JSONB NOT NULL DEFAULT \'[]\'::jsonb',
        '"job_token_hash" VARCHAR(64) NOT NULL', '"profile_version" VARCHAR(255)',
        '"cache_hits" INT NOT NULL DEFAULT 0', '"cache_misses" INT NOT NULL DEFAULT 0',
        '"iterations" INT NOT NULL DEFAULT 0', '"token_usage" JSONB', '"documents" TEXT', '"failed" TEXT',
        '"transcript" TEXT', '"error_code" VARCHAR(128)', '"error_message" TEXT',
        '"started_at" TIMESTAMPTZ', '"finished_at" TIMESTAMPTZ',
    )),
    'CREATE UNIQUE INDEX IF NOT EXISTS "uidx_policy_collection_plan_tenant" '
    'ON "network_access_policy_collection_plan" ("tenant_id") WHERE "deleted" = FALSE;',
    'CREATE UNIQUE INDEX IF NOT EXISTS "uidx_policy_collection_run_active" '
    'ON "network_access_policy_collection_run" ("tenant_id") WHERE "deleted" = FALSE '
    'AND "state" NOT IN (\'SUCCEEDED\', \'PARTIAL\', \'FAILED\', \'CANCELLED\');',
    'CREATE INDEX IF NOT EXISTS "idx_policy_collection_item_plan" '
    'ON "network_access_policy_collection_item" ("tenant_id", "plan_id");',
    'CREATE INDEX IF NOT EXISTS "idx_policy_collection_run_plan" '
    'ON "network_access_policy_collection_run" ("tenant_id", "plan_id", "ctime");',
    'CREATE INDEX IF NOT EXISTS "idx_policy_collection_run_state" '
    'ON "network_access_policy_collection_run" ("tenant_id", "state");',
    'CREATE INDEX IF NOT EXISTS "idx_policy_agent_job_run" '
    'ON "network_access_policy_agent_job" ("tenant_id", "collection_run_id");',
    f"""UPDATE "permissions_menu"
SET "Permissions" = '{PERMISSIONS}'::jsonb, "utime" = CURRENT_TIMESTAMP
WHERE "model" = 'network-management-access-control';""",
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return "\n\n".join(_DDL)


async def downgrade(db: BaseDBAsyncClient) -> str:
    return "\n".join([
        f"""UPDATE "permissions_menu" SET "Permissions" = '{LEGACY_PERMISSIONS}'::jsonb,
"utime" = CURRENT_TIMESTAMP WHERE "model" = 'network-management-access-control';""",
        'DROP TABLE IF EXISTS "network_access_policy_agent_job";',
        'DROP TABLE IF EXISTS "network_access_policy_collection_run";',
        'DROP TABLE IF EXISTS "network_access_policy_collection_item";',
        'DROP TABLE IF EXISTS "network_access_policy_collection_plan";',
    ])
