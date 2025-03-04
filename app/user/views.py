# coding=utf-8


from sanic import Request, HTTPResponse, Blueprint
from tortoise.queryset import QuerySet

from app.models import User
from core.response import response_ok
from sanic.log import logger

user_bp = Blueprint('user', url_prefix='user')
_all__ = ["user_bp"]


@user_bp.route("/list", methods=["GET"])
async def user_list(request: Request) -> HTTPResponse:
    """用户列表"""
    logger.info(request.json)
    page: int = 1
    per_page: int = 5
    queryset: QuerySet = User.filter().order_by("-ctime", "-id").offset((page - 1) * per_page).limit(per_page)
    result: list = await queryset
    data = [{k: v for k, v in vars(obj).items() if not k.startswith("_")} for obj in result]
    return response_ok(data)
