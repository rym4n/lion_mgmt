# coding=utf-8
from datetime import datetime

import pytz
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q, RawSQL
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Events, Runlog, PlaybookVersion, ToolResources

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
                "required": ["table_name", "conditions", "start_time", "end_time"],
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "表名，可选值：events, runlog"
                    },
                    "conditions": {
                        "type": "object",
                        "description": "查询条件"
                    },
                    "static_type": {
                        "type": "string",
                        "description": "统计类型，可选值：count(数量), value(全部字段的值)"
                    },
                    "group_by": {
                        "type": "string",
                        "description": "分组字段，events表可选字段：event_name, event_process_status, event_source, owner, room, updator, creator；runlog表可选字段：playbook_name"
                    },
                    "count_key": {
                        "type": "string",
                        "description": "分组统计的时候按什么字段做统计"
                    },
                    "count_distinct": {
                        "type": "boolean",
                        "description": "是否统计数量的不同值，默认值：True"
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
    table_dict = {
        "events": Events,
        "runlog": Runlog
    }
    conditions = params.get("conditions")
    start_time = params.get("start_time")
    end_time = params.get("end_time")
    table_name = params.get("table_name")
    static_type = params.get("static_type")
    group_by = params.get("group_by")
    count_key = params.get("count_key")

    if not start_time or not end_time or table_name not in table_dict or (not static_type and not group_by) or (group_by and not count_key):
        raise CustomError(ErrorCode.ParameterError)

    start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
    start_time = pytz.timezone(os.environ.get('TZ', 'Asia/Shanghai')).localize(start_time)
    end_time = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
    end_time = pytz.timezone(os.environ.get('TZ', 'Asia/Shanghai')).localize(end_time)
    result = None
    q = Q(ctime__range=(start_time, end_time))

    if conditions and isinstance(conditions, dict):
        q &= Q(**conditions)

    if static_type == "count":
        result = await table_dict.get(table_name).filter(q).count()
    elif static_type == "value":
        result = await table_dict.get(table_name).filter(q).values()
    elif group_by:
        if group_by == 'ctime':
            result = await table_dict.get(table_name).filter(q).annotate(
                day=RawSQL("TO_CHAR(DATE_TRUNC('day', ctime), 'YYYY-MM-DD')")
            ).group_by('day').annotate(
                counts=Count(count_key, distinct=params.get('count_distinct', True))
            ).order_by("-counts").values("day", "counts")
        else:
            result = await table_dict.get(table_name).filter(q).annotate(
                counts=Count(count_key, distinct=params.get('count_distinct', True))
            ).group_by(group_by).order_by("-counts").values(group_by, "counts")

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
    sql = """
          SELECT playbook_name,
                 ROUND(SUM(CASE WHEN has_fail = 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 2) AS success_rate
          FROM (SELECT run_id,
                       playbook_name,
                       MAX(CASE WHEN status != 0 THEN 1 ELSE 0 END) AS has_fail
                FROM runlog
                WHERE playbook_name != '执行动作'
                GROUP BY run_id, playbook_name) AS deduplicated
          GROUP BY playbook_name
          ORDER BY success_rate; \
          """
    async with in_transaction() as conn:
        rows = await conn.execute_query_dict(sql)
        for row in rows:
            row["success_rate"] = float(row["success_rate"])
        return response_ok(rows)
