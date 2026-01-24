# coding=utf-8

import datetime

from croniter import croniter
import pytz
from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from libs.utils import strip_timezone_to_seconds
from models import (
    Account,
    ActionPermissions,
    Playbook,
    PlaybookPermissions,
    PlaybookVersion,
    Runlog,
    Timed,
    ToolAction,
)
from services.workflow.views import get_playbook_input
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
                        "description": "执行策略 如果为单次执行，则传此类型参数2025-12-12 00:00:00，如果为周期执行，则传*/5 * * * *类型参数",
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
    data = request.json
    name = data.get("name")
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

    if await Timed.filter(name=request.json.get("name"), deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.TIMED_NAME_EXISTS)

    msg = {}
    playbook = None
    action = None
    playbook_version = None
    timer_rule = {}
    if operate_type == 0:
        playbook = await Playbook.get_or_none(id=playbook_id, deleted=False, tenant_id=tenant_id)
        playbook_version = await PlaybookVersion.get_or_none(id=playbook_input.get("flow_id"), tenant_id=tenant_id)
        if not playbook:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
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
            if not croniter.is_valid(tactics):
                logger.info(f"定时任务{name}的执行策略{tactics}不符合crontab格式，创建失败")
                raise CustomError(ErrorCode.CRON_EXPRESSION_ERROR)
            timer_rule["crontab"] = tactics
    elif operate_type == 1:
        action = await ToolAction.get_or_none(id=action_id, deleted=False, tenant_id=tenant_id)
        if not action:
            raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)
        action_input["executor"] = request.ctx.session.get("account_info").id
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
        msg["run_type"] = "action"
        msg["run_args"] = action_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            if not croniter.is_valid(tactics):
                logger.info(f"定时任务{name}的执行策略{tactics}不符合crontab格式，创建失败")
                raise CustomError(ErrorCode.CRON_EXPRESSION_ERROR)
            timer_rule["crontab"] = tactics
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
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
        internal=maintenance_mode if maintenance_mode else False,
        creator=request.ctx.session["account_info"],
        updator=request.ctx.session["account_info"],
        tenant_id=tenant_id,
    )
    msg["timer_id"] = timed.id
    msg["timer_rule"] = timer_rule
    msg["tenant_id"] = tenant_id
    await aproduce(TASK_TOPIC, msg)
    return response_ok(timed.json)


async def get_playbook_permissions(pb_id, subject, cache_client, tenant_id, is_user: bool = True):
    cache_key = (pb_id, subject)
    data = await cache_client.get(cache_key)
    filter_kwargs = {"tenant_id": tenant_id}
    if not data:
        filter_kwargs["pb_id"] = pb_id
        if is_user:
            filter_kwargs["users_id"] = subject
        else:
            filter_kwargs["roles_id"] = subject
        pp = await PlaybookPermissions.get_or_none(**filter_kwargs)
        if pp:
            data = pp.permissions
    return data


async def get_timed_permissions(request, filtered_items):
    result = []
    timed_permissions = {}
    user = request.ctx.session["account_info"]
    user_id = user.id
    user_roles = set(str(role.id) for role in user.roles)
    cache_client = request.app.ctx.cache_client

    tenant_id = request.ctx.session.get("tenant_id")

    for item in filtered_items:
        if item.playbook_id:
            # 先查用户权限
            data = await get_playbook_permissions(item.playbook_id, user_id, cache_client, tenant_id, is_user=True)
            if data and any(perm in data for perm in ("r", "e")):
                result.append(item)
                timed_permissions[item.id] = data
                continue

            # 再查角色权限
            for role_id in user_roles:
                data = await get_playbook_permissions(item.playbook_id, role_id, cache_client, tenant_id, is_user=False)
                if data and any(perm in data for perm in ("r", "e")):
                    result.append(item)
                    timed_permissions[item.id] = data
                    break
        else:
            # 动作权限处理
            permissions = await ActionPermissions.filter(
                tool_name=item.action_input["app_name"],
                action_name__in=[item.action_input["action"], "all"],
                deleted=False,
                tenant_id=tenant_id,
            )

            resource = item.action_input["resource"]
            if not resource:
                result.append(item)
                timed_permissions[item.id] = ["r", "e"]
                continue
            # 用户权限
            if "," in resource:
                resources = resource.split(",")
            else:
                resources = [resource]
            for permission in permissions:
                for resource in resources:
                    for res_dict in permission.users or []:
                        if resource in res_dict and user_id in map(str, res_dict[resource]):
                            result.append(item)
                            timed_permissions[item.id] = ["r", "e"]
                            break
                    else:  # 只有用户不满足时再检查角色
                        for res_dict in permission.roles or []:
                            if resource in res_dict and not user_roles.isdisjoint(map(str, res_dict[resource])):
                                result.append(item)
                                timed_permissions[item.id] = ["r", "e"]
                                break
    return result, timed_permissions


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
                    "scope": {"type": "integer", "description": "范围：0非内置，1内置，2全部；不传则按维护模式"},
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
    scope = req_json.get("scope")
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])

    tenant_id = request.ctx.session.get("tenant_id")

    condition = Q(deleted=False, tenant_id=tenant_id)

    internal = None
    if scope in [0, "0"]:
        internal = False
    elif scope in [1, "1"]:
        internal = True
    elif scope in [2, "2"]:
        internal = None
    else:
        internal = maintenance_mode if maintenance_mode else False

    if internal is not None:
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

    all_items = await Timed.filter(condition).prefetch_related("action", "playbook", "creator", "updator")

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

    # 权限过滤
    result, permissions = await get_timed_permissions(request, filtered_items)

    total = len(result)
    start = (page - 1) * size
    end = start + size
    paged_items = result[start:end]

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
            "permissions": permissions.get(item.id, []),
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
    items = await Timed.filter(id__in=request.json.get("ids"), deleted=False, tenant_id=tenant_id).all()
    return response_ok([await timer_success_rate(item) for item in items])


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

    timed = await Timed.get_or_none(id=timed_id, deleted=False, tenant_id=tenant_id).prefetch_related("action", "playbook", "creator", "updator")

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
                        "description": "执行策略 如果为单次执行，则传此类型参数2025-12-12 00:00:00，如果为周期执行，则传*/5 * * * *类型参数",
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

    timed = await Timed.get(id=timed_id, deleted=False, tenant_id=tenant_id).prefetch_related("action", "playbook", "creator", "updator")
    result, _ = await get_timed_permissions(request, [timed])

    if not result:
        raise CustomError(ErrorCode.UNAUTH)

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

        timed.playbook = await Playbook.get_or_none(id=playbook_id, deleted=False, tenant_id=tenant_id)
        playbook_version = await PlaybookVersion.get_or_none(id=playbook_input.get("flow_id"), tenant_id=tenant_id)
        timed.playbook_version = playbook_version.version if playbook_version else None

        playbook_input["executor"] = request.ctx.session.get("account_info").id

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

        timed.action = await ToolAction.get_or_none(id=action_id, deleted=False, tenant_id=tenant_id)

        action_input["executor"] = request.ctx.session.get("account_info").id
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
            if not croniter.is_valid(tactics):
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

    timed = await Timed.get_or_none(id=timed_id, deleted=False, tenant_id=tenant_id)
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
    await timed.save()
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

    condition = Q(timer_id=timed_id, deleted=False)

    if start_time:
        start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        condition &= Q(start_time__gte=start_dt)
    if end_time:
        end_dt = datetime.datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        condition &= Q(start_time__lte=end_dt)
    if status in [0, 1]:
        condition &= Q(status=int(status))

    total = await Runlog.filter(condition).order_by("-run_id").distinct().values_list("run_id", flat=True)

    run_ids = await Runlog.filter(condition).distinct().order_by("-run_id").offset((page - 1) * size).limit(size).values_list("run_id", flat=True)
    run_logs = []
    if run_ids:
        all_logs = await Runlog.filter(run_id__in=run_ids, deleted=False).order_by("-ctime").prefetch_related("timer", "playbook_version").all()
        run_logs = await runlog_list(all_logs)

    return response_ok({"page": page, "size": size, "total": len(total), "data": run_logs})


# 用于统一处理runlog返回格式
async def runlog_list(all_logs):
    run_logs = []
    logs_by_run_id = {}
    for log in all_logs:
        # 处理runlog中的过期的审核节点
        log_data = log.json
        creator = await Account.get_or_none(id=log_data["creator"])
        log_data["creator_username"] = creator.username if creator else None
        log_data["creator_display_name"] = creator.display_name if creator else None
        if (log.run_id, log.node_id) not in logs_by_run_id.setdefault(log.run_id, {}).keys():
            logs_by_run_id[log.run_id][(log.run_id, log.node_id)] = [log_data]
        else:
            logs_by_run_id[log.run_id][(log.run_id, log.node_id)].append(log_data)

    for value in logs_by_run_id.values():
        data = []
        for son_value in value.values():
            data.append(son_value)
        run_logs.append(data)

    return run_logs


async def timer_success_rate(timed: Timed):
    logs = await Runlog.filter(timer_id=timed.id, deleted=False).order_by("-start_time").limit(10)

    # 计算成功率
    if logs:
        success_count = sum(1 for log in logs if log.status == 0)
        timed.success_rate = f"{round(success_count / len(logs) * 100, 2)}%"
        # 最近一次执行结果
        timed.result = "success" if logs[0].status == 0 else "fail"
    else:
        timed.success_rate = None
        timed.result = None

    await timed.save()

    return {
        "id": timed.id,
        "result": timed.result,
        "success_rate": timed.success_rate,
    }
