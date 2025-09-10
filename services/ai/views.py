# coding=utf-8

from sanic import Blueprint

from core.error_code import ErrorCode
from core.response import response_ok
from libs.chat import chat_manager
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission

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
    data = await chat_manager.handle_ai_message(user, message)

    if data:
        return response_ok(data)

    return response_ok("运行成功")
