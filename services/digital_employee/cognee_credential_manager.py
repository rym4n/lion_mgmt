# coding=utf-8
import secrets
from datetime import datetime, timedelta
from typing import Any

import httpx
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.digital_employee import TenantCogneeCredential
from settings import LION_MEMORY_ADMIN_EMAIL, LION_MEMORY_ADMIN_PASSWORD, LION_MEMORY_ENDPOINT, TZ


def _tenant_email(tenant_id: str) -> str:
    return f"{tenant_id}@aioe.internal"


def _extract_token(body: dict[str, Any]) -> str | None:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return body.get("access_token") or body.get("token") or body.get("jwt") or data.get("access_token") or data.get("token") or data.get("jwt")


def _extract_user_id(body: dict[str, Any], fallback: str) -> str:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return str(body.get("id") or body.get("user_id") or data.get("id") or data.get("user_id") or fallback)


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    last_response: httpx.Response | None = None
    for path in ("/api/v1/auth/jwt/login", "/api/v1/auth/login"):
        response = await client.post(
            f"{LION_MEMORY_ENDPOINT.rstrip('/')}{path}",
            json={"email": email, "password": password},
        )
        last_response = response
        if response.status_code < 400:
            token = _extract_token(response.json())
            if token:
                return token
            break
    if last_response is not None:
        last_response.raise_for_status()
    raise CustomError(ErrorCode.COGNEE_AUTH_FAILED)


async def _login_admin(client: httpx.AsyncClient) -> str:
    if not LION_MEMORY_ADMIN_PASSWORD:
        raise CustomError(ErrorCode.COGNEE_AUTH_FAILED)
    return await _login(client, LION_MEMORY_ADMIN_EMAIL, LION_MEMORY_ADMIN_PASSWORD)


async def _ensure_remote_user(client: httpx.AsyncClient, credential: TenantCogneeCredential) -> str:
    admin_token = await _login_admin(client)
    response = await client.post(
        f"{LION_MEMORY_ENDPOINT.rstrip('/')}/api/v1/auth/register",
        json={"email": credential.cognee_user_email, "password": credential.cognee_user_password_enc},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if response.status_code in {200, 201, 202}:
        return _extract_user_id(response.json(), credential.cognee_user_id)
    if response.status_code in {400, 409}:
        logger.info(f"Cognee 租户用户已存在或已注册: tenant_id={credential.tenant_id}, status={response.status_code}")
        return credential.cognee_user_id
    response.raise_for_status()
    return credential.cognee_user_id


async def register_tenant_user(tenant_id: str) -> TenantCogneeCredential:
    existing = await TenantCogneeCredential.get_or_none(tenant_id=tenant_id, deleted=False)
    if existing:
        return existing

    password = secrets.token_urlsafe(32)
    credential = await TenantCogneeCredential.create(
        tenant_id=tenant_id,
        cognee_user_id=tenant_id,
        cognee_user_email=_tenant_email(tenant_id),
        cognee_user_password_enc=password,
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            credential.cognee_user_id = await _ensure_remote_user(client, credential)
            await credential.save(update_fields=["cognee_user_id", "utime"])
    except CustomError:
        raise
    except httpx.HTTPError as exc:
        raise CustomError(ErrorCode.COGNEE_AUTH_FAILED) from exc
    return credential


async def get_or_refresh_jwt(tenant_id: str) -> str:
    credential = await register_tenant_user(tenant_id)
    now = datetime.now(TZ)
    if credential.cognee_jwt and credential.cognee_jwt_expires_at and credential.cognee_jwt_expires_at > now + timedelta(minutes=5):
        return credential.cognee_jwt

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                token = await _login(client, credential.cognee_user_email, credential.cognee_user_password_enc)
            except (httpx.HTTPStatusError, CustomError):
                credential.cognee_user_id = await _ensure_remote_user(client, credential)
                await credential.save(update_fields=["cognee_user_id", "utime"])
                token = await _login(client, credential.cognee_user_email, credential.cognee_user_password_enc)
    except httpx.HTTPError as exc:
        raise CustomError(ErrorCode.COGNEE_AUTH_FAILED) from exc

    credential.cognee_jwt = token
    credential.cognee_jwt_expires_at = now + timedelta(hours=1)
    await credential.save(update_fields=["cognee_jwt", "cognee_jwt_expires_at", "utime"])
    return token


async def delete_tenant_user(tenant_id: str) -> None:
    credential = await TenantCogneeCredential.get_or_none(tenant_id=tenant_id, deleted=False)
    if credential:
        credential.deleted = True
        await credential.save(update_fields=["deleted", "utime"])


async def delete_employee_memory_dataset(tenant_id: str, dataset_name: str) -> None:
    if not dataset_name:
        return
    try:
        jwt = await get_or_refresh_jwt(tenant_id)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(
                f"{LION_MEMORY_ENDPOINT.rstrip('/')}/api/v1/datasets/{dataset_name}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
        if response.status_code not in {200, 202, 204, 404}:
            response.raise_for_status()
    except Exception as exc:
        logger.warning(f"删除数字员工记忆数据集失败: tenant_id={tenant_id}, dataset_name={dataset_name}, error={exc}")
