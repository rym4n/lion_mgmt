# coding=utf-8

import datetime

import pytz
from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Max
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.runlog_pagination import paginated_runlog_summaries, runlog_node_page
from libs.security.authorization import (
    account_has_permission,
    check_action_execute_permission,
    check_playbook_permission,
    requires_permission,
)
from libs.utils import normalize_required_text, paginated_runlog_groups, strip_timezone_to_seconds
from models import (
    Playbook,
    PlaybookVersion,
    Runlog,
    Timed,
    Tool,
    ToolAction,
)
from services.workflow.views import get_playbook_input
from services.timed.cron_expression import is_valid_cron_expression
from settings import TASK_TOPIC

timed_bp = Blueprint("timed", url_prefix="/timed")
_all__ = ["timed_bp"]

# 枚举类型
RUN_TYPE_FLOW = 0
RUN_TYPE_ACTION = 1
EXEC_TYPE_ONCE = 0
EXEC_TYPE_CRON = 1
DISABLED = 0
ENABLED = 1
# 列表按 timed 垂直权限映射前端可操作项，替代原先基于资源执行权限的行级过滤
_TIMED_PERMISSION_MAP = (("r", "timed:r"), ("u", "timed:u"), ("d", "timed:d"))
# Only terminal success counts; running/waiting logs must not inflate the rate.
SUCCESS_RUNLOG_STATUSES = {0}
RUNNING_RUNLOG_STATUSES = {1, 2}


def _current_timed_internal_scope(maintenance_mode):
    if isinstance(maintenance_mode, str):
        return maintenance_mode.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(maintenance_mode)


def _timed_internal_scope_from_request(_scope, maintenance_mode):
    """Keep the legacy scope parameter inert; the server session owns the mode."""
    return _current_timed_internal_scope(maintenance_mode)


def _timed_name_filter_kwargs(name, tenant_id, internal):
    return {"name": name, "deleted": False, "tenant_id": tenant_id, "internal": internal}


async def get_current_timed_internal_scope(request):
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    return _current_timed_internal_scope(maintenance_mode)


async def get_requested_timed_internal_scope(request):
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    return _timed_internal_scope_from_request((request.json or {}).get("scope"), maintenance_mode)


async def _get_action_tool_in_timed_scope(action, tenant_id, internal):
    if not action:
        return None
    return await Tool.get_or_none(
        name=action.tool_name,
        version=action.version_number,
        deleted=False,
        maintain=bool(internal),
        tenant_id=tenant_id,
    )


def _canonicalize_timed_action_input(action, tool, action_input, executor_id):
    if not isinstance(action_input, dict) or not isinstance(action.action, dict):
        raise CustomError(ErrorCode.ACTION_INPUT_ERROR)
    action_func = action.action.get("func")
    action_display_name = action.action.get("name")
    if not action_func or not action_display_name:
        raise CustomError(ErrorCode.ACTION_INPUT_ERROR)

    canonical = dict(action_input)
    canonical.update(
        {
            "app_name": tool.name,
            "app_ver": tool.version,
            "main": tool.main or "",
            "name": tool.description or tool.name or "",
            "node_name": action_display_name,
            "action_name": action_display_name,
            "action": action_func,
            "executor": executor_id,
        }
    )
    return canonical


def _parse_dashboard_datetime_range(start, end):
    start_dt = datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    end_dt = datetime.datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    if (
        end_dt.time() == datetime.time.min
        and start_dt.date() < end_dt.date()
        and end_dt.microsecond == 0
    ):
        end_dt = end_dt + datetime.timedelta(days=1) - datetime.timedelta(microseconds=1)
    return start_dt, end_dt


def build_timer_rule(status, tactics, end_time):
    if status == EXEC_TYPE_ONCE:
        return {"timer": "once", "timer_rule": {"run_date": tactics}}
    else:
        return {
            "timer": "cron",
            "timer_rule": {"end_date": end_time, "crontab": tactics},
        }


@timed_bp.post("/add")
@openapi.definition(
    summary="添加定时任务",
    description="添加一个新的定时任务，需要定时任务创建权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "type", "status", "tactics"],
                "properties": {
                    "name": {"type": "string", "description": "定时任务名称"},
                    "type": {
                        "type": "integer",
                        "description": "执行操作类型 0为执行剧本，1为执行动作",
                    },
                    "playbook_id": {
                        "type": "string",
                        "description": "剧本id 当type为0时必填",
                    },
                    "playbook_input": {
                        "type": "object",
                        "description": "剧本输入参数 当type为0时必填",
                    },
                    "action_id": {
                        "type": "string",
                        "description": "动作id 当type为1时必填",
                    },
                    "action_input": {
                        "type": "object",
                        "description": "动作输入参数 当type为1时必填",
                    },
                    "status": {
                        "type": "integer",
                        "description": "执行类型 0为单次执行，1为周期执行",
                    },
                    "tactics": {
                        "type": "string",
                        "description": "执行策略 如果为单次执行，则传2025-12-12 00:00:00；如果为周期执行，则传0 */5 * * * *（秒 分 时 日 月 周）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间 为周期执行时填写，格式为2025-12-12 00:00:00",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "添加成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "type": {"type": "integer"},
                                    "playbook_id": {"type": "string"},
                                    "playbook_input": {"type": "object"},
                                    "action_id": {"type": "string"},
                                    "action_input": {"type": "object"},
                                    "status": {"type": "integer"},
                                    "tactics": {"type": "string"},
                                    "end_time": {"type": "string"},
                                    "enable": {"type": "integer"},
                                    "result": {"type": "string"},
                                    "success_rate": {"type": "string"},
                                    "internal": {"type": "boolean"},
                                    "creator": {
                                        "type": "object",
                                        "properties": {
                                            "username": {"type": "string"},
                                            "display_name": {"type": "string"},
                                        },
                                    },
                                    "updator": {
                                        "type": "object",
                                        "properties": {
                                            "username": {"type": "string"},
                                            "display_name": {"type": "string"},
                                        },
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
@requires_permission("timed:c")
async def add_timed(request):
    data = request.json or {}
    name = normalize_required_text(data.get("name"))
    operate_type = int(data.get("type"))
    playbook_id = data.get("playbook_id") or ""
    playbook_input = data.get("playbook_input") or {}
    action_id = data.get("action_id") or ""
    action_input = data.get("action_input") or {}
    status = int(data.get("status"))
    tactics = data.get("tactics")
    end_time = data.get("end_time") or ""
    if any(x is None for x in [name, operate_type, status, tactics]):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if status not in [0, 1] or operate_type not in [0, 1]:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if operate_type == 0 and not playbook_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if operate_type == 1 and not action_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_requested_timed_internal_scope(request)
    if internal is None:
        internal = await get_current_timed_internal_scope(request)

    if await Timed.filter(**_timed_name_filter_kwargs(name, tenant_id, internal)).exists():
        raise CustomError(ErrorCode.TIMED_NAME_EXISTS)

    msg = {}
    playbook = None
    action = None
    playbook_version = None
    timer_rule = {}
    if operate_type == 0:
        playbook = await Playbook.get_or_none(
            id=playbook_id,
            deleted=False,
            internal=bool(internal),
            tenant_id=tenant_id,
        )
        if not playbook:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
        playbook_version = await PlaybookVersion.get_or_none(
            id=playbook_input.get("flow_id"),
            playbook_id=playbook.id,
            deleted=False,
            tenant_id=tenant_id,
        )
        if not playbook_version:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
        # 前端下拉已按执行权(e)过滤，API 层做等价校验，避免直调 API 绕过前端约束
        creator_account = request.ctx.session["account_info"]
        if not await check_playbook_permission(request, creator_account, playbook_id, ["e"]):
            raise CustomError(ErrorCode.PLAYBOOK_UNAUTHORIZED)
        msg["run_type"] = "flow"
        playbook_input["executor"] = request.ctx.session.get("account_info").id
        if any(key not in playbook_input.keys() for key in ["flow_id", "flow_input", "executor"]):
            raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)
        msg["run_args"] = playbook_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            if not is_valid_cron_expression(tactics):
                logger.info(f"定时任务{name}的执行策略{tactics}不符合crontab格式，创建失败")
                raise CustomError(ErrorCode.CRON_EXPRESSION_ERROR)
            timer_rule["crontab"] = tactics
    elif operate_type == 1:
        action = await ToolAction.get_or_none(
            id=action_id, deleted=False, tenant_id=tenant_id
        )
        tool = await _get_action_tool_in_timed_scope(action, tenant_id, internal)
        if not tool:
            raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)
        action_input = _canonicalize_timed_action_input(
            action,
            tool,
            action_input,
            request.ctx.session.get("account_info").id,
        )
        if any(
            key not in action_input
            for key in [
                "app_name",
                "name",
                "app_ver",
                "main",
                "action_input",
                "resource",
                "node_name",
                "action_name",
                "action",
                "executor",
                "retry_times",
                "timeout",
            ]
        ):
            raise CustomError(ErrorCode.ACTION_INPUT_ERROR)
        # 前端下拉已按 (tool, action, resource) → 用户/角色 过滤，API 层同步校验；超管豁免
        creator_account = request.ctx.session["account_info"]
        if not await check_action_execute_permission(
            creator_account,
            action_input.get("app_name"),
            action_input.get("action"),
            action_input.get("resource") or "",
            tenant_id,
            bool(internal),
            action_input.get("app_ver"),
        ):
            raise CustomError(ErrorCode.UNAUTH)
        msg["run_type"] = "action"
        msg["run_args"] = action_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            if not is_valid_cron_expression(tactics):
                logger.info(f"定时任务{name}的执行策略{tactics}不符合crontab格式，创建失败")
                raise CustomError(ErrorCode.CRON_EXPRESSION_ERROR)
            timer_rule["crontab"] = tactics
    timed = await Timed.create(
        name=name,
        type=operate_type,
        playbook=playbook,
        playbook_input=playbook_input,
        action=action,
        playbook_version=playbook_version.version if playbook_version else None,
        action_input=action_input,
        status=status,
        tactics=tactics,
        end_time=end_time,
        enable=1,
        internal=internal,
        creator=request.ctx.session["account_info"],
        updator=request.ctx.session["account_info"],
        tenant_id=tenant_id,
    )
    msg["timer_id"] = timed.id
    msg["timer_rule"] = timer_rule
    msg["tenant_id"] = tenant_id
    await aproduce(TASK_TOPIC, msg)
    return response_ok(timed.json)


def _timed_action_permissions(account, tenant_id: str | None = None):
    """按当前账户对 timed 模块的垂直权限，推导前端可操作项(r/u/d)。

    定时任务的可见性与全平台其它模块一致，只由租户隔离 + 垂直权限 timed:r 决定
    （由 @requires_permission('timed:r') 装饰器守门），不再基于底层剧本/动作的
    资源级执行权限做行级读过滤——那会导致「有权创建、创建成功却看不到」的错位。
    """
    return [flag for flag, perm in _TIMED_PERMISSION_MAP if account_has_permission(account, perm, tenant_id=tenant_id)]


@timed_bp.post("/all")
@openapi.definition(
    summary="获取所有定时任务列表",
    description="获取所有定时任务列表，需要定时任务查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码，默认1"},
                    "size": {"type": "integer", "description": "页大小，默认10"},
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键字，目前支持任务名称，动作名称，剧本名称搜索",
                    },
                    "playbook_name": {"type": "string", "description": "剧本名称（模糊匹配）"},
                    "enable": {"type": "integer", "description": "是否启用：0禁用，1启用"},
                    "updator": {"type": "string", "description": "更新人（用户名/显示名 模糊匹配）"},
                    "utime": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "更新时间范围（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "scope": {
                        "type": "integer",
                        "description": "兼容旧客户端的保留字段；服务端始终按当前维护模式隔离",
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
                                "type": "object",
                                "properties": {
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total": {"type": "integer"},
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "name": {"type": "string"},
                                                "type": {"type": "integer"},
                                                "status": {"type": "integer"},
                                                "tactics": {"type": "string"},
                                                "result": {"type": "string"},
                                                "playbook_remark": {"type": "string"},
                                                "action": {"type": "string"},
                                                "enable": {"type": "integer"},
                                                "end_time": {"type": "string"},
                                                "creator": {
                                                    "type": "object",
                                                    "properties": {
                                                        "username": {"type": "string"},
                                                        "display_name": {"type": "string"},
                                                    },
                                                },
                                                "updator": {
                                                    "type": "object",
                                                    "properties": {
                                                        "username": {"type": "string"},
                                                        "display_name": {"type": "string"},
                                                    },
                                                },
                                                "permissions": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
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
@requires_permission("timed:r")
async def get_all(request):
    req_json = request.json or {}
    page = max(1, int(req_json.get("page") or 1))
    size = max(1, min(int(req_json.get("size") or 15), 100))
    keyword = (req_json.get("keyword") or "").strip()
    playbook_name = (req_json.get("playbook_name") or "").strip()
    enable = req_json.get("enable")
    updator = (req_json.get("updator") or "").strip()
    utime = req_json.get("utime")
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])

    tenant_id = request.ctx.session.get("tenant_id")

    condition = Q(deleted=False, tenant_id=tenant_id)

    internal = _timed_internal_scope_from_request(req_json.get("scope"), maintenance_mode)
    condition &= Q(internal=internal)

    if enable not in [None, ""]:
        try:
            enable_int = int(enable)
        except (TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_ERROR)
        if enable_int in [DISABLED, ENABLED]:
            condition &= Q(enable=enable_int)

    if playbook_name:
        condition &= Q(playbook__name=playbook_name)

    if updator:
        condition &= Q(updator__username=updator) | Q(updator__display_name=updator)

    if utime:
        if not isinstance(utime, (list, tuple)) or len(utime) != 2:
            raise CustomError(ErrorCode.PARAM_ERROR)
        start_str, end_str = utime
        if start_str:
            start_time = datetime.datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
            condition &= Q(utime__gte=start_time)
        if end_str:
            end_time = datetime.datetime.strptime(end_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
            condition &= Q(utime__lte=end_time)

    all_items = await Timed.filter(condition).prefetch_related("action", "playbook", "creator", "updator").order_by("-ctime")

    if keyword:
        keyword = keyword.lower()

        def match(item):
            name_match = keyword in (item.name or "").lower()

            action_desc = ""
            if item.action and isinstance(item.action.action, dict):
                action_desc = item.action.action.get("name", "")
            action_match = keyword in action_desc.lower()

            pb_name = item.playbook.name if item.playbook else ""
            playbook_match = keyword in pb_name.lower()

            return name_match or action_match or playbook_match

        filtered_items = list(filter(match, all_items))
    else:
        filtered_items = all_items

    # 可见性 = 租户隔离 + 垂直权限 timed:r（装饰器已守门），不再按资源级执行权限行级过滤。
    action_permissions = _timed_action_permissions(request.ctx.session["account_info"], tenant_id)

    total = len(filtered_items)
    start = (page - 1) * size
    end = start + size
    paged_items = filtered_items[start:end]

    items = [
        {
            "id": item.id,
            "ctime": strip_timezone_to_seconds(item.ctime),
            "utime": strip_timezone_to_seconds(item.utime),
            "name": item.name,
            "type": item.type,
            "status": item.status,
            "tactics": item.tactics,
            "result": item.result,
            "playbook_remark": item.playbook.name if item.playbook else "",
            "action": item.action.action["name"] if item.action else "",
            "enable": item.enable,
            "end_time": item.end_time,
            "creator": {
                "username": item.creator.username,
                "display_name": item.creator.display_name,
            },
            "updator": {
                "username": item.updator.username,
                "display_name": item.updator.display_name,
            },
            "permissions": action_permissions,
        }
        for item in paged_items
    ]

    return response_ok({"page": page, "size": size, "total": total, "items": items})


@timed_bp.post("/success")
@openapi.definition(
    summary="获取定时任务成功率",
    description="获取定时任务成功率，需要定时任务查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "定时任务的id列表",
                    }
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
                                        "id": {"type": "string"},
                                        "result": {"type": "string"},
                                        "success_rate": {"type": "string"},
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
@requires_permission("timed:r")
async def get_success_rate(request):
    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)
    items = await Timed.filter(
        id__in=request.json.get("ids"),
        deleted=False,
        internal=internal,
        tenant_id=tenant_id,
    ).all()
    return response_ok([await timer_success_rate(item) for item in items])


@timed_bp.post("/dashboard/statistics")
@openapi.definition(
    summary="获取定时任务仪表盘统计数据",
    description="获取指定定时任务在指定时间范围内的执行统计数据",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "start", "end"],
                "properties": {
                    "id": {"type": "string", "description": "定时任务ID"},
                    "start": {
                        "type": "string",
                        "description": "查询开始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end": {
                        "type": "string",
                        "description": "查询结束时间（UTC格式：2025-01-01T00:00:00Z）",
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
                                        "date": {"type": "string"},
                                        "total_runs": {"type": "integer"},
                                        "success_count": {"type": "integer"},
                                        "fail_count": {"type": "integer"},
                                        "success_rate": {"type": "string"},
                                    },
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("timed:r")
async def dashboard_statistics(request):
    data = request.json or {}
    timed_id = data.get("id") or data.get("timed_id")
    start = data.get("start")
    end = data.get("end")

    if not timed_id or not start or not end:
        raise CustomError(ErrorCode.PARAM_ERROR)

    try:
        start_dt, end_dt = _parse_dashboard_datetime_range(start, end)
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)
    timed = await Timed.get_or_none(
        id=timed_id,
        deleted=False,
        internal=internal,
        tenant_id=tenant_id,
    )
    if not timed:
        raise CustomError(ErrorCode.TIMED_NOT_FOUND)

    sql = """
        SELECT
            TO_CHAR(t.run_end, 'YYYY-MM-DD') as date,
            COUNT(t.run_id) as total_runs,
            SUM(CASE WHEN t.is_fail = 0 THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN t.is_fail = 1 THEN 1 ELSE 0 END) as fail_count
        FROM (
            SELECT
                run_id,
                MAX(end_time) as run_end,
                MAX(CASE WHEN status NOT IN (0) THEN 1 ELSE 0 END) as is_fail
            FROM runlog
            WHERE timer_id = $1
              AND start_time BETWEEN $2 AND $3
              AND tenant_id = $4
              AND deleted = FALSE
            GROUP BY run_id
        ) as t
        WHERE t.run_end IS NOT NULL
        GROUP BY date
        ORDER BY date ASC;
    """

    async with in_transaction() as conn:
        results = await conn.execute_query_dict(sql, [timed_id, start_dt, end_dt, tenant_id])

    final_result = []
    for row in results:
        total = row["total_runs"]
        success_count = int(row["success_count"])
        fail = int(row["fail_count"])
        day = row["date"]

        rate_val = round(success_count / total, 2) if total else 0
        success_rate = str(rate_val * 100) + "%"

        final_result.append(
            {
                "date": day,
                "total_runs": total,
                "success_count": success_count,
                "fail_count": fail,
                "success_rate": success_rate,
            }
        )

    return response_ok(final_result)


@timed_bp.post("/detail")
@openapi.definition(
    summary="获取单个定时任务详情",
    description="获取单个定时任务详情",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "定时任务id"}},
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
                                "type": "object",
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "name": {"type": "string"},
                                                "type": {"type": "integer"},
                                                "playbook_id": {"type": "string"},
                                                "playbook_input": {"type": "object"},
                                                "action_id": {"type": "string"},
                                                "action_input": {"type": "object"},
                                                "status": {"type": "integer"},
                                                "tactics": {"type": "string"},
                                                "end_time": {"type": "string"},
                                                "enable": {"type": "integer"},
                                                "result": {"type": "string"},
                                                "success_rate": {"type": "string"},
                                                "internal": {"type": "boolean"},
                                                "creator": {
                                                    "type": "object",
                                                    "properties": {
                                                        "username": {"type": "string"},
                                                        "display_name": {"type": "string"},
                                                    },
                                                },
                                                "updator": {
                                                    "type": "object",
                                                    "properties": {
                                                        "username": {"type": "string"},
                                                        "display_name": {"type": "string"},
                                                    },
                                                },
                                                "input_args": {
                                                    "type": "array",
                                                    "items": {"type": "object"},
                                                },
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("timed:r")
async def get_timed_detail(request):
    timed_id = request.json.get("id")
    if not timed_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)

    timed = await Timed.get_or_none(
        id=timed_id,
        deleted=False,
        internal=internal,
        tenant_id=tenant_id,
    ).prefetch_related("action", "playbook", "creator", "updator")

    if not timed:
        raise CustomError(ErrorCode.PARAM_ERROR)

    data = timed.json
    if timed.playbook_input:
        input_args = await get_playbook_input(timed.playbook_input["flow_id"], tenant_id=tenant_id)
        data["input_args"] = input_args
    if timed.action_input:
        actions = await ToolAction.filter(
            tool_name=timed.action_input.get("app_name"),
            deleted=False,
            version_number=timed.action_input.get("app_ver"),
            tenant_id=tenant_id,
        ).all()
        for action in actions:
            if action.action["func"] == timed.action_input["action"]:
                input_args = []
                args = action.action["input"]
                for arg in args:
                    input_args.append({arg["key"]: arg})
                data["input_args"] = input_args

    return response_ok({"items": [data]})


@timed_bp.post("/update")
@openapi.definition(
    summary="更新定时任务",
    description="更新定时任务，需要定时任务更新权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "定时任务id"},
                    "type": {
                        "type": "integer",
                        "description": "执行操作类型 0为执行剧本，1为执行动作",
                    },
                    "playbook_id": {
                        "type": "string",
                        "description": "剧本id 当type为0时必填",
                    },
                    "playbook_input": {
                        "type": "object",
                        "description": "剧本输入参数 当type为0时必填",
                    },
                    "action_id": {
                        "type": "string",
                        "description": "动作id 当type为1时必填",
                    },
                    "action_input": {
                        "type": "object",
                        "description": "动作输入参数 当type为1时必填",
                    },
                    "status": {
                        "type": "integer",
                        "description": "执行类型 0为单次执行，1为周期执行",
                    },
                    "tactics": {
                        "type": "string",
                        "description": "执行策略 如果为单次执行，则传2025-12-12 00:00:00；如果为周期执行，则传0 */5 * * * *（秒 分 时 日 月 周）",
                    },
                    "enable": {
                        "type": "integer",
                        "description": "是否启用 0为禁用，1为启用",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间 为周期执行时填写，格式为2025-12-12 00:00:00",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "type": {"type": "integer"},
                                    "playbook_id": {"type": "string"},
                                    "playbook_input": {"type": "object"},
                                    "action_id": {"type": "string"},
                                    "action_input": {"type": "object"},
                                    "status": {"type": "integer"},
                                    "tactics": {"type": "string"},
                                    "end_time": {"type": "string"},
                                    "enable": {"type": "integer"},
                                    "result": {"type": "string"},
                                    "success_rate": {"type": "string"},
                                    "internal": {"type": "boolean"},
                                    "creator": {
                                        "type": "object",
                                        "properties": {
                                            "username": {"type": "string"},
                                            "display_name": {"type": "string"},
                                        },
                                    },
                                    "updator": {
                                        "type": "object",
                                        "properties": {
                                            "username": {"type": "string"},
                                            "display_name": {"type": "string"},
                                        },
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
@requires_permission("timed:u")
async def update_timed(request):
    data = request.json
    timed_id = data.get("id")
    if not timed_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)

    # 鉴权由 @requires_permission('timed:u') + 租户隔离守门，与全平台可见性/操作语义一致。
    timed = await Timed.get_or_none(
        id=timed_id,
        deleted=False,
        internal=internal,
        tenant_id=tenant_id,
    ).prefetch_related("action", "playbook", "creator", "updator")
    if not timed:
        raise CustomError(ErrorCode.TIMED_NOT_FOUND)

    run_type = int(data.get("type", timed.type))
    if run_type not in [RUN_TYPE_FLOW, RUN_TYPE_ACTION]:
        raise CustomError(ErrorCode.PARAM_ERROR)

    enable = data.get("enable", timed.enable)
    status = data.get("status", timed.status)
    tactics = data.get("tactics", timed.tactics)
    end_time = data.get("end_time", timed.end_time)

    msg = {"run_type": "flow" if run_type == RUN_TYPE_FLOW else "action"}

    if run_type == RUN_TYPE_FLOW:
        playbook_id = data.get("playbook_id")
        playbook_input = data.get("playbook_input")

        if not playbook_id or not playbook_input:
            if not timed.playbook_input:
                raise CustomError(ErrorCode.PARAM_ERROR)
            else:
                playbook_input = timed.playbook_input
                playbook_id = timed.playbook.id

        timed.playbook = await Playbook.get_or_none(
            id=playbook_id,
            deleted=False,
            internal=bool(timed.internal),
            tenant_id=tenant_id,
        )
        if not timed.playbook:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
        playbook_version = await PlaybookVersion.get_or_none(
            id=playbook_input.get("flow_id"),
            playbook_id=timed.playbook.id,
            deleted=False,
            tenant_id=tenant_id,
        )
        if not playbook_version:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
        timed.playbook_version = playbook_version.version

        # 与 add 侧一致：写入前校验对目标剧本有执行权(e)，超管豁免
        editor_account = request.ctx.session["account_info"]
        if not await check_playbook_permission(request, editor_account, playbook_id, ["e"]):
            raise CustomError(ErrorCode.PLAYBOOK_UNAUTHORIZED)

        playbook_input["executor"] = editor_account.id

        if any(key not in playbook_input.keys() for key in ["flow_id", "flow_input", "executor"]):
            raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)

        timed.playbook_input = playbook_input
        timed.action = None
        timed.action_input = {}
        msg["run_args"] = playbook_input

    else:
        if not timed.action and not data.get("action_id"):
            raise CustomError(ErrorCode.PARAM_ERROR)

        action_id = data.get("action_id", timed.action.id if timed.action else None)
        action_input = data.get("action_input", timed.action_input)

        timed.action = await ToolAction.get_or_none(
            id=action_id, deleted=False, tenant_id=tenant_id
        )
        tool = await _get_action_tool_in_timed_scope(
            timed.action,
            tenant_id,
            timed.internal,
        )
        if not tool:
            raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

        editor_account = request.ctx.session["account_info"]
        action_input = _canonicalize_timed_action_input(
            timed.action,
            tool,
            action_input,
            editor_account.id,
        )
        if any(
            key not in action_input
            for key in [
                "app_name",
                "name",
                "app_ver",
                "main",
                "action_input",
                "resource",
                "node_name",
                "action_name",
                "action",
                "executor",
                "retry_times",
                "timeout",
            ]
        ):
            raise CustomError(ErrorCode.ACTION_INPUT_ERROR)
        # 与 add 侧一致：写入前校验对 (tool, action, resource) 有执行权，超管豁免
        if not await check_action_execute_permission(
            editor_account,
            action_input.get("app_name"),
            action_input.get("action"),
            action_input.get("resource") or "",
            tenant_id,
            bool(timed.internal),
            action_input.get("app_ver"),
        ):
            raise CustomError(ErrorCode.UNAUTH)

        timed.action_input = action_input
        timed.playbook = None
        timed.playbook_version = None
        timed.playbook_input = {}
        msg["run_args"] = action_input

    if enable == DISABLED:
        msg["timer"] = "remove"
        msg["timer_rule"] = {}
    else:
        if status == EXEC_TYPE_CRON:
            if not is_valid_cron_expression(tactics):
                logger.info(f"定时任务{timed.name}的执行策略{tactics}不符合crontab格式，更新失败")
                raise CustomError(ErrorCode.CRON_EXPRESSION_ERROR)
        timer_info = build_timer_rule(status, tactics, end_time)
        msg.update(timer_info)
    msg["timer_id"] = timed.id
    msg["tenant_id"] = tenant_id

    timed.enable = enable
    timed.status = status
    timed.tactics = tactics
    timed.end_time = end_time
    timed.type = run_type
    timed.updator = request.ctx.session.get("account_info")

    await timed.save()
    await aproduce(TASK_TOPIC, msg)
    return response_ok(timed.json)


@timed_bp.post("/delete")
@openapi.definition(
    summary="删除定时任务",
    description="删除定时任务，需要定时任务删除权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "定时任务id"}},
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("timed:d")
async def delete_timed(request):
    timed_id = request.json.get("id")
    if not timed_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)

    timed = await Timed.get_or_none(
        id=timed_id,
        deleted=False,
        internal=internal,
        tenant_id=tenant_id,
    )
    if not timed:
        raise CustomError(ErrorCode.TIMED_NOT_FOUND)
    if timed.internal:
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)
    msg = {}
    if timed.type == RUN_TYPE_FLOW:
        msg["run_type"] = "flow"
        msg["run_args"] = timed.playbook_input
    else:
        msg["run_type"] = "action"
        msg["run_args"] = timed.action_input
    msg["timer"] = "remove"
    msg["timer_id"] = timed.id
    msg["timer_rule"] = {}
    msg["tenant_id"] = tenant_id
    await aproduce(TASK_TOPIC, msg)

    timed.deleted = True
    timed.updator = request.ctx.session.get("account_info")
    await timed.save(update_fields=["deleted", "updator_id", "utime"])
    return response_ok("定时任务删除成功")


@timed_bp.post("/runlog")
@openapi.definition(
    summary="获取定时任务执行日志",
    description="获取定时任务执行日志，需要定时任务查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "page", "size"],
                "properties": {
                    "id": {"type": "string", "description": "定时任务id"},
                    "page": {"type": "integer", "description": "页码，默认1"},
                    "size": {"type": "integer", "description": "页大小，默认10"},
                    "start_time": {
                        "type": "string",
                        "description": "开始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "status": {
                        "type": "integer",
                        "description": "状态 0为成功，1为失败",
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
                                "type": "object",
                                "properties": {
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {"type": "string"},
                                                    "ctime": {"type": "string"},
                                                    "utime": {"type": "string"},
                                                    "run_id": {"type": "string"},
                                                    "event_id": {"type": "string"},
                                                    "node_id": {"type": "string"},
                                                    "node_name": {"type": "string"},
                                                    "node_type": {"type": "string"},
                                                    "tool_name": {"type": "string"},
                                                    "tool_version": {"type": "string"},
                                                    "tool_display_name": {"type": "string"},
                                                    "action_name": {"type": "string"},
                                                    "action_display_name": {"type": "string"},
                                                    "status": {"type": "integer"},
                                                    "args": {"type": "object"},
                                                    "result": {"type": "string"},
                                                    "start_time": {"type": "string"},
                                                    "end_time": {"type": "string"},
                                                    "executor": {"type": "string"},
                                                    "resource_name": {"type": "string"},
                                                    "playbook_name": {"type": "string"},
                                                    "playbook_version_id": {"type": "string"},
                                                    "timer_id": {"type": "string"},
                                                },
                                            },
                                        },
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
@requires_permission("timed:r")
async def get_run_log(request):
    req_json = request.json or {}
    timed_id = req_json.get("id")
    page = max(1, int(req_json.get("page") or 1))
    size = max(1, min(int(req_json.get("size") or 15), 100))
    start_time = req_json.get("start_time")
    end_time = req_json.get("end_time")
    status = req_json.get("status")

    if not timed_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)
    if not await Timed.filter(
        id=timed_id,
        deleted=False,
        internal=internal,
        tenant_id=tenant_id,
    ).exists():
        raise CustomError(ErrorCode.TIMED_NOT_FOUND)

    condition = Q(timer_id=timed_id, deleted=False, tenant_id=tenant_id)

    if start_time:
        start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        condition &= Q(start_time__gte=start_dt)
    if end_time:
        end_dt = datetime.datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        condition &= Q(start_time__lte=end_dt)
    if status in [0, 1]:
        condition &= Q(status=int(status))

    if req_json.get("summary_only"):
        total, run_logs = await paginated_runlog_summaries(Runlog, condition, page, size)
    else:
        total, run_logs = await paginated_runlog_groups(Runlog, condition, page, size)

    return response_ok({"page": page, "size": size, "total": total, "data": run_logs})


async def _require_timed_run_scope(request, timed_id: str, run_id: str) -> str:
    tenant_id = request.ctx.session.get("tenant_id")
    internal = await get_current_timed_internal_scope(request)
    if not await Timed.filter(id=timed_id, deleted=False, internal=internal, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.TIMED_NOT_FOUND)
    if not await Runlog.filter(timer_id=timed_id, run_id=run_id, tenant_id=tenant_id, deleted=False).exists():
        raise CustomError(ErrorCode.NOT_FOUND)
    return tenant_id


@timed_bp.post("/runlog/nodes")
@openapi.definition(summary="分页获取定时任务单次执行的节点摘要")
@requires_permission("timed:r")
async def get_run_log_nodes(request):
    data = request.json or {}
    timed_id = str(data.get("id") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    if not timed_id or not run_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = await _require_timed_run_scope(request, timed_id, run_id)
    return response_ok(
        await runlog_node_page(
            Runlog,
            Q(timer_id=timed_id, tenant_id=tenant_id, deleted=False),
            run_id=run_id,
            cursor=data.get("cursor"),
            limit=data.get("limit"),
            cursor_kind="timed-node",
            cursor_scope={"timed_id": timed_id},
            node_id=str(data.get("node_id") or "").strip() or None,
            rows_cursor=data.get("rows_cursor"),
            loop_limit=data.get("loop_limit"),
        )
    )


@timed_bp.post("/runlog/detail")
@openapi.definition(summary="获取定时任务单条运行日志详情")
@requires_permission("timed:r")
async def get_run_log_detail(request):
    data = request.json or {}
    timed_id = str(data.get("id") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    runlog_id = str(data.get("runlog_id") or "").strip()
    if not timed_id or not run_id or not runlog_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = await _require_timed_run_scope(request, timed_id, run_id)
    runlog = await Runlog.get_or_none(id=runlog_id, timer_id=timed_id, run_id=run_id, tenant_id=tenant_id, deleted=False)
    if not runlog:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(runlog.json)


async def timer_success_rate(timed: Timed):
    previous_success_rate = timed.success_rate
    previous_result = timed.result
    recent_runs = await Runlog.filter(timer_id=timed.id, deleted=False).annotate(latest_start_time=Max("start_time"), latest_ctime=Max("ctime")).group_by("run_id").order_by("-latest_start_time", "-latest_ctime").limit(10).values("run_id")
    run_ids = [row["run_id"] for row in recent_runs]
    logs = await Runlog.filter(timer_id=timed.id, run_id__in=run_ids, deleted=False).all() if run_ids else []

    # 按一次运行聚合成功率。Runlog 是节点级记录，同一次 run_id 下任一节点失败即该次运行失败。
    if logs:
        runs = {}
        for log in logs:
            runs.setdefault(log.run_id, []).append(log)
        success_count = sum(1 for run_logs in runs.values() if all(log.status in SUCCESS_RUNLOG_STATUSES for log in run_logs))
        timed.success_rate = f"{round(success_count / len(runs) * 100, 2)}%"
        latest_run_id = run_ids[0]
        latest_logs = runs.get(latest_run_id, [])
        if latest_logs and any(log.status in RUNNING_RUNLOG_STATUSES for log in latest_logs):
            timed.result = "running"
        elif latest_logs and all(log.status in SUCCESS_RUNLOG_STATUSES for log in latest_logs):
            timed.result = "success"
        else:
            timed.result = "fail"
    else:
        timed.success_rate = None
        timed.result = None

    if timed.success_rate != previous_success_rate or timed.result != previous_result:
        await timed.save(update_fields=["success_rate", "result", "utime"])

    return {
        "id": timed.id,
        "result": timed.result,
        "success_rate": timed.success_rate,
    }
