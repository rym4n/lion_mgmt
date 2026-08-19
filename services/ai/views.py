# coding=utf-8
import datetime

from sanic import Blueprint
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.chat import chat_manager, get_playbook_prompt_words, get_prompt_words
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from libs.utils import gen_run_id
from models import Account, CommandAgent, CommandTask, Message, PlaybookVersion, Room, Tool, ToolAction, ToolResourcesModel
from settings import TASK_TOPIC

ai_bp = Blueprint("ai", url_prefix="/ai")
__all__ = ["ai_bp"]


@ai_bp.post("/question")
@openapi.definition(
    summary="进行AI对话",
    description="进行AI对话",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "消息内容"},
                    "room_id": {"type": "string", "description": "作战室ID"},
                    "ai_type": {
                        "type": "string",
                        "description": "获取动作或者剧本，可选action、flow、event",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "运行成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def ai_question(request):
    room_id = request.json.get("room_id")
    content = request.json.get("content")
    ai_type = request.json.get("ai_type")
    user = request.ctx.session["account_info"]

    tenant_id = request.ctx.session.get("tenant_id")

    room = await Room.get(id=room_id, deleted=False, tenant_id=tenant_id)
    message = await Message.create(type="ai-question", sender=user, room=room, content=content, image_content="", receiver=user, tenant_id=tenant_id)  # 群聊消息设为发送者自己作为占位符

    message_data = {
        "type": "ai-question",
        "message_id": str(message.id),
        "sender_id": user.id,
        "sender_name": user.username,
        "sender_display_name": user.display_name,
        "content": content,
        "image_content": "",
        "timestamp": str(message.ctime),
        "room_id": room_id,
    }

    await chat_manager.publish_ai_message(message_data)

    # 优先派给内置「AI 运营助手」数字员工；找不到时回落到名为 AI 的剧本，
    # 避免升级过程中作战室 AI 出现空档。
    assistant = await CommandAgent.filter(
        role_key="ai-ops-assistant",
        enabled=True,
        deleted=False,
        tenant_id=tenant_id,
    ).first()

    if assistant:
        await CommandTask.create(
            title=content[:255],
            description=content,
            source="manual",
            payload={"user_query": content, "ai_type": ai_type, "room_id": room_id},
            requested_agent_id=assistant.id,
            assigned_agent=assistant,
            assigned_agent_key=assistant.agent_key,
            room=room,
            creator=user,
            tenant_id=tenant_id,
        )
        return response_ok("运行成功")

    pv = await PlaybookVersion.filter(playbook__name="AI", deleted=False, status=1, tenant_id=tenant_id).order_by("-ctime").first()
    if not pv:
        return response_ok("ai功能剧本缺失或未发布，请联系管理员处理")

    params = {
        "flow_id": pv.id,
        "flow_input": {"msg": content, "room_name": room.name, "ai_type": ai_type},
        "executor": user.id,
    }
    run_id = gen_run_id()
    msg = {"run_type": "flow", "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    await aproduce(TASK_TOPIC, msg)

    return response_ok("运行成功")


@ai_bp.post("/create-msg")
@openapi.definition(
    summary="创建AI作战室历史消息",
    description="创建AI作战室历史消息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "消息内容"},
                    "room_name": {"type": "string", "description": "作战室名称"},
                    "user_id": {"type": "string", "description": "用户ID"},
                    "type": {"type": "string", "description": "消息类型"},
                    "create": {"type": "boolean", "description": "是否创建历史消息"},
                    "eof": {"type": "boolean", "description": "是否为结尾消息"},
                    "ai_msg_id": {"type": "string", "description": "AI类型的消息id"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string"},
                                    "message_id": {"type": "string"},
                                    "sender_id": {"type": "string"},
                                    "sender_name": {"type": "string"},
                                    "sender_display_name": {"type": "string"},
                                    "content": {"type": "string"},
                                    "image_content": {"type": "string"},
                                    "timestamp": {"type": "string"},
                                    "room_id": {"type": "string"},
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
@requires_permission("warroom:r")
async def create_msg(request):
    user_id = request.json.get("user_id")
    room_name = request.json.get("room_name")
    content = request.json.get("content")
    msg_type = request.json.get("type")
    tenant_id = request.ctx.session.get("tenant_id")
    user = await Account.get(id=user_id, deleted=False)
    room = await Room.get(name=room_name, deleted=False, tenant_id=tenant_id)
    message = None
    if request.json.get("create"):
        message = await Message.create(type=msg_type, sender=user, room=room, content=content, image_content="", receiver=user, tenant_id=tenant_id)  # 群聊消息设为发送者自己作为占位符

    message_data = {
        "type": msg_type,
        "message_id": str(message.id) if message else "",
        "sender_id": user.id,
        "sender_name": user.username,
        "sender_display_name": user.display_name,
        "content": content,
        "image_content": "",
        "timestamp": (str(message.ctime) if message else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")),
        "room_id": room.id,
    }

    if request.json.get("eof"):
        message_data["eof"] = True
    else:
        message_data["eof"] = False

    if request.json.get("ai_msg_id"):
        message_data["ai_msg_id"] = request.json.get("ai_msg_id")

    return response_ok(message_data)


@ai_bp.post("/publish-msg")
@openapi.definition(
    summary="实时发送AI消息",
    description="实时发送AI消息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"data": {"type": "object", "description": "往redis里面发的ai消息"}},
            }
        }
    },
    response={
        "200": {
            "description": "发送成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "boolean"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def publish_msg(request):
    await chat_manager.publish_ai_message(request.json.get("data"))

    return response_ok(True)


@ai_bp.post("/prompt-word")
@openapi.definition(
    summary="获取提示词",
    description="获取提示词",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "prompt_word": {"type": "string", "description": "基础提示词"},
                    "ai_type": {"type": "string", "description": "提示词类型"},
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
                                    "system_prompt": {"type": "string"},
                                    "ai_dict": {"type": "object"},
                                    "ai_desc": {"type": "object"},
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
@requires_permission("warroom:r")
async def get_prompt_word(request):
    tenant_id = request.ctx.session.get("tenant_id")
    maintenance_mode = bool(
        await request.app.ctx.cache_client.get(request.ctx.session["id"])
    )

    if request.json.get("ai_type") == "action":
        system_prompt, ai_dict, ai_desc = await get_prompt_words(
            request.json.get("prompt_word"),
            tenant_id,
            maintenance_mode,
        )
    else:
        system_prompt, ai_dict, ai_desc = await get_playbook_prompt_words(
            request.json.get("prompt_word"),
            tenant_id,
            maintenance_mode,
        )

    return response_ok({"system_prompt": system_prompt, "ai_dict": ai_dict, "ai_desc": ai_desc})


@ai_bp.post("/tool-action")
@openapi.definition(
    summary="获取动作输入参数列表",
    description="获取动作输入参数列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "动作ID列表",
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
                                "type": "object",
                                "properties": {
                                    "tool_actions": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "action": {"type": "object"},
                                                "tool_name": {"type": "string"},
                                                "version_number": {"type": "string"},
                                            },
                                        },
                                    }
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
@requires_permission("warroom:r")
async def get_tool_action(request):
    tenant_id = request.ctx.session.get("tenant_id")
    maintenance_mode = bool(
        await request.app.ctx.cache_client.get(request.ctx.session["id"])
    )
    tool_actions = await ToolAction.filter(id__in=request.json.get("data"), deleted=False, tenant_id=tenant_id).all()
    data = []
    for tool_action in tool_actions:
        if not await Tool.filter(
            name=tool_action.tool_name,
            version=tool_action.version_number,
            deleted=False,
            maintain=maintenance_mode,
            tenant_id=tenant_id,
        ).exists():
            continue
        data.append(
            {
                "action": tool_action.action,
                "tool_name": tool_action.tool_name,
                "version_number": tool_action.version_number,
            }
        )
    return response_ok({"tool_actions": data})


@ai_bp.post("/tool-info")
@openapi.definition(
    summary="获取动作信息",
    description="获取动作信息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "工具名称"},
                    "version_number": {"type": "string", "description": "版本号"},
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
                                    "description": {"type": "string"},
                                    "main": {"type": "string"},
                                    "resource": {"type": "integer"},
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
@requires_permission("warroom:r")
async def get_tool_info(request):
    tenant_id = request.ctx.session.get("tenant_id")
    maintenance_mode = bool(
        await request.app.ctx.cache_client.get(request.ctx.session["id"])
    )

    tool = await Tool.get_or_none(
        name=request.json.get("tool_name"),
        version=request.json.get("version_number"),
        deleted=False,
        maintain=maintenance_mode,
        tenant_id=tenant_id,
    )
    if not tool:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    resource = await ToolResourcesModel.get_or_none(
        tool_name=request.json.get("tool_name"),
        deleted=False,
        internal=maintenance_mode,
        tenant_id=tenant_id,
    )
    return response_ok(
        {
            "description": tool.description,
            "main": tool.main,
            "resource": resource.status if resource else 0,
        }
    )
