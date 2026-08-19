from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS public.timed_run_summary (
            timer_id VARCHAR(36) NOT NULL REFERENCES public.timed (id) ON DELETE CASCADE,
            run_id VARCHAR(36) NOT NULL,
            latest_start_time TIMESTAMPTZ,
            latest_ctime TIMESTAMPTZ NOT NULL,
            is_running BOOLEAN NOT NULL,
            is_success BOOLEAN NOT NULL,
            PRIMARY KEY (timer_id, run_id)
        );

        WITH aggregated_runs AS (
            SELECT
                timer_id,
                run_id,
                MAX(start_time) AS latest_start_time,
                MAX(ctime) AS latest_ctime,
                BOOL_OR(status IN (1, 2)) AS is_running,
                BOOL_AND(status = 0) AS is_success
            FROM public.runlog
            WHERE timer_id IS NOT NULL AND deleted = FALSE
            GROUP BY timer_id, run_id
        ), ranked_runs AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY timer_id
                    ORDER BY latest_start_time DESC, latest_ctime DESC, run_id
                ) AS run_rank
            FROM aggregated_runs
        )
        INSERT INTO public.timed_run_summary (
            timer_id,
            run_id,
            latest_start_time,
            latest_ctime,
            is_running,
            is_success
        )
        SELECT
            timer_id,
            run_id,
            latest_start_time,
            latest_ctime,
            is_running,
            is_success
        FROM ranked_runs
        WHERE run_rank <= 10
        ON CONFLICT (timer_id, run_id) DO UPDATE SET
            latest_start_time = EXCLUDED.latest_start_time,
            latest_ctime = EXCLUDED.latest_ctime,
            is_running = EXCLUDED.is_running,
            is_success = EXCLUDED.is_success;

        DELETE FROM public.timed_run_summary AS summary
        USING (
            SELECT timer_id, run_id
            FROM (
                SELECT
                    timer_id,
                    run_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY timer_id
                        ORDER BY latest_start_time DESC, latest_ctime DESC, run_id
                    ) AS run_rank
                FROM public.timed_run_summary
            ) AS ranked
            WHERE run_rank > 10
        ) AS expired
        WHERE summary.timer_id = expired.timer_id
          AND summary.run_id = expired.run_id;

        CREATE INDEX IF NOT EXISTS idx_timed_run_summary_recent
        ON public.timed_run_summary (
            timer_id,
            latest_start_time DESC,
            latest_ctime DESC,
            run_id
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return "DROP TABLE IF EXISTS public.timed_run_summary;"
