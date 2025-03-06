# coding=utf-8

import functools
from contextlib import suppress

from sanic.log import logger
from sanic.request import Request

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.utils import get_ip
from models import Account, TwoStepSession


async def request_two_step_verification(request: Request, account: Account = None) -> TwoStepSession:
    """
    刷新二次验证的会话信息
    """
    with suppress(CustomError(ErrorCode.NotFound), CustomError(ErrorCode.JWTDecodeError)):
        two_step_session = await TwoStepSession.decode(request)
        if two_step_session.active:
            await two_step_session.deactivate()
        if not account:
            account = two_step_session.bearer
    two_step_session = await TwoStepSession.new(request, account)
    request.ctx.session = two_step_session
    return two_step_session


async def two_step_verification(request: Request) -> TwoStepSession:
    """
    二次认证
    """
    two_step_session = await TwoStepSession.decode(request)
    two_step_session.validate()
    two_step_session.bearer.validate()
    try:
        await two_step_session.check_code(request.form.get("code"))
    except CustomError(ErrorCode.MaxChallenge) as e:
        logger.warning(
            f"Client {get_ip(request)} has exceeded maximum two-step session {two_step_session.id} challenge attempts."
        )
        raise e
    logger.info(
        f"Client {get_ip(request)} has completed two-step session {two_step_session.id} challenge."
    )
    return two_step_session


def requires_two_step_verification(arg=None):
    """
    要求二次认证的装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await two_step_verification(request)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator(arg) if callable(arg) else decorator


async def verify_account(request: Request) -> TwoStepSession:
    """
    Verifies the client's account via two-step session code.

    Args:
        request (Request): Sanic request parameter. Request body should contain form-data with the following argument(s): code.

    Raises:
        NotFoundError
        JWTDecodeError
        DeletedError
        ExpiredError
        DeactivatedError
        ChallengeError
        MaxedOutChallengeError
        VerifiedError

    Returns:
         two_step_session
    """
    two_step_session = await TwoStepSession.decode(request)
    if two_step_session.bearer.verified:
        raise CustomError(ErrorCode.AccountUnverified)
    two_step_session.validate()
    try:
        await two_step_session.check_code(request.form.get("code"))
    except CustomError(ErrorCode.MaxChallenge) as e:
        logger.warning(
            f"Client {get_ip(request)} has exceeded maximum two-step session {two_step_session.id} challenge attempts "
            "during account verification."
        )
        raise e
    two_step_session.bearer.verified = True
    await two_step_session.bearer.save(update_fields=["verified"])
    logger.info(
        f"Client {get_ip(request)} has verified account {two_step_session.bearer.id}."
    )
    return two_step_session
