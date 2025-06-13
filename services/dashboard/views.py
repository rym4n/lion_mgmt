# coding=utf-8
from datetime import datetime, timedelta
import pytz

from sanic import Blueprint
from tortoise.transactions import in_transaction
from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Events, Runlog, Room, Account, Role
from tortoise.functions import Count

dashboard_bp = Blueprint('dashboard', version=1, url_prefix='/dashboard')
_all__ = ["dashboard_bp"]

table_dict = {
    "events": Events,
    "runlog": Runlog
}

beijing = pytz.timezone("Asia/Shanghai")


def parse_conditions(conditions: dict) -> dict:
    parsed = {}
    for key, value in conditions.items():
        if value is None:
            continue
        if isinstance(value, str) and 'T' in value and ':' in value:
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = beijing.localize(dt)
                else:
                    dt = dt.astimezone(beijing)
                parsed[key] = dt
                continue
            except ValueError:
                pass
        parsed[key] = value
    return parsed


@dashboard_bp.post('/view')
@requires_permission('dashboard:r')
async def view_dashboard(request):
    """
    获取仪表盘数据

    请求参数：
        table_name: str, 表名，必填值：events, runlog
        conditions: dict, 查询条件，必填值：
        static_type: str, 统计类型，选填值（group_by为空时必填）：count(数量), value(全部字段的值)
        group_by: str, 分组字段，选填值（static_type为空时必填）：event_name, event_process_status, event_source, owner, room, updator, creator
    """
    conditions = parse_conditions(request.json.get("conditions"))
    print(conditions)
    if request.json.get("static_type") == "count":
        result = await table_dict.get(request.json.get("table_name")).filter(**conditions).count()
    elif request.json.get("static_type") == "value":
        result = await table_dict.get(request.json.get("table_name")).filter(**conditions).values()
    elif request.json.get("group_by"):
        group_by = request.json.get("group_by")
        result = await table_dict.get(request.json.get("table_name")).filter(**conditions).group_by(*group_by).annotate(count=Count("id")).values(*group_by, "count")
        await table_dict.get(request.json.get("table_name")).filter(**conditions).count()

    return response_ok(result)
