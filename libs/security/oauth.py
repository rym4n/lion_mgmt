# coding=utf-8

import uuid

from httpx_oauth.oauth2 import (
    BaseOAuth2,
    GetAccessTokenError,
)
from jsonpath import jsonpath
from sanic.log import logger
from tortoise.exceptions import IntegrityError, DoesNotExist

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authentication import token_encode
from models import Account


async def oauth_callback(client: BaseOAuth2, code: str, oauth_cfg: dict, code_verifier: str = None) -> tuple[dict, str]:
    try:
        token_info = await client.get_access_token(
            code,
            None,
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
                oauth_id=username[0],
                password='',
                display_name=(jsonpath(user_info, oauth_cfg.get('display_name_path')) or [None])[0],
                email=(jsonpath(user_info, oauth_cfg.get('email_path')) or [None])[0],
                phone=(jsonpath(user_info, oauth_cfg.get('phone_path')) or [None])[0],
                disabled=False,
                mfa_enable=False,
                mfa_key=None,
                creator='auto',
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
