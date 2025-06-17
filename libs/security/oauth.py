# coding=utf-8

import functools
import time
import uuid
from contextlib import suppress

import jwt
from httpx_oauth.oauth2 import (
    BaseOAuth2,
    RefreshTokenError,
    RevokeTokenError,
    RevokeTokenNotSupportedError,
    GetAccessTokenError,
)
from jsonpath import jsonpath
from jwt import DecodeError
from sanic import Request, HTTPResponse, Sanic
from sanic.log import logger
from tortoise.exceptions import IntegrityError, DoesNotExist

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authentication import token_encode
from models import Account
from settings import SESSION_ENCODING_ALGORITHM, SESSION_HTTPONLY, SESSION_SAMESITE, SESSION_SECURE, SESSION_DOMAIN, AUTHENTICATION_REFRESH_EXPIRATION, SECRET


async def oauth_callback(client: BaseOAuth2, code: str, redirect_uri: str, oauth_cfg: dict, code_verifier: str = None) -> tuple[dict, str]:
    try:
        token_info = await client.get_access_token(
            code,
            redirect_uri,
            code_verifier,
        )
        logger.info(f"token_info: {token_info}")
        access_token = jsonpath(token_info, oauth_cfg['token_path'])
        if not access_token:
            raise GetAccessTokenError
        user_info = await get_user_info(client, access_token[0], oauth_cfg['user_info_uri'])
        logger.info(f"user_info: {user_info}")
        username = jsonpath(user_info, oauth_cfg['user_id_path'])
        if not username:
            raise CustomError(ErrorCode.GetUserInfoError)
        try:
            account = await Account.get(username=username[0]).prefetch_related("roles")
        except DoesNotExist:
            if oauth_cfg['auto_create'] != 'true':
                raise CustomError(ErrorCode.AccountNotFound)
            # 不存在自动新建
            account = await Account.create(
                username=username[0],
                password='',
                display_name=(jsonpath(user_info, oauth_cfg.get('display_name_path')) or [None])[0],
                email=(jsonpath(user_info, oauth_cfg.get('email_path')) or [None])[0],
                phone=(jsonpath(user_info, oauth_cfg.get('phone_path')) or [None])[0],
                disabled=False,
                mfa_enable=False,
                mfa_key=None,
                permit_ip=None
            )
            await account.fetch_related("roles")
        aioe_token = token_encode(account.id, account.username, account.display_name, account.mfa_enable, str(uuid.uuid4()))
        return account.json, aioe_token
    except IntegrityError:
        raise CustomError(ErrorCode.CredentialsError)
    except GetAccessTokenError:
        raise CustomError(ErrorCode.GetAccessTokenError)


async def get_user_info(client, token, user_info_uri):
    async with client.get_httpx_client() as c:
        resp = await c.get(user_info_uri, headers={"Authorization": f"Bearer {token}"})
        return resp.json()


def oauth_encode(response: HTTPResponse, token_info: dict) -> None:
    """
    Transforms access token into JWT and then is stored in a cookie.

    Args:
        response (HTTPResponse): Sanic response used to store JWT into a cookie on the client.
        token_info (dict): OAuth access token.
    """
    response.cookies.add_cookie(
        f"aioe_oauth",
        str(
            jwt.encode(
                token_info,
                SECRET,
                SESSION_ENCODING_ALGORITHM,
            ),
        ),
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        domain=SESSION_DOMAIN,
        max_age=token_info["expires_in"] + AUTHENTICATION_REFRESH_EXPIRATION,
    )


# noinspection PyTypeChecker
async def oauth_revoke(request: Request, client: BaseOAuth2) -> dict:
    """
    Revokes the client's access token.

    Args:
        request (Request): Sanic request parameter.
        client (BaseOAuth2): OAuth provider.

    Raises:
        OAuthError
    """
    if request.headers.get(f"aioe-oauth"):
        try:
            token_info = await oauth_decode(request, client, False)
            request.ctx.oauth["revoked"] = True
            with suppress(RevokeTokenNotSupportedError):
                await client.revoke_token(
                    token_info.get("access_token"), "access_token"
                )
            return token_info
        except RevokeTokenError as e:
            raise CustomError(ErrorCode.RevokeTokenError)


async def oauth_decode(request: Request, client: BaseOAuth2, refresh=True) -> dict:
    """
    Decodes JWT token from client cookie into an access token.

    Args:
        request (Request): Sanic request parameter.
        client (BaseOAuth2): OAuth provider.
        refresh (bool): Determines that the decoded access token is refreshed during expiration.

    Raises:
        OAuthError

    Returns:
        token_info
    """
    try:
        token_info = jwt.decode(request.headers.get(f"aioe-oauth"), SECRET, SESSION_ENCODING_ALGORITHM)
        if refresh and time.time() > token_info["expires_at"]:
            token_info = await client.refresh_token(token_info.get("refresh_token"))
            token_info["is_refresh"] = True
            if "expires_at" not in token_info:
                token_info["expires_at"] = time.time() + token_info["expires_in"]
        request.ctx.oauth = token_info
        return token_info
    except RefreshTokenError as e:
        raise CustomError(ErrorCode.RefreshTokenError)
    except DecodeError:
        raise CustomError(ErrorCode.SessionInvalid)


def requires_oauth(client: BaseOAuth2):
    """
    Decodes JWT token from client cookie into an access token.

    Args:
        client (BaseOAuth2): OAuth provider.

    Example:
        This method is not called directly and instead used as a decorator:

            @app.get('api/oauth')
            @requires_oauth
            async def on_oauth(request):
                return text('Access token retrieved!')

    Raises:
        OAuthError
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await oauth_decode(request, client)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def initialize_oauth(app: Sanic) -> None:
    """
    Attaches session middleware.

    Args:
        app (Sanic): Sanic application instance.
    """

    @app.on_response
    async def session_middleware(request, response):
        if hasattr(request.ctx, "oauth"):
            if request.ctx.oauth.get("is_refresh"):
                oauth_encode(response, request.ctx.oauth)
            elif request.ctx.oauth.get("revoked"):
                response.delete_cookie(f"aioe_oauth")
