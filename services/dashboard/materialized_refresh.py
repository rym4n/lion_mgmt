# coding=utf-8

import asyncio
import time

from sanic.log import logger
from tortoise.transactions import in_transaction


_DASHBOARD_MATERIALIZED_VIEWS = (
    "public.events_day",
    "public.events_status",
    "public.runlog_playbook",
    "public.runlog_action",
    "public.runlog_playbook_success_rate",
)
_DASHBOARD_REFRESH_CACHE_KEY = "dashboard:materialized-views:refresh"
_DASHBOARD_REFRESH_CACHE_TTL_SECONDS = 300
_DASHBOARD_REFRESH_ADVISORY_LOCK = "lion_mgmt.dashboard.materialized_views"
_DASHBOARD_REFRESH_TIMEOUT_SECONDS = 300


async def refresh_dashboard_materialized_views(app) -> None:
    """Refresh the dashboard snapshots while coordinating all workers."""
    cache = getattr(app.ctx, "cache_client", None)
    if cache is None:
        logger.warning("Dashboard refresh skipped because the cache client is unavailable")
        return

    try:
        await cache.add(
            _DASHBOARD_REFRESH_CACHE_KEY,
            "1",
            ttl=_DASHBOARD_REFRESH_CACHE_TTL_SECONDS,
        )
    except ValueError:
        return
    except Exception:
        logger.exception("Dashboard refresh lock could not be acquired")
        return

    started_at = time.monotonic()
    try:
        async with asyncio.timeout(_DASHBOARD_REFRESH_TIMEOUT_SECONDS):
            async with in_transaction() as tconn:
                await tconn.execute_query("SET LOCAL lock_timeout = '5s'")
                await tconn.execute_query("SET LOCAL statement_timeout = '120s'")
                lock_rows = await tconn.execute_query_dict(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0)) AS acquired",
                    (_DASHBOARD_REFRESH_ADVISORY_LOCK,),
                )
                if not lock_rows or not lock_rows[0]["acquired"]:
                    return

                for view_name in _DASHBOARD_MATERIALIZED_VIEWS:
                    await tconn.execute_query(
                        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}"
                    )
    except asyncio.CancelledError:
        logger.info("Dashboard materialized view refresh cancelled")
        raise
    except Exception:
        logger.exception("Dashboard materialized view refresh failed")
        return

    logger.info(
        "Dashboard materialized views refreshed in %.3fs",
        time.monotonic() - started_at,
    )


__all__ = ["refresh_dashboard_materialized_views"]
