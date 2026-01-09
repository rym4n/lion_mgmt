# coding=utf-8

import orjson
from sanic import response
from sanic_ext import openapi

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.security.authorization import requires_permission
from models import (
    Runlog,
    ToolAction,
    PlaybookVersion,
    Tool,
    ToolResourcesModel,
    ToolResources,
)


@openapi.exclude()
@requires_permission("warroom:r")
async def activity(request, activityId):
    if not activityId:
        raise CustomError(ErrorCode.ParamValueError)

    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    logs = await Runlog.filter(run_id=activityId, deleted=False).all()
    result = {}
    if logs[0].playbook_name != "执行动作":
        executorInstanceId = logs[0].playbook_version_id
        executorInstanceName = logs[0].playbook_name
        executorInstanceType = "PLAYBOOK"
    else:
        actions = await ToolAction.filter(tool_name=logs[0].tool_name, version_number=logs[0].tool_version, tenant_id=tenant_id).all()
        for action in actions:
            if action.action["func"] == logs[0].action_name and action.action["name"] == logs[0].action_display_name:
                executorInstanceId = action.id
                executorInstanceName = logs[0].action_display_name
                executorInstanceType = "ACTION"
                break
    result["id"] = logs[0].id
    result["activityId"] = logs[0].run_id
    result["eventId"] = logs[0].event_id
    result["executorInstanceId"] = executorInstanceId
    result["executorInstanceName"] = executorInstanceName
    result["executorInstanceType"] = executorInstanceType
    result["execMode"] = None
    result["postStatus"] = None
    result["postCode"] = None
    result["postMsg"] = None
    status = []
    for log in logs:
        status.append(log.status)
    if 1 in status:
        result["excuteStatus"] = "RUNNING"
    elif 2 in status:
        result["excuteStatus"] = "WAITING_APPROVE"
    elif -1 in status:
        result["excuteStatus"] = "FAIL"
    else:
        result["excuteStatus"] = "SUCCESS"

    return response.json({"code": 0, "data": [log.json for log in logs], "result": result})


@openapi.exclude()
@requires_permission("workflow:r")
async def playbook_list(request):
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 10))
    offset = (page - 1) * size
    limit = size

    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    pvs = await PlaybookVersion.filter(deleted=False, tenant_id=tenant_id).offset(offset).limit(limit).prefetch_related("playbook", "creator", "updator", "playbook__scenes", "playbook__tags")

    content = []
    for pv in pvs:
        data = {
            "id": pv.id,
            "name": pv.playbook.name,
            "drawJson": pv.flow_json,
            "description": pv.playbook.remark,
            "displayName": pv.playbook.name,
            "publishStatus": "ONLINE",
            "grade": None,
            "playbookCategory": "COMMONEVENT",
            "sceneList": [
                {
                    "id": scene.id,
                    "name": scene.name,
                    "display": scene.remark,
                    "severity": str(scene.status),
                    "effective": True if scene.status == 1 else False,
                }
                for scene in pv.playbook.scenes
            ],
            "labelList": [{"id": tag.id, "name": tag.name} for tag in pv.playbook.tags],
        }
        content.append(data)
    total = await PlaybookVersion.filter(deleted=False, tenant_id=tenant_id).count()

    return response.json(
        {
            "code": 0,
            "result": {
                "content": content,
                "page": page,
                "first": True if page == 1 else False,
                "last": True if page * size >= total else False,
                "size": size,
                "totalElements": total,
                "empty": False if content else True,
            },
        }
    )


@openapi.exclude()
@requires_permission("tool:r")
async def all_apps(request):
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 10))
    offset = (page - 1) * size
    limit = size

    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    tools = await Tool.filter(deleted=False).offset(offset).limit(limit)
    content = []
    for tool in tools:
        actions = await ToolAction.filter(tool_name=tool.name, version_number=tool.version, deleted=False, tenant_id=tenant_id).all()
        resource_model = await ToolResourcesModel.get(tool_name=tool.name, deleted=False)
        resources = await ToolResources.filter(tool_name=tool.name, deleted=False).all()
        data = {
            "id": tool.id,
            "name": tool.name,
            "description": tool.description,
            "displayName": tool.description,
            "version": tool.version,
            "readMePath": None,
            "iconfilePath": None,
            "os": None,
            "logicLanguage": "Python",
            "logicLanguageVersion": "",
            "category": tool.tag,
            "assetVariableList": [
                {
                    "name": key,
                    "required": True if value.get("required") else False,
                    "type": value.get("data_type"),
                    "description": value.get("description"),
                    "lable": key,
                    "defaultValue": None,
                    "optionsList": None,
                    "multiSelected": False,
                    "concat": None,
                    "order": value.get("order"),
                    "array": False,
                }
                for key, value in resource_model.resources.items()
            ],
            "appActionList": [
                {
                    "id": action.id,
                    "appId": tool.id,
                    "name": action.action.get("func"),
                    "actionType": action.action.get("classify"),
                    "readonly": (False if action.action.get("classify") == "write" else True),
                    "description": action.action.get("name"),
                    "displayName": action.action.get("name"),
                    "classify": action.action.get("classify"),
                    "os": None,
                    "logicLanguage": "Python",
                    "logic": None,
                    "retryTimes": 0,
                    "sleepSeconds": 0,
                    "quartzEtl": "",
                    "parameterVariableList": [
                        {
                            "name": param.get("key"),
                            "required": True if param.get("required") else False,
                            "type": param.get("type"),
                            "description": param.get("description"),
                            "lable": param.get("key"),
                            "defaultValue": None,
                            "optionsList": None,
                            "multiSelected": False,
                            "concat": None,
                            "order": None,
                            "array": False,
                        }
                        for param in action.action.get("input")
                    ],
                    "resultVariableList": [
                        {
                            "description": None,
                            "valueType": None,
                            "dataPath": None,
                            "exampleValues": None,
                        }
                    ],
                    "isTest": False,
                    "resultDisplayTmptType": None,
                    "quartz": False,
                }
                for action in actions
            ],
            "appAssetList": [
                {
                    "id": resource.id,
                    "appid": tool.id,
                    "name": resource.name,
                    "description": None,
                    "productVendor": None,
                    "productName": None,
                    "assetSettingList": [
                        {
                            "name": key,
                            "value": value.get("value"),
                            "type": value.get("data_type"),
                        }
                        for key, value in orjson.loads(resource.resources).items()
                    ],
                    "actionPermissionMap": {},
                    "lastHealthCheckTime": None,
                    "healthStatus": None,
                    "actionEngineType": resource.engine,
                }
                for resource in resources
            ],
            "logicContent": None,
            "requireAsset": True if resource_model.status == 1 else False,
            "actionEngineType": None,
        }
        content.append(data)
    total = await Tool.filter(deleted=False).count()
    return response.json(
        {
            "code": 0,
            "result": {
                "content": content,
                "page": page,
                "first": True if page == 1 else False,
                "last": True if page * size >= total else False,
                "size": size,
                "totalElements": total,
                "empty": False if content else True,
            },
        }
    )
