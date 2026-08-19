RUNNING_RUNLOG_STATUSES = (1, 2)
SUCCESS_RUNLOG_STATUSES = (0,)


async def refresh_timed_run_summaries(connection, runlogs) -> None:
    run_ids = sorted(
        {str(item.run_id) for item in runlogs if getattr(item, "run_id", None)}
    )
    if not run_ids:
        return

    affected_rows = await connection.execute_query_dict(
        """
        SELECT DISTINCT timer_id, run_id
        FROM runlog
        WHERE run_id = ANY($1::varchar[])
          AND timer_id IS NOT NULL
        """,
        [run_ids],
    )
    pairs = sorted((row["timer_id"], row["run_id"]) for row in affected_rows)
    if not pairs:
        return
    timer_ids = sorted({timer_id for timer_id, _ in pairs})
    affected_timer_ids = [timer_id for timer_id, _ in pairs]
    affected_run_ids = [run_id for _, run_id in pairs]

    # Kafka partitions or service replicas may update the same timer concurrently.
    # Lock real timer rows in a stable order so pruning cannot retain >10 rows.
    await connection.execute_query(
        """
        SELECT id
        FROM timed
        WHERE id = ANY($1::varchar[])
        ORDER BY id
        FOR NO KEY UPDATE
        """,
        [timer_ids],
    )

    await connection.execute_query(
        """
        DELETE FROM timed_run_summary AS summary
        USING unnest($1::varchar[], $2::varchar[]) AS affected(timer_id, run_id)
        WHERE summary.timer_id = affected.timer_id
          AND summary.run_id = affected.run_id
        """,
        [affected_timer_ids, affected_run_ids],
    )
    await connection.execute_query(
        """
        INSERT INTO timed_run_summary (
            timer_id,
            run_id,
            latest_start_time,
            latest_ctime,
            is_running,
            is_success
        )
        SELECT
            logs.timer_id,
            logs.run_id,
            MAX(logs.start_time),
            MAX(logs.ctime),
            BOOL_OR(logs.status = ANY($3::integer[])),
            BOOL_AND(logs.status = ANY($4::integer[]))
        FROM runlog AS logs
        JOIN unnest($1::varchar[], $2::varchar[]) AS affected(timer_id, run_id)
          ON logs.timer_id = affected.timer_id
         AND logs.run_id = affected.run_id
        WHERE logs.timer_id IS NOT NULL
          AND logs.deleted = FALSE
        GROUP BY logs.timer_id, logs.run_id
        """,
        [
            affected_timer_ids,
            affected_run_ids,
            list(RUNNING_RUNLOG_STATUSES),
            list(SUCCESS_RUNLOG_STATUSES),
        ],
    )
    await connection.execute_query(
        """
        DELETE FROM timed_run_summary AS summary
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
                FROM timed_run_summary
                WHERE timer_id = ANY($1::varchar[])
            ) AS ranked
            WHERE run_rank > 10
        ) AS expired
        WHERE summary.timer_id = expired.timer_id
          AND summary.run_id = expired.run_id
        """,
        [timer_ids],
    )
