from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE MATERIALIZED VIEW public.runlog_playbook_success_rate AS
        SELECT
            tenant_id,
            playbook_name,
            ROUND(
                SUM(CASE WHEN has_fail = 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*),
                2
            ) AS success_rate
        FROM (
            SELECT
                tenant_id,
                run_id,
                playbook_name,
                MAX(CASE WHEN status != 0 THEN 1 ELSE 0 END) AS has_fail
            FROM public.runlog
            WHERE playbook_name != '执行动作'
            GROUP BY tenant_id, run_id, playbook_name
        ) AS deduplicated
        GROUP BY tenant_id, playbook_name;

        CREATE UNIQUE INDEX idx_runlog_playbook_success_rate_refresh_unique
        ON public.runlog_playbook_success_rate (tenant_id, playbook_name);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP MATERIALIZED VIEW IF EXISTS public.runlog_playbook_success_rate;
    """
