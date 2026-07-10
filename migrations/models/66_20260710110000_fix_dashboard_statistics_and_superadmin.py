from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

DASHBOARD_VIEWS_SQL = """
    DROP MATERIALIZED VIEW IF EXISTS runlog_playbook;
    DROP MATERIALIZED VIEW IF EXISTS runlog_action;

    CREATE MATERIALIZED VIEW runlog_playbook AS
    SELECT
        executions.day,
        executions.playbook_name,
        COUNT(*) AS total,
        executions.tenant_id
    FROM (
        SELECT
            MIN(r.ctime)::date AS day,
            r.playbook_name,
            r.run_id,
            r.tenant_id
        FROM runlog AS r
        INNER JOIN playbook_version AS pv ON pv.id = r.playbook_version_id
        INNER JOIN playbook AS p ON p.id = pv.playbook_id
        WHERE r.deleted = FALSE
          AND p.internal = FALSE
          AND r.playbook_name IS NOT NULL
        GROUP BY r.run_id, r.playbook_name, r.tenant_id
    ) AS executions
    GROUP BY executions.tenant_id, executions.day, executions.playbook_name;

    CREATE MATERIALIZED VIEW runlog_action AS
    SELECT
        r.ctime::date AS day,
        r.action_display_name,
        COUNT(r.id) AS total,
        r.tenant_id
    FROM runlog AS r
    LEFT JOIN playbook_version AS pv ON pv.id = r.playbook_version_id
    LEFT JOIN playbook AS p ON p.id = pv.playbook_id
    WHERE r.deleted = FALSE
      AND r.node_type = 'action-node'
      AND r.action_display_name IS NOT NULL
      AND COALESCE(p.internal, FALSE) = FALSE
    GROUP BY r.tenant_id, day, r.action_display_name;
"""

LEGACY_DASHBOARD_VIEWS_SQL = """
    DROP MATERIALIZED VIEW IF EXISTS runlog_playbook;
    DROP MATERIALIZED VIEW IF EXISTS runlog_action;

    CREATE MATERIALIZED VIEW runlog_playbook AS
    SELECT ctime::date AS day, playbook_name, COUNT(id) AS total, tenant_id
    FROM runlog
    GROUP BY tenant_id, day, playbook_name;

    CREATE MATERIALIZED VIEW runlog_action AS
    SELECT ctime::date AS day, action_display_name, COUNT(id) AS total, tenant_id
    FROM runlog
    GROUP BY tenant_id, day, action_display_name;
"""


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE role
        SET
            permissions = COALESCE(permissions, '[]'::jsonb) || '["*:*"]'::jsonb,
            utime = CURRENT_TIMESTAMP
        WHERE id = '111111'
          AND deleted = FALSE
          AND NOT (COALESCE(permissions, '[]'::jsonb) ? '*:*');

        CREATE INDEX IF NOT EXISTS idx_approvement_pending_expiry
        ON approvement (tenant_id, expire_time)
        WHERE status = 0 AND deleted = FALSE;

        {DASHBOARD_VIEWS_SQL}
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        DROP INDEX IF EXISTS idx_approvement_pending_expiry;
        {LEGACY_DASHBOARD_VIEWS_SQL}
    """
