# coding=utf-8

import functools
import time
from contextlib import suppress

import jwt
from httpx_oauth.exceptions import GetIdEmailError
from httpx_oauth.oauth2 import (
    BaseOAuth2,
    RefreshTokenError,
    RevokeTokenError,
    RevokeTokenNotSupportedError,
    GetAccessTokenError,
)
from jwt import DecodeError
from sanic import Request, HTTPResponse, Sanic
from tortoise.exceptions import IntegrityError, DoesNotExist

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authentication import token_encode
from models import Account
from settings import SESSION_ENCODING_ALGORITHM, SESSION_HTTPONLY, SESSION_SAMESITE, SESSION_SECURE, SESSION_DOMAIN, AUTHENTICATION_REFRESH_EXPIRATION, SECRET, OAUTH_REDIRECT


async def oauth_callback(request: Request, client: BaseOAuth2, redirect_uri: str = OAUTH_REDIRECT, code_verifier: str = None, ) -> tuple[dict, str]:
    """
    Requests an access token using the authorization code obtained after the user has authorized the application.
    An account is retrieved if it already exists, created if it doesn't, and the user is logged in.

    Args:
        request (Request): Sanic request parameter.
        client (BaseOAuth2): OAuth provider.
        redirect_uri (str): The URL where the user was redirected after authorization.
        code_verifier (str): Optional code verifier used in the [PKCE](https://datatracker.ietf.org/doc/html/rfc7636) flow.

    Returns:
        token_info, authentication_session
    """
    try:
        token_info = await client.get_access_token(
            request.args.get("code"),
            redirect_uri,
            code_verifier,
        )
        if "expires_at" not in token_info:
            token_info["expires_at"] = time.time() + token_info["expires_in"]
        oauth_id, email = await client.get_id_email(token_info["access_token"])
        try:
            account = await Account.get(oauth_id=oauth_id)
        except DoesNotExist:
            # 不存在自动新建
            account = await Account.create(
                email=email,
                username=email.split("@")[0],
                password="",
                oauth_id=oauth_id,
                verified=True,
            )
        aioe_token = token_encode(account, oauth_id)
        return token_info, aioe_token
    except IntegrityError:
        raise CustomError(ErrorCode.CredentialsError)
    except GetAccessTokenError:
        raise CustomError(ErrorCode.GetAccessTokenError)
    except GetIdEmailError as e:
        raise CustomError(ErrorCode.GetIdEmailError)


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


async def oauth_revoke(request: Request, client: BaseOAuth2) -> dict:
    """
    Revokes the client's access token.

    Args:
        request (Request): Sanic request parameter.
        client (BaseOAuth2): OAuth provider.

    Raises:
        OAuthError
    """
    if request.cookies.get(f"aioe_oauth"):
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
        token_info = jwt.decode(request.cookies.get(f"aioe_oauth", ), SECRET, SESSION_ENCODING_ALGORITHM)
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
