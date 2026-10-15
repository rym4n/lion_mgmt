from libs.runlog_pagination import (
    RUNLOG_BATCH_DEFAULT_SIZE,
    RUNLOG_BATCH_MAX_SIZE,
    parse_bounded_int,
    runlog_batch_summaries,
)


async def paginated_timed_runlog_summaries(
    model,
    condition,
    *,
    tenant_id: str,
    timer_id: str,
    page: int,
    size: int,
    start_time=None,
    end_time=None,
    status=None,
) -> tuple[int, list[dict]]:
    page = parse_bounded_int(page, default=1, maximum=1_000_000)
    size = parse_bounded_int(
        size, default=RUNLOG_BATCH_DEFAULT_SIZE, maximum=RUNLOG_BATCH_MAX_SIZE
    )
    params = [tenant_id, timer_id]
    filters = ['"tenant_id" = $1', '"timer_id" = $2', '"deleted" = FALSE']
    if start_time is not None:
        params.append(start_time)
        filters.append(f'"start_time" >= ${len(params)}')
    if end_time is not None:
        params.append(end_time)
        filters.append(f'"start_time" <= ${len(params)}')
    if status in (0, 1):
        params.append(int(status))
        filters.append(f'"status" = ${len(params)}')

    params.extend([size, (page - 1) * size])
    limit_param, offset_param = len(params) - 1, len(params)
    sql = f"""
        WITH grouped AS MATERIALIZED (
            SELECT "run_id", MAX("start_time") AS latest_start_time,
                   MAX("ctime") AS latest_ctime
            FROM "runlog"
            WHERE {' AND '.join(filters)}
            GROUP BY "run_id"
        )
        SELECT totals.total, selected.run_id
        FROM (SELECT COUNT(*) AS total FROM grouped) AS totals
        LEFT JOIN (
            SELECT run_id, latest_start_time, latest_ctime
            FROM grouped
            ORDER BY latest_start_time DESC, latest_ctime DESC, run_id DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
        ) AS selected ON TRUE
        ORDER BY selected.latest_start_time DESC,
                 selected.latest_ctime DESC, selected.run_id DESC
    """
    rows = await model._meta.db.execute_query_dict(sql, params)
    total = int(rows[0]["total"])
    run_ids = [row["run_id"] for row in rows if row["run_id"] is not None]
    summaries = await runlog_batch_summaries(model, condition, run_ids)
    return total, [summaries[run_id] for run_id in run_ids if run_id in summaries]
