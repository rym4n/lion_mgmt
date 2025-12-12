# coding=utf-8
from collections import defaultdict
from datetime import datetime

from sanic import Blueprint
from sanic_ext import openapi
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import query_vlog
from models import PlaybookVersion, ToolResources

dashboard_bp = Blueprint('dashboard', url_prefix='/dashboard')
_all__ = ["dashboard_bp"]


@dashboard_bp.post('/view')
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
                        "description": "统计类型，可选值：events_day，events_status，runlog_playbook，runlog_action"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "统计的起始时间，格式：YYYY-MM-DD HH:MM:SS"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "统计的结束时间，格式：YYYY-MM-DD HH:MM:SS"
                    }
                }
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
                                    {"type": "object"}
                                ]
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('dashboard:r')
async def view_dashboard(request):
    params = request.json

    start_time = params.get("start_time")
    end_time = params.get("end_time")
    static_type = params.get("static_type")

    if not start_time or not end_time or not static_type:
        raise CustomError(ErrorCode.ParameterError)

    result = None
    start_time = datetime.strptime(start_time, '%Y-%m-%d')
    end_time = datetime.strptime(end_time, '%Y-%m-%d')

    async with in_transaction() as tconn:
        if static_type == "events_day":
            records = await tconn.execute_query_dict("SELECT day, total FROM events_day WHERE day >= $1 AND day <= $2", (start_time, end_time))
            result = [{"day": row["day"], "counts": row["total"]} for row in records]
            await tconn.execute_query("REFRESH MATERIALIZED VIEW events_day")
        elif static_type == "events_status":
            records = await tconn.execute_query_dict("SELECT event_process_status, SUM(total) FROM events_status WHERE day >= $1 AND day <= $2 GROUP BY event_process_status", (start_time, end_time))
            result = [{"event_process_status": row["event_process_status"], "counts": int(row["sum"])} for row in records]
            await tconn.execute_query("REFRESH MATERIALIZED VIEW events_status")
        elif static_type == "runlog_playbook":
            records = await tconn.execute_query_dict("SELECT playbook_name, SUM(total) FROM runlog_playbook WHERE day >= $1 AND day <= $2 GROUP BY playbook_name", (start_time, end_time))
            result = [{"playbook_name": row["playbook_name"], "counts": int(row["sum"])} for row in records]
            await tconn.execute_query("REFRESH MATERIALIZED VIEW runlog_playbook")
        elif static_type == "runlog_action":
            records = await tconn.execute_query_dict("SELECT action_display_name, SUM(total) FROM runlog_action WHERE day >= $1 AND day <= $2 GROUP BY action_display_name", (start_time, end_time))
            result = [{"action_display_name": row["action_display_name"], "counts": int(row["sum"])} for row in records]
            await tconn.execute_query("REFRESH MATERIALIZED VIEW runlog_action")

    return response_ok(result)


@dashboard_bp.post('/mttr')
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
                        "description": "统计的起始时间，格式：YYYY-MM-DD HH:MM:SS"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "统计的结束时间，格式：YYYY-MM-DD HH:MM:SS"
                    }
                }
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
                                        "event_count": {"type": "integer"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('dashboard:r')
async def count_mttr(request):
    params = request.json
    start_time = params.get("start_time")
    end_time = params.get("end_time")

    # 判断时间参数是否合法的时间格式
    try:
        start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        end_time = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        raise CustomError(ErrorCode.ParameterError)

    async with in_transaction() as tconn:
        results = await tconn.execute_query_dict(
            "SELECT DATE(ctime) AS day, "
            "AVG(EXTRACT(EPOCH FROM (close_time - ctime))) AS avg_seconds, "
            "COUNT(id) AS event_count "
            "FROM events "
            "WHERE close_time IS NOT NULL "
            "AND ctime >= $1 AND ctime <= $2 "
            "GROUP BY DATE(ctime) "
            "ORDER BY day",
            (start_time, end_time)
        )
        return response_ok([
            {
                "day": row["day"].strftime("%Y-%m-%d"),
                "counts": round((float(row["avg_seconds"]) or 0) / 3600, 2),
                "event_count": row["event_count"]
            }
            for row in results
        ])


@dashboard_bp.post('/playbooks')
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
                            "data": {"type": "integer"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('dashboard:r')
async def count_playbooks(request):
    result = await PlaybookVersion.filter(status=1).annotate(
        counts=Count('playbook_id', distinct=True)
    ).values("counts")
    return response_ok(result[0].get("counts"))


@dashboard_bp.post('/apps')
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
                            "data": {"type": "integer"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('dashboard:r')
async def count_apps(request):
    result = await ToolResources.annotate(
        counts=Count('tool_name', distinct=True)
    ).values("counts")
    return response_ok(result[0].get("counts"))


@dashboard_bp.post('/success-rate')
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
                                        "success_rate": {"type": "number"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('dashboard:r')
async def playbooks_success_rate(request):
    query = 'playbook_name: !"执行动作"'
    logs = await query_vlog(query, project_id=1, account_id=0)

    grouped = {}
    for log in logs:
        playbook_name = log.get("playbook_name")
        msg_id = log.get("_msg")

        if not playbook_name or not msg_id:
            continue

        if log.get("result") in ["执行中", "等待审核"]:
            continue

        status = log.get("status")
        grouped.setdefault(playbook_name, {}).setdefault(msg_id, []).append(status)

    results = []
    for playbook_name, executions in grouped.items():
        total = len(executions)
        success = 0

        for msg_id, statuses in executions.items():
            if any(s != "0" for s in statuses):
                continue
            success += 1

        success_rate = round(success / total, 2) if total > 0 else 0

        results.append({
            "playbook_name": playbook_name,
            "success_rate": success_rate
        })
    results = sorted(results, key=lambda x: x["success_rate"])
    return response_ok(results)
