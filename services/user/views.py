# coding=utf-8

from sanic import Blueprint

from core.response import response_ok
from libs.security.authentication import login, logout, register, fulfill_second_factor
from libs.security.verification import request_two_step_verification

user_bp = Blueprint('user', url_prefix='user')
_all__ = ["user_bp"]


@user_bp.route("/add", methods=["POST"])
async def add_user(request):
    """新建用户"""
    account = await register(request)
    two_step_session = await request_two_step_verification(request, account)
    response = response_ok(account.json)
    two_step_session.encode(response)
    return response


@user_bp.route("/login", methods=["POST"])
async def on_login(request):
    """用户登录"""
    authentication_session = await login(request)
    # todo: 完善双因素验证
    # authentication_session = await login(request, require_second_factor=True)
    # two_step_session = await request_two_step_verification(request, authentication_session.bearer)
    response = response_ok(authentication_session.bearer.json)
    authentication_session.encode(response)
    # two_step_session.encode(response)
    return response


@user_bp.route("/logout", methods=["POST"])
async def on_logout(request):
    """用户登出"""
    authentication_session = await logout(request)
    # todo: oauth凭据销毁
    # token_info = await oauth_revoke(request, google_oauth)
    return response_ok(authentication_session.json)


@user_bp.route("/mfa-auth", methods=["POST"])
async def on_mfa_auth(request):
    """用户二次验证"""
    authentication_session = await fulfill_second_factor(request)
    response = response_ok(authentication_session.bearer.json)
    return response
