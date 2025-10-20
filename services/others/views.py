# coding=utf-8

import orjson
from sanic import response
from sanic_ext import openapi

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Runlog, ToolAction, PlaybookVersion, Tool, ToolResourcesModel, ToolResources


@openapi.definition(
    summary="获取执行记录",
    description="获取执行记录（兼容老接口，请勿使用），需要warroom:r权限",
    deprecated=True,
    parameter=[
        {
            "name": "activityId",
            "in": "path",
            "description": "活动ID",
            "required": True,
            "schema": {
                "type": "string"
            }
        }
    ],
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
                                        "timer_id": {"type": "string"}
                                    }
                                }
                            },
                            "result": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "activityId": {"type": "string"},
                                    "eventId": {"type": "string"},
                                    "executorInstanceId": {"type": "string"},
                                    "executorInstanceName": {"type": "string"},
                                    "executorInstanceType": {"type": "string"},
                                    "execMode": {"type": "string"},
                                    "postStatus": {"type": "string"},
                                    "postCode": {"type": "string"},
                                    "postMsg": {"type": "string"},
                                    "excuteStatus": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("warroom:r")
async def activity(request, activityId):
    if not activityId:
        raise CustomError(ErrorCode.ParamValueError)
    logs = await Runlog.filter(run_id=activityId, deleted=False).all()
    result = {}
    if logs[0].playbook_name != "执行动作":
        executorInstanceId = logs[0].playbook_version_id
        executorInstanceName = logs[0].playbook_name
        executorInstanceType = "PLAYBOOK"
    else:
        actions = await ToolAction.filter(tool_name=logs[0].tool_name, version_number=logs[0].tool_version).all()
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

    return response.json({
        "code": 0,
        "data": [log.json for log in logs],
        "result": result
    })


@openapi.definition(
    summary="获取剧本版本列表",
    description="获取剧本版本列表（兼容老接口，请勿使用），需要workflow:r权限",
    deprecated=True,
    parameter=[
        {
            "name": "page",
            "in": "query",
            "description": "页码",
            "required": False,
            "schema": {
                "type": "integer"
            }
        },
        {
            "name": "size",
            "in": "query",
            "description": "每页数量",
            "required": False,
            "schema": {
                "type": "integer"
            }
        },
        {
            "name": "publishStatus",
            "in": "query",
            "description": "剧本发布状态（当该参数的值为ONLINE时，即可查询可用的剧本）",
            "required": False,
            "schema": {
                "type": "string"
            }
        }
    ],
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "result": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "drawJson": {"type": "object"},
                                                "description": {"type": "string"},
                                                "displayName": {"type": "string"},
                                                "publishStatus": {"type": "string"},
                                                "grade": {"type": "string"},
                                                "playbookCategory": {"type": "string"},
                                                "sceneList": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "display": {"type": "string"},
                                                            "severity": {"type": "string"},
                                                            "effective": {"type": "boolean"}
                                                        }
                                                    }
                                                },
                                                "labelList": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "name": {"type": "string"}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    },
                                    "page": {"type": "integer"},
                                    "first": {"type": "boolean"},
                                    "last": {"type": "boolean"},
                                    "size": {"type": "integer"},
                                    "totalElements": {"type": "integer"},
                                    "empty": {"type": "boolean"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("workflow:r")
async def playbook_list(request):
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 10))
    offset = (page - 1) * size
    limit = size

    pvs = await PlaybookVersion.filter(deleted=False).offset(offset).limit(limit).prefetch_related("playbook", "creator", "updator", "playbook__scenes", "playbook__tags")

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
                    "effective": True if scene.status == 1 else False
                }
                for scene in pv.playbook.scenes
            ],
            "labelList": [
                {
                    "id": tag.id,
                    "name": tag.name
                }
                for tag in pv.playbook.tags
            ]
        }
        content.append(data)
    total = await PlaybookVersion.filter(deleted=False).count()

    return response.json({
        "code": 0,
        "result": {
            "content": content,
            "page": page,
            "first": True if page == 1 else False,
            "last": True if page * size >= total else False,
            "size": size,
            "totalElements": total,
            "empty": False if content else True
        }
    })


@openapi.definition(
    summary="查询所有可用的动作",
    description="查询所有可用的动作（需要工具查看权限）（兼容老接口，请勿使用），需要tool:r权限",
    deprecated=True,
    parameter=[
        {
            "name": "page",
            "in": "query",
            "description": "页码",
            "required": False,
            "schema": {
                "type": "integer"
            }
        },
        {
            "name": "size",
            "in": "query",
            "description": "每页数量",
            "required": False,
            "schema": {
                "type": "integer"
            }
        }
    ],
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "result": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
                                                "displayName": {"type": "string"},
                                                "version": {"type": "string"},
                                                "readMePath": {"type": "string"},
                                                "iconfilePath": {"type": "string"},
                                                "os": {"type": "string"},
                                                "logicLanguage": {"type": "string"},
                                                "logicLanguageVersion": {"type": "string"},
                                                "category": {"type": "array"},
                                                "assetVariableList": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "name": {"type": "string"},
                                                            "required": {"type": "boolean"},
                                                            "type": {"type": "string"},
                                                            "description": {"type": "string"},
                                                            "lable": {"type": "string"},
                                                            "defaultValue": {"type": "string"},
                                                            "optionsList": {"type": "string"},
                                                            "multiSelected": {"type": "boolean"},
                                                            "concat": {"type": "string"},
                                                            "order": {"type": "integer"},
                                                            "array": {"type": "boolean"}
                                                        }
                                                    }
                                                },
                                                "appActionList": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "appId": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "actionType": {"type": "string"},
                                                            "readonly": {"type": "boolean"},
                                                            "description": {"type": "string"},
                                                            "displayName": {"type": "string"},
                                                            "classify": {"type": "string"},
                                                            "os": {"type": "string"},
                                                            "logicLanguage": {"type": "string"},
                                                            "logic": {"type": "string"},
                                                            "retryTimes": {"type": "integer"},
                                                            "sleepSeconds": {"type": "integer"},
                                                            "quartzEtl": {"type": "string"},
                                                            "parameterVariableList": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "object",
                                                                    "properties": {
                                                                        "name": {"type": "string"},
                                                                        "required": {"type": "boolean"},
                                                                        "type": {"type": "string"},
                                                                        "description": {"type": "string"},
                                                                        "lable": {"type": "string"},
                                                                        "defaultValue": {"type": "string"},
                                                                        "optionsList": {"type": "string"},
                                                                        "multiSelected": {"type": "boolean"},
                                                                        "concat": {"type": "string"},
                                                                        "order": {"type": "string"},
                                                                        "array": {"type": "boolean"}
                                                                    }
                                                                }
                                                            },
                                                            "resultVariableList": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "object",
                                                                    "properties": {
                                                                        "description": {"type": "string"},
                                                                        "valueType": {"type": "string"},
                                                                        "dataPath": {"type": "string"},
                                                                        "exampleValues": {"type": "string"}
                                                                    }
                                                                }
                                                            },
                                                            "isTest": {"type": "boolean"},
                                                            "resultDisplayTmptType": {"type": "string"},
                                                            "quartz": {"type": "boolean"}
                                                        }
                                                    }
                                                },
                                                "appAssetList": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "appid": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "description": {"type": "string"},
                                                            "productVendor": {"type": "string"},
                                                            "productName": {"type": "string"},
                                                            "assetSettingList": {
                                                                "type": "array",
                                                                "items": {
                                                                    "type": "object",
                                                                    "properties": {
                                                                        "name": {"type": "string"},
                                                                        "value": {"type": "string"},
                                                                        "type": {"type": "string"}
                                                                    }
                                                                }
                                                            },
                                                            "actionPermissionMap": {"type": "object"},
                                                            "lastHealthCheckTime": {"type": "string"},
                                                            "healthStatus": {"type": "string"},
                                                            "actionEngineType": {"type": "string"}
                                                        }
                                                    }
                                                },
                                                "logicContent": {"type": "string"},
                                                "requireAsset": {"type": "boolean"},
                                                "actionEngineType": {"type": "string"}
                                            }
                                        }
                                    },
                                    "page": {"type": "integer"},
                                    "first": {"type": "boolean"},
                                    "last": {"type": "boolean"},
                                    "size": {"type": "integer"},
                                    "totalElements": {"type": "integer"},
                                    "empty": {"type": "boolean"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def all_apps(request):
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 10))
    offset = (page - 1) * size
    limit = size

    tools = await Tool.filter(deleted=False).offset(offset).limit(limit)
    content = []
    for tool in tools:
        actions = await ToolAction.filter(tool_name=tool.name, version_number=tool.version, deleted=False).all()
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
                    "array": False
                } for key, value in resource_model.resources.items()
            ],
            "appActionList": [
                {
                    "id": action.id,
                    "appId": tool.id,
                    "name": action.action.get("func"),
                    "actionType": action.action.get("classify"),
                    "readonly": False if action.action.get("classify") == "write" else True,
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
                            "array": False
                        }
                        for param in action.action.get("input")
                    ],
                    "resultVariableList": [
                        {
                            "description": None,
                            "valueType": None,
                            "dataPath": None,
                            "exampleValues": None
                        }
                    ],
                    "isTest": False,
                    "resultDisplayTmptType": None,
                    "quartz": False
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
                    "actionEngineType": resource.engine
                }
                for resource in resources
            ],
            "logicContent": None,
            "requireAsset": True if resource_model.status == 1 else False,
            "actionEngineType": None
        }
        content.append(data)
    total = await Tool.filter(deleted=False).count()
    return response.json({
        "code": 0,
        "result": {
            "content": content,
            "page": page,
            "first": True if page == 1 else False,
            "last": True if page * size >= total else False,
            "size": size,
            "totalElements": total,
            "empty": False if content else True
        }
    })
