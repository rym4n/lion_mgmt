# coding=utf-8
from datetime import datetime
from datetime import timedelta

import pytz
from sanic import Blueprint
from tortoise.expressions import F
from tortoise.expressions import Q, RawSQL
from tortoise.functions import Avg
from tortoise.functions import Count

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Events, Runlog, PlaybookVersion, ToolResources

dashboard_bp = Blueprint('dashboard', url_prefix='/dashboard')
_all__ = ["dashboard_bp"]


@dashboard_bp.post('/view')
@requires_permission('dashboard:r')
async def view_dashboard(request):
    """
    获取仪表盘数据

    请求参数：
        table_name: str, 表名，必填值：events, runlog
        conditions: dict, 查询条件，必填值：
        static_type: str, 统计类型，选填值（group_by为空时必填）：count(数量), value(全部字段的值)
        group_by: str, 分组字段，选填值（static_type为空时必填）, events表可选字段：event_name, event_process_status, event_source, owner, room, updator, creator；runlog表可选字段：playbook_name
        count_key: str, 分组统计的时候按什么字段做统计，group_by 不为空的时候必填
        start_time: str, 统计的起始时间
        end_time: str, 统计的结束时间
        time_calc: str, 时间差计算专用字段
    """
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
    time_calc = params.get("time_calc")
    if not start_time or not end_time or table_name not in table_dict or (not static_type and not group_by) or (group_by and not count_key):
        raise CustomError(ErrorCode.ParameterError)

    start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
    start_time = pytz.timezone("Asia/Shanghai").localize(start_time)
    end_time = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
    end_time = pytz.timezone("Asia/Shanghai").localize(end_time)
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
            if time_calc == 'mttr':
                result = await table_dict.get(table_name).filter(q).annotate(
                    day=RawSQL("TO_CHAR(DATE_TRUNC('day', ctime), 'YYYY-MM-DD')"),
                    duration_seconds=(F("close_time") - F("ctime")) / timedelta(seconds=1)
                ).group_by("day").annotate(
                    avg_duration=Avg("duration_seconds")
                ).values("day", "avg_time")
            else:
                result = await table_dict.get(table_name).filter(q).annotate(
                    day=RawSQL("TO_CHAR(DATE_TRUNC('day', ctime), 'YYYY-MM-DD')")
                ).group_by('day').annotate(
                    counts=Count(count_key, distinct=True)
                ).values("day", "counts")
        else:
            result = await table_dict.get(table_name).filter(q).annotate(
                counts=Count(count_key, distinct=True)
            ).group_by(group_by).values(group_by, "counts")

    return response_ok(result)


@dashboard_bp.post('/playbooks')
@requires_permission('dashboard:r')
async def count_playbooks(request):
    """
    获取已发布的剧本数量
    """
    result = await PlaybookVersion.filter(status=1).annotate(
        counts=Count('playbook_id', distinct=True)
    ).values("counts")
    return response_ok(result)


@dashboard_bp.post('/apps')
@requires_permission('dashboard:r')
async def count_playbooks(request):
    """
    获取已配置资源的应用数量
    """
    result = await ToolResources.annotate(
        counts=Count('tool_name', distinct=True)
    ).values("counts")
    return response_ok(result)
