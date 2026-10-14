"""Schema for topology automatic collection plans, items and runs."""

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


_DDL = (
    _table("network_topology_collection_plan", (
        '"topology_id" VARCHAR(36) NOT NULL', '"enabled" BOOL NOT NULL DEFAULT FALSE',
        '"schedule_cron" VARCHAR(100) NOT NULL DEFAULT \'0 0 2 * * *\'', '"concurrency" INT NOT NULL DEFAULT 8',
        '"per_item_timeout_seconds" INT NOT NULL DEFAULT 300', '"run_timeout_seconds" INT NOT NULL DEFAULT 3600',
        '"notify_enabled" BOOL NOT NULL DEFAULT FALSE', '"notify_config" JSONB', '"timed_id" VARCHAR(36)',
        '"last_run_id" VARCHAR(36)', '"consecutive_failures" INT NOT NULL DEFAULT 0', '"revision" INT NOT NULL DEFAULT 1',
        '"updater" VARCHAR(255)',
    )),
    _table("network_topology_collection_item", (
        '"plan_id" VARCHAR(36) NOT NULL', '"app_name" VARCHAR(255) NOT NULL', '"app_version" VARCHAR(50)',
        '"action_func" VARCHAR(255) NOT NULL', '"action_input" JSONB', '"resources" JSONB NOT NULL DEFAULT \'[]\'::jsonb',
        '"include_new_resources" BOOL NOT NULL DEFAULT FALSE', '"enabled" BOOL NOT NULL DEFAULT TRUE', '"order" INT NOT NULL DEFAULT 0',
    )),
    _table("network_topology_collection_run", (
        '"plan_id" VARCHAR(36) NOT NULL', '"topology_id" VARCHAR(36) NOT NULL', '"trigger" VARCHAR(16) NOT NULL',
        '"triggered_by" VARCHAR(255)', '"state" VARCHAR(20) NOT NULL DEFAULT \'PENDING\'', '"flow_run_id" VARCHAR(64)',
        '"manifest_id" VARCHAR(255)', '"sync_id" VARCHAR(255)', '"snapshot_id" VARCHAR(255)', '"analysis_run_id" VARCHAR(36)',
        '"snapshot_draft" TEXT', '"frozen_targets" JSONB', '"item_results" JSONB', '"device_total" INT NOT NULL DEFAULT 0',
        '"device_succeeded" INT NOT NULL DEFAULT 0', '"result" VARCHAR(16)', '"graph_revision" BIGINT', '"error_code" VARCHAR(64)',
        '"error_message" TEXT', '"started_at" TIMESTAMPTZ', '"finished_at" TIMESTAMPTZ',
    )),
    'CREATE UNIQUE INDEX IF NOT EXISTS "uidx_topology_collection_plan_topology" ON "network_topology_collection_plan" ("tenant_id", "topology_id") WHERE "deleted" = FALSE;',
    'CREATE UNIQUE INDEX IF NOT EXISTS "uidx_topology_collection_run_active" ON "network_topology_collection_run" ("tenant_id", "topology_id") WHERE "deleted" = FALSE AND "state" NOT IN (\'SUCCEEDED\', \'FAILED\', \'CANCELLED\');',
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return "\n\n".join(_DDL)


async def downgrade(db: BaseDBAsyncClient) -> str:
    return "\n".join([
        'DROP TABLE IF EXISTS "network_topology_collection_run";',
        'DROP TABLE IF EXISTS "network_topology_collection_item";',
        'DROP TABLE IF EXISTS "network_topology_collection_plan";',
    ])
