# coding=utf-8
import orjson
from sanic import Blueprint

from core.response import response_ok
from libs.chat import chat_manager, get_ai_config, get_prompt_words, get_playbook_prompt_words
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from libs.utils import gen_run_id
from models import Tool, ToolResources, Account, Message, Room, ToolAction, ToolResourcesModel, PlaybookVersion
from settings import KAFKA_SERVERS, TASK_TOPIC

ai_bp = Blueprint('ai', url_prefix='/ai')
__all__ = ['ai_bp']


@ai_bp.post("/question")
@requires_permission("warroom:r")
async def ai_question(request):
    """
    进行ai对话

    {"type":"ai","content":"什么是智能体","image_content":"","room_id":"c6ab45be-fe5b-4f3e-b4aa-cf009fcfab2a"}

    请求参数:
        "content":"什么是智能体"      消息
        "room_id":"c6ab45be-fe5b-4f3e-b4aa-cf009fcfab2a"    作战室id
        "ai_type": "action"/"flow"  获取动作或者剧本
    """
    room_id = request.json.get("room_id")
    content = request.json.get("content")
    user = request.ctx.session['account_info']

    room = await Room.get(id=room_id, deleted=False)
    message = await Message.create(
        type="ai-question",
        sender=user,
        room=room,
        content=content,
        image_content="",
        receiver=user  # 群聊消息设为发送者自己作为占位符
    )

    message_data = {
        "type": "ai-question",
        "message_id": str(message.id),
        "sender_id": user.id,
        "sender_name": user.username,
        "sender_display_name": user.display_name,
        "content": content,
        "image_content": "",
        "timestamp": str(message.ctime),
        "room_id": room_id
    }

    await chat_manager.publish_ai_message(message_data)

    sender = await Account.get(username="system")

    ai_config = await get_ai_config()

    message_data["sender_id"] = sender.id
    message_data["sender_name"] = sender.username
    message_data["type"] = "ai"

    host = ai_config.get('llm_host')
    if host is None:
        message_data["content"] = "当前未配置ai服务地址，请前往系统设置进行配置。"
        await chat_manager.publish_ai_message(message_data)
        return None
    model = ai_config.get('llm_model')
    if model is None:
        message_data["content"] = "当前未配置ai服务模型，请前往系统设置进行配置。"
        await chat_manager.publish_ai_message(message_data)
        return None

    # 剧本实现
    pv = await PlaybookVersion.filter(playbook_id="222222", deleted=False).order_by("-ctime").first()
    if not pv:
        return response_ok("ai功能剧本缺失，请联系管理员处理")

    params = {"flow_id": pv.id,
              "flow_input": {
                  "msg": content,
                  "room_id": room_id,
                  "user_id": sender.id,
                  "ai_config": orjson.dumps(ai_config).decode("utf-8"),
                  "ai_type": request.json.get("ai_type", "action")
              },
              "executor": user.id}
    run_id = gen_run_id()
    msg = {"run_type": "flow", "run_id": run_id, "run_args": params}
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok("运行成功")


@ai_bp.post("/create-msg")
@requires_permission("warroom:r")
async def create_msg(request):
    """
    创建ai作战室历史消息

    请求参数:
        "content":"什么是智能体"      消息
        "room_id":"c6ab45be-fe5b-4f3e-b4aa-cf009fcfab2a"    作战室id
        "user_id": "43782755-6382-4f42-82a2-5e557370d858" 用户id
        "type": "ai-question" 消息类型
    """

    user_id = request.json.get("user_id")
    room_id = request.json.get("room_id")
    content = request.json.get("content")
    msg_type = request.json.get("type")
    user = await Account.get(id=user_id, deleted=False)
    room = await Room.get(id=room_id, deleted=False)
    message = await Message.create(
        type=msg_type,
        sender=user,
        room=room,
        content=content,
        image_content="",
        receiver=user  # 群聊消息设为发送者自己作为占位符
    )

    message_data = {
        "type": msg_type,
        "message_id": str(message.id),
        "sender_id": user.id,
        "sender_name": user.username,
        "sender_display_name": user.display_name,
        "content": content,
        "image_content": "",
        "timestamp": str(message.ctime),
        "room_id": room_id
    }

    return response_ok(message_data)


@ai_bp.post("/publish-msg")
@requires_permission("warroom:r")
async def publish_msg(request):
    """
    实时发送ai消息
    请求参数:
        "data": 往redis里面发的ai消息
    """
    await chat_manager.publish_ai_message(request.json.get("data"))

    return response_ok(True)


@ai_bp.post("/prompt-word")
@requires_permission("warroom:r")
async def get_prompt_word(request):
    """
    获取提示词
    请求参数:
        "prompt_word": 基础提示词
        "ai_type": 提示词类型
    """
    if request.json.get("ai_type") == "action":
        system_prompt, ai_dict, ai_desc = await get_prompt_words(request.json.get("prompt_word"))
    else:
        system_prompt, ai_dict, ai_desc = await get_playbook_prompt_words(request.json.get("prompt_word"))

    return response_ok({
        "system_prompt": system_prompt,
        "ai_dict": ai_dict,
        "ai_desc": ai_desc
    })


@ai_bp.post("/tool-action")
@requires_permission("warroom:r")
async def get_tool_action(request):
    """
    获取动作输入参数列表
    请求参数:
        "data": 动作id
    """
    tool_actions = await ToolAction.filter(id__in=request.json.get("data"), deleted=False).all()
    data = []
    for tool_action in tool_actions:
        data.append({
            "action": tool_action.action,
            "tool_name": tool_action.tool_name,
            "version_number": tool_action.version_number,
        })
    return response_ok({
        "tool_actions": data
    })


@ai_bp.post("/tool-info")
@requires_permission("warroom:r")
async def get_tool_info(request):
    """
    获取动作信息
    请求参数:
        "tool_name"
        "version_number"
    """
    tool = await Tool.get_or_none(name=request.json.get("tool_name"),
                                  version=request.json.get("version_number"),
                                  deleted=False)

    resource = await ToolResourcesModel.get_or_none(tool_name=request.json.get("tool_name"),
                                                    deleted=False)
    return response_ok({
        "description": tool.description,
        "main": tool.main,
        "resource": resource.status if resource else 0
    })
