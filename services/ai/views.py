# coding=utf-8
import asyncio

from sanic import Blueprint

from core.error_code import ErrorCode
from core.response import response_ok
from libs.chat import chat_manager
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from libs.utils import gen_order_version
from models import Tool, ToolResources
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
    """
    room_id = request.json.get("room_id")
    content = request.json.get("content")
    user = request.ctx.session['account_info']
    if not room_id or not content:
        raise CustomError(ErrorCode.ParamValueError)
    message = {
        "type": "ai",
        "content": content,
        "image_content": "",
        "room_id": room_id
    }
    await chat_manager.handle_ai_message(user, message)
    # room_id = request.json.get("room_id")
    # content = request.json.get("content")
    # tool = await Tool.filter(name="lion_mgmt", deleted=False).order_by("-ctime").first()
    # if not tool:
    #     return response_ok("ai运行APP缺失，请联系管理员处理")
    #
    # resource = await ToolResources.filter(tool_name="lion_mgmt", deleted=False).order_by("-ctime").first()
    # if not resource:
    #     raise response_ok("ai运行资源不存在，请联系管理员处理")
    #
    # user = request.ctx.session['account_info']
    # params = {"resource": resource.id, "node_name": tool.description,
    #           "action": "ai_run_action", "action_name": "ai执行动作", "app_name": "lion_mgmt",
    #           "name": tool.description, "app_ver": tool.version, "main": tool.main, "action_input": {"msg": content, "room_id": room_id},
    #           "retry_times": 0, "timeout": 300,
    #           "executor": user.id}
    # run_id = gen_order_version(length=6)
    # msg = {"run_type": "action", "run_id": run_id, "run_args": params}
    # await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)
    # 目前先用add_task 后续进行拆分使用剧本实现
    # room_id = request.json.get("room_id")
    # content = request.json.get("content")
    # user = request.ctx.session['account_info']
    # if not room_id or not content:
    #     raise CustomError(ErrorCode.ParamValueError)
    # message = {
    #     "type": "ai",
    #     "content": content,
    #     "image_content": "",
    #     "room_id": room_id
    # }
    # request.app.add_task(chat_manager.handle_ai_message(user, message))

    return response_ok("运行成功")


@ai_bp.post("/action")
@requires_permission("warroom:r")
async def ai_action(request):
    """
    ai动作

    {"type":"ai","content":"什么是智能体","image_content":"","room_id":"c6ab45be-fe5b-4f3e-b4aa-cf009fcfab2a"}

    请求参数:
        "content":"什么是智能体"      消息
        "room_id":"c6ab45be-fe5b-4f3e-b4aa-cf009fcfab2a"    作战室id
    """
    room_id = request.json.get("room_id")
    content = request.json.get("content")
    user = request.ctx.session['account_info']
    if not room_id or not content:
        raise CustomError(ErrorCode.ParamValueError)
    message = {
        "type": "ai",
        "content": content,
        "image_content": "",
        "room_id": room_id
    }
    await chat_manager.handle_ai_message(user, message)

    return response_ok("运行成功")
