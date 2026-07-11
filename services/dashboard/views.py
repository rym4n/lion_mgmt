# coding=utf-8

import asyncio
import time
from datetime import datetime

import pytz
from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import PlaybookVersion, ToolResources

dashboard_bp = Blueprint("dashboard", url_prefix="/dashboard")
_all__ = ["dashboard_bp"]

_DASHBOARD_MATERIALIZED_VIEWS = (
    "public.events_day",
    "public.events_status",
    "public.runlog_playbook",
    "public.runlog_action",
)
_DASHBOARD_REFRESH_CACHE_KEY = "dashboard:materialized-views:refresh"
_DASHBOARD_REFRESH_CACHE_TTL_SECONDS = 300
_DASHBOARD_REFRESH_ADVISORY_LOCK = "lion_mgmt.dashboard.materialized_views"
_DASHBOARD_REFRESH_TASK_NAME = "dashboard_materialized_view_refresh"
_DASHBOARD_REFRESH_TIMEOUT_SECONDS = 300


async def _refresh_dashboard_materialized_views(app) -> None:
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


def _schedule_dashboard_materialized_view_refresh(app) -> None:
    current_task = app.get_task(_DASHBOARD_REFRESH_TASK_NAME, raise_exception=False)
    if current_task is not None and not current_task.done():
        return
    app.add_task(
        _refresh_dashboard_materialized_views(app),
        name=_DASHBOARD_REFRESH_TASK_NAME,
    )


@dashboard_bp.post("/view")
@openapi.definition(
    summary="获取仪表盘数据",
    description="获取仪表盘数据，需要dashboard:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["static_type", "start_time", "end_time"],
                "properties": {
                    "static_type": {
                        "type": "string",
                        "description": "统计类型，可选值：events_day，events_status，runlog_playbook，runlog_action",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "统计的起始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "统计的结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "oneOf": [
                                    {"type": "integer"},
                                    {"type": "array"},
                                    {"type": "object"},
                                ]
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("dashboard:r")
async def view_dashboard(request):
    params = request.json
    tenant_id = request.ctx.session.get("tenant_id")

    start_time = params.get("start_time")
    end_time = params.get("end_time")
    static_type = params.get("static_type")

    if not start_time or not end_time or not static_type:
        raise CustomError(ErrorCode.PARAM_ERROR)

    result = None
    start_time = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    end_time = datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)

    async with in_transaction() as tconn:
        if static_type == "events_day":
            records = await tconn.execute_query_dict(
                "SELECT day, total FROM events_day WHERE day >= $1 AND day <= $2 AND tenant_id = $3",
                (start_time, end_time, tenant_id),
            )
            result = [{"day": row["day"], "counts": row["total"]} for row in records]
        elif static_type == "events_status":
            records = await tconn.execute_query_dict(
                "SELECT event_process_status, SUM(total) FROM events_status WHERE day >= $1 AND day <= $2 AND tenant_id = $3 GROUP BY event_process_status",
                (start_time, end_time, tenant_id),
            )
            result = [
                {
                    "event_process_status": row["event_process_status"],
                    "counts": int(row["sum"]),
                }
                for row in records
            ]
        elif static_type == "runlog_playbook":
            records = await tconn.execute_query_dict(
                "SELECT playbook_name, SUM(total) FROM runlog_playbook WHERE day >= $1 AND day <= $2 AND tenant_id = $3 GROUP BY playbook_name",
                (start_time, end_time, tenant_id),
            )
            result = [{"playbook_name": row["playbook_name"], "counts": int(row["sum"])} for row in records]
        elif static_type == "runlog_action":
            records = await tconn.execute_query_dict(
                "SELECT action_display_name, SUM(total) FROM runlog_action WHERE day >= $1 AND day <= $2 AND tenant_id = $3 GROUP BY action_display_name",
                (start_time, end_time, tenant_id),
            )
            result = [
                {
                    "action_display_name": row["action_display_name"],
                    "counts": int(row["sum"]),
                }
                for row in records
            ]

    _schedule_dashboard_materialized_view_refresh(request.app)
    return response_ok(result)


@dashboard_bp.post("/mttr")
@openapi.definition(
    summary="获取MTTR数据",
    description="获取MTTR数据，需要dashboard:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["start_time", "end_time"],
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "统计的起始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "统计的结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "day": {"type": "string"},
                                        "counts": {"type": "number"},
                                        "event_count": {"type": "integer"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("dashboard:r")
async def count_mttr(request):
    params = request.json
    start_time = params.get("start_time")
    end_time = params.get("end_time")

    # 判断时间参数是否合法的时间格式
    try:
        start_time = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        end_time = datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    except ValueError:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    async with in_transaction() as tconn:
        results = await tconn.execute_query_dict(
            "SELECT DATE(ctime) AS day, "
            "AVG(EXTRACT(EPOCH FROM (close_time - ctime))) AS avg_seconds, "
            "COUNT(id) AS event_count "
            "FROM events "
            "WHERE close_time IS NOT NULL "
            "AND ctime >= $1 AND ctime <= $2 "
            "AND tenant_id = $3 "
            "GROUP BY DATE(ctime) "
            "ORDER BY day",
            (start_time, end_time, tenant_id),
        )
        return response_ok(
            [
                {
                    "day": row["day"].strftime("%Y-%m-%d"),
                    "counts": round((float(row["avg_seconds"]) or 0) / 3600, 2),
                    "event_count": row["event_count"],
                }
                for row in results
            ]
        )


@dashboard_bp.post("/playbooks")
@openapi.definition(
    summary="获取已发布的剧本数量",
    description="获取已发布的剧本数量，需要dashboard:r权限",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "integer"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("dashboard:r")
async def count_playbooks(request):
    tenant_id = request.ctx.session.get("tenant_id")
    result = await PlaybookVersion.filter(status=1, tenant_id=tenant_id).annotate(counts=Count("playbook_id", distinct=True)).values("counts")
    return response_ok(result[0].get("counts"))


@dashboard_bp.post("/apps")
@openapi.definition(
    summary="获取已配置资源的应用数量",
    description="获取已配置资源的应用数量，需要dashboard:r权限",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "integer"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("dashboard:r")
async def count_apps(request):
    tenant_id = request.ctx.session.get("tenant_id")
    result = await ToolResources.filter(tenant_id=tenant_id).annotate(counts=Count("tool_name", distinct=True)).values("counts")
    return response_ok(result[0].get("counts"))


@dashboard_bp.post("/success-rate")
@openapi.definition(
    summary="获取剧本的成功率排行",
    description="获取剧本的成功率排行（修复成功率计算），需要dashboard:r权限",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "playbook_name": {"type": "string"},
                                        "success_rate": {"type": "number"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("dashboard:r")
async def playbooks_success_rate(request):
    tenant_id = request.ctx.session.get("tenant_id")
    sql = """
          SELECT playbook_name,
                 ROUND(SUM(CASE WHEN has_fail = 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 2) AS success_rate
          FROM (SELECT run_id,
                       playbook_name,
                       MAX(CASE WHEN status != 0 THEN 1 ELSE 0 END) AS has_fail
                FROM runlog
                WHERE playbook_name != '执行动作' AND tenant_id = $1
                GROUP BY run_id, playbook_name) AS deduplicated
          GROUP BY playbook_name
          ORDER BY success_rate;
          """
    async with in_transaction() as conn:
        rows = await conn.execute_query_dict(sql, (tenant_id,))
        for row in rows:
            row["success_rate"] = float(row["success_rate"])
        return response_ok(rows)
