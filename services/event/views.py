# coding=utf-8
import asyncio
from datetime import datetime

import orjson
import pytz
from sanic import Blueprint, response
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from libs.utils import gen_random_code, gen_run_id, strip_timezone_to_seconds
from models import Account, Events, EventsIngestion, PlaybookVersion, Role, Room, Runlog, Tool, ToolAction, ToolResources
from services.timed.views import runlog_list
from settings import TASK_TOPIC, TZ

TYPE_DICT = {"PLAYBOOK": "flow", "ACTION": "action"}

event_bp = Blueprint("event", url_prefix="/event")
_all__ = ["event_bp"]

PROCESSSTATUS = {
    "已处理": "POCESSED",
    "处理中": "PROCESSING",
    "待处理": "OPENED",
}

STATUS = {
    "已处理": "RESOLVED",
    "处理中": "OPEN",
    "待处理": "NEW",
}

# 将aioe的节点状态映射成雾帜的节点状态
NODE_TYPE = {
    "start-node": "START",
    "end-node": "END",
    "judge-node": "RULE",
    "action-node": "ACTION",
    "approval-node": "APPROVE",
    "subscript-node": "PLAYBOOK",
}


@event_bp.post("/list")
@openapi.definition(
    summary="获取指定页数的事件列表",
    description="获取指定页数的事件（支持条件筛选）(需要事件查询权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码（默认1）"},
                    "size": {"type": "integer", "description": "每页数量（默认15）"},
                    "event_name": {
                        "type": "string",
                        "description": "事件名称（支持模糊搜索）",
                    },
                    "event_process_status": {
                        "type": "array",
                        "description": "事件处理状态（精确匹配）",
                        "items": {
                            "type": "string",
                            "enum": ["已处理", "处理中", "待处理"],
                        },
                    },
                    "event_source": {
                        "type": "integer",
                        "description": "事件来源（精确匹配）",
                    },
                    "ctime": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "起止时间范围",
                        "example": ["2025-10-19 00:00:00", "2025-10-29 00:00:00"],
                    },
                },
            }
        }
    },
    response={"events": [], "total": 0, "page": 1, "size": 15, "total_pages": 0},
)
@requires_permission("event:r")
async def get_all_events(request):
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 15))
    offset = (page - 1) * size
    limit = size

    tenant_id = request.ctx.session.get("tenant_id")

    filters = {"tenant_id": tenant_id}
    if request.json.get("event_name"):
        filters["event_name__icontains"] = request.json.get("event_name")
    if request.json.get("event_process_status"):
        filters["event_process_status__in"] = request.json.get("event_process_status")
    if request.json.get("event_source"):
        filters["event_source"] = request.json.get("event_source")
    if len(request.json.get("ctime") or []) == 2:
        ctime = [TZ.localize(datetime.strptime(t, "%Y-%m-%d %H:%M:%S")) for t in request.json.get("ctime")]
        filters["ctime__range"] = ctime

    events = await Events.filter(**filters).offset(offset).limit(limit).all().prefetch_related("room", "creator", "updator", "scene")
    total = await Events.filter(**filters).count()

    data = []

    for event in events:
        event_dict = {}
        role_list = []
        user_list = []
        if event.owner:
            role_ids = event.owner["role"]
            user_ids = event.owner["user"]

            role_objs = await Role.filter(id__in=role_ids, tenant_id=tenant_id)
            user_objs = await Account.filter(id__in=user_ids)

            role_list = [{r.id: r.name} for r in role_objs]
            user_list = [{u.id: {"display_name": u.display_name, "username": u.username}} for u in user_objs]

        event_dict.update(event.json)

        event_dict["owner"] = {"role": role_list, "user": user_list}
        event_dict["scene"] = event.scene.name if event.scene else ""

        event_ingestion = await EventsIngestion.get_or_none(event_ingestion_name=event.event_source, deleted=False, tenant_id=tenant_id).prefetch_related("creator")
        if event_ingestion:
            event_dict["event_source"] = {
                "name": event_ingestion.event_ingestion_name,
                "describe": event_ingestion.event_ingestion_describe,
                "transforms_type": event_ingestion.transforms_type,
                "enabled": event_ingestion.enabled_status,
                "ctime": event_ingestion.ctime.astimezone(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                "creator_id": (
                    {
                        "username": event_ingestion.creator.username,
                        "display_name": event_ingestion.creator.display_name,
                    }
                    if event_ingestion.creator
                    else {}
                ),
            }

        data.append(event_dict)

    return response_ok(
        {
            "events": data,
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
        }
    )


@event_bp.post("/artificial")
@openapi.exclude()
@requires_permission("event:c")
async def artificial_create_event(request):
    event_name = request.json.get("eventName")
    event_process_status = "待处理"
    event_source = "人工"
    owner = {"role": [], "user": [request.ctx.session["account_info"].id or ""]} or {}
    user = request.ctx.session["account_info"]
    event_summary = request.json.get("eventSummary") or ""
    event_ingestion_id = ""
    log_data = ""

    tenant_id = request.ctx.session.get("tenant_id")

    if await Events.filter(event_name=event_name, deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.EVENT_NAME_EXISTS)

    async with in_transaction():
        event = await Events.create(
            event_name=event_name,
            event_process_status=event_process_status,
            event_source=event_source,
            owner=owner,
            creator=user,
            event_summary=event_summary,
            updator=user,
            event_ingestion_id=event_ingestion_id,
            log_data=log_data,
            tenant_id=tenant_id,
        )
        await event.fetch_related("scene")
        run_log = await Runlog.filter(event_id=event.id, deleted=False).first()

        user_ids = event.owner["user"]

        user_objs = await Account.filter(id__in=user_ids)

        members = [
            {
                "eventId": event.id,
                "userId": u.id,
                "username": u.username,
                "eventName": event.event_name,
                "follow": True,
            }
            for u in user_objs
        ]
    return response.json(
        {
            "code": 0,
            "data": {
                "id": event.id,
            },
            "result": {
                "id": event.id,
                "eventName": event.event_name,
                "categoryName": "",
                "categoryId": 0,
                "ownerId": request.ctx.session["account_info"].id,
                "eventSummary": event.event_summary,
                "eventStatus": STATUS[event.event_process_status],
                "eventProcessStatus": PROCESSSTATUS[event.event_process_status],
                "sceneName": event.scene.name if event.scene else "",
                "sceneDisplay": event.scene.remark if event.scene else "",
                "logId": run_log.id if run_log else "",
                "relationEventId": 0,
                "relationType": "UNKNOWN",
                "eventSource": "MANUAL",
                "bizName": "",
                "openTime": None,
                "resolvedTime": event.close_time,
                "categoryDisplayName": None,
                "resolvedBy": "",
                "preBizModel": None,
                "logModel": None,
                "permissions": [
                    {
                        "id": None,
                        "eventId": event.id,
                        "ownerType": None,
                        "ownerId": request.ctx.session["account_info"].id,
                        "permissionBy": "EVENT_OWNER",
                        "relationId": None,
                    }
                ],
                "members": members,
            },
        }
    )


@event_bp.post("/")
@openapi.exclude()
@requires_permission("event:c")
async def artificial_create_event_wuzhi(request):
    event_name = request.json.get("eventName")
    event_process_status = "待处理"
    event_source = "人工"
    owner = {"role": [], "user": [request.ctx.session["account_info"].id or ""]} or {}
    user = request.ctx.session["account_info"]
    event_summary = request.json.get("eventSummary") or ""
    event_ingestion_id = ""
    log_data = ""

    tenant_id = request.ctx.session.get("tenant_id")

    if await Events.filter(event_name=event_name, deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.EVENT_NAME_EXISTS)

    async with in_transaction():
        event = await Events.create(
            event_name=event_name,
            event_process_status=event_process_status,
            event_source=event_source,
            owner=owner,
            creator=user,
            event_summary=event_summary,
            updator=user,
            event_ingestion_id=event_ingestion_id,
            log_data=log_data,
            tenant_id=tenant_id,
        )
        await event.fetch_related("scene")
        run_log = await Runlog.filter(event_id=event.id, deleted=False).first()

        user_ids = event.owner["user"]

        user_objs = await Account.filter(id__in=user_ids)

        members = [
            {
                "eventId": event.id,
                "userId": u.id,
                "username": u.username,
                "eventName": event.event_name,
                "follow": True,
            }
            for u in user_objs
        ]
    return response.json(
        {
            "code": 0,
            "data": {
                "id": event.id,
            },
            "result": {
                "id": event.id,
                "eventName": event.event_name,
                "categoryName": "",
                "categoryId": 0,
                "ownerId": request.ctx.session["account_info"].id,
                "eventSummary": event.event_summary,
                "eventStatus": STATUS[event.event_process_status],
                "eventProcessStatus": PROCESSSTATUS[event.event_process_status],
                "sceneName": event.scene.name if event.scene else "",
                "sceneDisplay": event.scene.remark if event.scene else "",
                "logId": run_log.id if run_log else "",
                "relationEventId": 0,
                "relationType": "UNKNOWN",
                "eventSource": "MANUAL",
                "bizName": "",
                "openTime": None,
                "resolvedTime": event.close_time,
                "categoryDisplayName": None,
                "resolvedBy": "",
                "preBizModel": None,
                "logModel": None,
                "permissions": [
                    {
                        "id": None,
                        "eventId": event.id,
                        "ownerType": None,
                        "ownerId": request.ctx.session["account_info"].id,
                        "permissionBy": "EVENT_OWNER",
                        "relationId": None,
                    }
                ],
                "members": members,
            },
        }
    )


@event_bp.post("/execution")
@openapi.exclude()
@requires_permission("event:r")
async def execution(request):
    eventId = request.json.get("eventId")
    executorInstanceType = request.json.get("executorInstanceType")
    executorInstanceId = request.json.get("executorInstanceId")
    executorActionAssetIds = request.json.get("executorActionAssetIds")
    executorParams = request.json.get("params")
    user = request.ctx.session["account_info"]

    tenant_id = request.ctx.session.get("tenant_id")

    event = await Events.get_or_none(id=eventId, tenant_id=tenant_id)
    if not event.room:
        room_name = event.event_name + "_" + gen_random_code(6)
        if await Room.filter(name=room_name, deleted=False, tenant_id=tenant_id).exists():
            raise CustomError(ErrorCode.ROOM_NAME_EXISTS)
        room = await Room.create(name=room_name, creator=user, tenant_id=tenant_id)
        event.room = room
    room_id = event.room_id
    type = TYPE_DICT.get(executorInstanceType)
    if not room_id or not type:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    params = {}
    input_data = {}
    if type == "flow":
        if executorParams:
            for param in executorParams:
                input_data[param["key"]] = param["value"]
        params["flow_id"] = executorInstanceId
        params["flow_input"] = input_data
    elif type == "action":
        action = await ToolAction.get_or_none(id=executorInstanceId, deleted=False, tenant_id=tenant_id)
        tool = await Tool.get_or_none(name=action.tool_name, version=action.version_number, deleted=False, tenant_id=tenant_id)
        if executorParams:
            for param in executorParams:
                input_data[param["key"]] = param["value"]
        params["app_name"] = tool.name
        params["name"] = tool.description
        params["app_ver"] = tool.version
        params["main"] = tool.main
        params["action_input"] = input_data
        params["node_name"] = action.action["name"]
        params["action"] = action.action["func"]
        params["action_name"] = action.action["name"]
        params["retry_times"] = 1
        params["timeout"] = 300
        params["resource"] = ""
    params["executor"] = user.id
    if executorActionAssetIds and type == "action":
        params["resource"] = executorActionAssetIds
    run_id = gen_run_id()
    msg = {"run_type": type, "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    room = await Room.get_or_none(id=room_id, tenant_id=tenant_id)
    room.updator = user
    room.run_id.append(msg["run_id"])
    await room.save()
    await request.app.ctx.cache_client.set(str(msg["run_id"]), {"room_id": str(room_id), "updator": user.id}, 3600 * 24 * 7)
    await aproduce(TASK_TOPIC, msg)

    return response.json({"code": 0, "data": {"activityId": str(run_id)}, "result": str(run_id)})


@event_bp.get("/activity/<activityId>")
@openapi.exclude()
@requires_permission("event:r")
async def get_activity_result(request, activityId):
    if not activityId:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    logs = await Runlog.filter(run_id=activityId, deleted=False).all()
    activity = {}
    if logs[0].playbook_name != "执行动作":
        executorInstanceId = logs[0].playbook_version_id
        executorInstanceName = logs[0].playbook_name
    else:
        actions = await ToolAction.filter(tool_name=logs[0].tool_name, version_number=logs[0].tool_version, tenant_id=tenant_id).all()
        for action in actions:
            if action.action["func"] == logs[0].action_name and action.action["name"] == logs[0].action_display_name:
                executorInstanceId = action.id
                executorInstanceName = logs[0].action_display_name
                break
    executorActionParams = logs[0].args
    start_time = strip_timezone_to_seconds(logs[0].start_time)
    finish_time = strip_timezone_to_seconds(logs[0].end_time)
    for log in logs:
        if log.node_type == "start-node":
            start_time = strip_timezone_to_seconds(log.start_time)
            executorActionParams = str(log.args)
        if log.node_type == "end-node":
            finish_time = strip_timezone_to_seconds(log.end_time)
    activity["eventId"] = logs[0].event_id
    activity["executorInstanceId"] = executorInstanceId
    activity["executorActionParams"] = executorActionParams
    activity["execMode"] = None
    activity["displayName"] = executorInstanceName
    activity["code"] = 200
    activity["msg"] = "执行成功"
    activity["messageId"] = None
    activity["startTime"] = start_time
    activity["finishTime"] = finish_time
    status = []
    for log in logs:
        status.append(log.status)
    if 1 in status:
        activity["excuteStatus"] = "RUNNING"
    elif 2 in status:
        activity["excuteStatus"] = "WAITING_APPROVE"
    elif -1 in status:
        activity["excuteStatus"] = "FAIL"
    else:
        activity["excuteStatus"] = "SUCCESS"
    node_ids = []
    nodeResults = []
    for log in logs:
        if log.node_id in node_ids:
            break
        node_ids.append(log.node_id)
        tool_action = await ToolAction.filter(tool_name=log.tool_name, version_number=log.tool_version, deleted=False, tenant_id=tenant_id).first()
        action_id = tool_action.id if tool_action and tool_action.action.get("func") == log.action_name else None

        nodes = await Runlog.filter(run_id=log.run_id, node_id=log.node_id, deleted=False).all()

        status = []
        for node in nodes:
            status.append(node.status)
        if 1 in status:
            executeStatus = "RUNNING"
        elif 2 in status:
            executeStatus = "WAITING_APPROVE"
        elif -1 in status and 0 in status:
            executeStatus = "FAIL_PARTLY"
        elif -1 in status and 0 not in status:
            executeStatus = "FAIL"
        else:
            executeStatus = "SUCCESS"

        nodeResults.append(
            {
                "nodeResultModel": {
                    "pnodeResultId": 0,
                    "mnodeResultId": 0,
                    "playbookId": (executorInstanceId if log.playbook_name != "执行动作" else None),
                    "playbookName": (executorInstanceName if log.playbook_name != "执行动作" else None),
                    "nodeId": log.node_id,
                    "nodeName": log.node_name,
                    "actionId": action_id,
                    "actionName": log.action_display_name,
                    "code": 200,
                    "msg": None,
                    "displayName": log.node_name,
                    "nodeType": NODE_TYPE[log.node_type],
                    "executeStatus": executeStatus,
                    "pname": "",
                    "accasion": None,
                    "loopType": "NO",
                    "levelType": "ROOT" if log.node_type == "start-node" else "SUB",
                },
                "assetResultModels": [],
            }
        )
        if log.resource_name:
            for node in nodes:
                resources = await ToolResources.get_or_none(tool_name=node.tool_name, name=node.resource_name, deleted=False, tenant_id=tenant_id)
                nodeResults[-1]["assetResultModels"].append(
                    {
                        "nodeResultId": node.id,
                        "assetId": resources.id if resources else None,
                        "assetName": node.resource_name,
                        "code": 200,
                        "msg": None,
                        "displayName": node.resource_name,
                        "detailSize": 1,
                        "prettyResult": None,
                        "fileData": False,
                        "detailModels": [
                            {
                                "nodeResultId": node.id,
                                "assetResultId": node.id,
                                "playbookId": (executorInstanceId if log.playbook_name != "执行动作" else None),
                                "playbookName": (executorInstanceName if log.playbook_name != "执行动作" else None),
                                "nodeId": node.node_id,
                                "nodeName": node.node_name,
                                "nodeDisplay": node.node_name,
                                "actionId": action_id,
                                "actionName": log.action_display_name,
                                "assetId": resources.id if resources else None,
                                "assetName": node.resource_name,
                                "extend": None,
                                "startTime": strip_timezone_to_seconds(node.start_time),
                                "finishTime": strip_timezone_to_seconds(node.end_time),
                                "jsonData": node.result,
                            }
                        ],
                    }
                )
        else:
            nodeResults[-1]["assetResultModels"].append(None)
    return response.json(
        {
            "code": 0,
            "result": {
                "activity": activity,
                "nodeResults": nodeResults,
            },
        }
    )


@event_bp.post("/create")
@openapi.definition(
    summary="创建事件",
    description="创建事件（需要事件创建权限）",
    body={
        "event_name": "事件名称",
        "event_source": "事件来源",
        "owner": {"role": ["root"], "user": ["test"]},
        "event_summary": "事件概要",
        "event_ingestion_id": "事件接入id",
        "log_data": "日志数据",
    },
    response={"code": 0, "data": "创建成功", "message": "创建成功"},
)
@requires_permission("event:c")
async def create_event(request):
    event_name = request.json.get("event_name")
    event_process_status = "待处理"
    event_source = "人工"
    owner = request.json.get("owner") or {}
    user = request.ctx.session["account_info"]
    event_summary = request.json.get("event_summary") or ""
    event_ingestion_id = request.json.get("event_ingestion_id") or ""
    log_data = request.json.get("log_data") or ""

    tenant_id = request.ctx.session.get("tenant_id")

    if await Events.filter(event_name=event_name, deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.EVENT_NAME_EXISTS)

    async with in_transaction():
        await Events.create(
            event_name=event_name,
            event_process_status=event_process_status,
            event_source=event_source,
            owner=owner,
            creator=user,
            event_summary=event_summary,
            updator=user,
            event_ingestion_id=event_ingestion_id,
            log_data=log_data,
            tenant_id=tenant_id,
        )

    return response_ok("创建成功")


@event_bp.post("/detail")
@openapi.definition(
    summary="获取事件详情",
    description="获取事件详情（需要事件查询权限）",
    body={"id": "事件id"},
    response={"code": 0, "data": {}, "message": "成功"},
)
@requires_permission("event:r")
async def get_event_detail(request):
    event_id = request.json.get("id")

    tenant_id = request.ctx.session.get("tenant_id")

    event = await Events.get(id=event_id, deleted=False, tenant_id=tenant_id).prefetch_related("room", "creator", "updator")
    if not event:
        raise CustomError(ErrorCode.NOT_FOUND)
    creator = request.ctx.session["account_info"]
    first = False
    if not event.room:
        room_name = event.event_name + "_" + gen_random_code(6)
        if await Room.filter(name=room_name, deleted=False, tenant_id=tenant_id).exists():
            raise CustomError(ErrorCode.ROOM_NAME_EXISTS)
        room = await Room.create(name=room_name, creator=creator, tenant_id=tenant_id)
        event.room = room
        first = True
    if event.event_process_status == "待处理":
        event.event_process_status = "处理中"
    await event.save()
    await creator.star_rooms.add(event.room)
    await event.room.users.add(creator)

    event_dict = {"created_name": request.ctx.session["account_info"].username}

    run_logs = await Runlog.filter(event_id=event_id, deleted=False).order_by("ctime").all().prefetch_related("playbook_version")
    run_logs_grouped = await runlog_list(run_logs)
    event_dict["data"] = run_logs_grouped

    event_dict["room_users"] = [user.username if user else "" for user in await event.room.users.all()]
    role_list = []
    user_list = []
    if event.owner:
        role_ids = event.owner["role"]
        user_ids = event.owner["user"]

        role_objs = await Role.filter(id__in=role_ids, tenant_id=tenant_id)
        user_objs = await Account.filter(id__in=user_ids)

        role_list = [{r.id: r.name} for r in role_objs]
        user_list = [{u.id: {"display_name": u.display_name, "username": u.username}} for u in user_objs]

    event_dict.update(event.json)

    event_dict["owner"] = {"role": role_list, "user": user_list}
    event_dict["first"] = first

    return response_ok(event_dict)


@event_bp.post("/update")
@openapi.definition(
    summary="更新事件",
    description="更新事件（需要事件更新权限）",
    body={
        "id": "事件id",
        "event_name": "事件名称",
        "owner": {"role": ["root"], "user": ["test"]},
        "event_summary": "事件概要",
        "event_process_status": "事件处理状态",
        "room_user_id": "作战室新增用户id",
    },
    response={"code": 0, "data": "更新成功", "message": "更新成功"},
)
@requires_permission("event:u")
async def update_event(request):
    params = request.json
    event_id = params.get("id")
    if not event_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    event = await Events.get(id=event_id, deleted=False, tenant_id=tenant_id).prefetch_related("room", "creator", "updator")
    if not event:
        raise CustomError(ErrorCode.NOT_FOUND)

    update_fields = []

    if name := params.get("event_name"):
        event.event_name = name
        update_fields.append("event_name")

    if summary := params.get("event_summary"):
        event.event_summary = summary
        update_fields.append("event_summary")

    if status := params.get("event_process_status"):
        event.event_process_status = status
        if status == "已处理":
            event.download_url = await create_ai_report(event.id, tenant_id)
            event.close_time = TZ.localize(datetime.now())
            update_fields.append("close_time")
            update_fields.append("download_url")
        update_fields.append("event_process_status")

    if room_user_id := params.get("room_user_id"):
        is_member = await event.room.users.filter(id=room_user_id).exists()
        if not is_member:
            user = await Account.get_or_none(id=room_user_id)
            if user:
                await event.room.users.add(user)

    new_owner = params.get("owner")
    if new_owner is not None:
        if not isinstance(event.owner, dict) or not event.owner:
            event.owner = {"role": [], "user": []}
        owner_data = event.owner
        for o in new_owner.get("user", []):
            if o not in owner_data.get("user", []):
                owner_data.get("user", []).append(o)
        for o in new_owner.get("role", []):
            if o not in owner_data.get("role", []):
                owner_data.get("role", []).append(o)
        update_fields.append("owner")

    if update_fields:
        await event.save(update_fields=update_fields)

    return response_ok("更新成功")


async def create_ai_report(event_id, tenant_id):
    pv = await PlaybookVersion.filter(playbook__name="AI", deleted=False, status=1, tenant_id=tenant_id).order_by("-ctime").first()
    if not pv:
        return ""
    download_link = ""
    params = {
        "flow_id": pv.id,
        "flow_input": {"event_id": event_id, "ai_type": "event"},
    }
    run_id = gen_run_id()
    msg = {"run_type": "flow", "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    await aproduce(TASK_TOPIC, msg)
    while True:
        runlog = await Runlog.filter(run_id=run_id, deleted=False).order_by("-ctime").all()
        if runlog:
            for log in runlog:
                if "download_link" in log.result:
                    download_link = orjson.loads(log.result)["data"]["download_link"]
                    return download_link

            if any(log.node_type == "end-node" for log in runlog):
                break
        await asyncio.sleep(1)

    return download_link


@event_bp.post("/report")
@openapi.definition(
    summary="获取报告下载链接",
    description="获取报告下载链接",
    body={"event_id": "事件id"},
    response={
        "code": 0,
        "data": {
            "status": "success",
            "result": {
                "code": 0,
                "msg": "",
                "data": {
                    "download_link": "https://192.168.12.254/api/system/down-file/9c0e33ca-b416-4e1b-a6d7-da80172d81a6.docx",
                    "file_id": "9c0e33ca-b416-4e1b-a6d7-da80172d81a6.docx",
                },
            },
        },
        "message": "生成成功",
    },
)
@requires_permission("event:c")
async def get_report(request):
    event_id = request.json.get("event_id")

    if not event_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    event = await Events.get(id=event_id, tenant_id=tenant_id)

    return response_ok(event.download_url if event.download_url else "")
