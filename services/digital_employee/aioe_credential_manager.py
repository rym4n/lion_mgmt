# coding=utf-8
"""数字员工 ↔ AIOE 平台 API 账号 的凭证管理。

每个数字员工在创建时派生一个 type=api 的 Account，绑定员工的 RBAC 角色，
签发长期有效的 aioe-auth token（account_id:jwt）。token 由 lion_agent 的
aioe-cli 工具持有，用来回调 AIOE OpenAPI（如 /playbook/debug 触发应用动作）。

设计约定：
- token 长期有效（10 年），不做缓存过期/刷新——员工生命周期即账号生命周期。
- 鉴权与权限完全复用平台既有机制：JWT 用账号自身 password 签名，权限由绑定的
  RBAC Role 决定。prompt/岗位元数据不作为授权来源。
- 历史已存在的员工不自动补建，靠重新导入触发 create_employee。
"""
import time
import uuid
from datetime import datetime

from aiocache import caches
from sanic.log import logger
from tortoise.transactions import in_transaction

from libs.security.authentication import token_encode
from libs.utils import password_hasher
from models.digital_employee import EmployeeApiCredential
from models.user import Account, Role
from settings import TZ

# API 账号 token 有效期：10 年（视为长期有效）
API_TOKEN_EXPIRATION_SECONDS = 60 * 60 * 24 * 365 * 10


def _cache_client():
    return caches.get("redis_mgmt")


def _api_username(employee) -> str:
    # 账号用户名全局唯一，用员工 ID 派生，避免与人类用户/其它员工冲突。
    return f"digital-employee-{employee.id}"


async def _bind_rbac_role(account: Account, rbac_role_id: str | None, tenant_id: str) -> None:
    if not rbac_role_id:
        return
    role = await Role.get_or_none(id=rbac_role_id, tenant_id=tenant_id, deleted=False)
    if role:
        await account.roles.add(role)
    else:
        logger.warning(f"数字员工 API 账号未找到 RBAC 角色: rbac_role_id={rbac_role_id}, tenant_id={tenant_id}")


async def _sync_rbac_role(account: Account, rbac_role_id: str | None, tenant_id: str) -> None:
    if not rbac_role_id:
        return
    await account.fetch_related("roles")
    if any(str(role.id) == str(rbac_role_id) for role in account.roles or []):
        return
    await _bind_rbac_role(account, rbac_role_id, tenant_id)


def _encode_api_token(account: Account) -> str:
    token = token_encode(
        account.id,
        account.username,
        account.display_name,
        account.mfa_enable,
        account.tenant_id,
        str(uuid.uuid4()),
        API_TOKEN_EXPIRATION_SECONDS,
        account.password,
        permit_ip=account.permit_ip,
    )
    return f"{account.id}:{token}"


async def _warm_account_cache(account: Account) -> None:
    try:
        await account.fetch_related("roles")
        await _cache_client().set(f"account_{account.id}", account, 7 * 24 * 3600)
    except Exception as exc:
        logger.warning(f"数字员工 API 账号缓存预热失败(不影响建号): account_id={account.id}, error={exc}")


async def create_employee_api_account(employee) -> EmployeeApiCredential:
    """为数字员工创建/复用一个 type=api 的 AIOE 账号，签发长期 token。

    幂等：已存在凭证则直接返回，不重复建号。
    """
    existing = await EmployeeApiCredential.get_or_none(employee_id=employee.id, deleted=False)
    if existing:
        account = await Account.get_or_none(id=existing.account_id, deleted=False)
        if account:
            await _sync_rbac_role(account, employee.rbac_role_id, employee.tenant_id)
            existing.api_token = _encode_api_token(account)
            await existing.save(update_fields=["api_token", "utime"])
            await _warm_account_cache(account)
        return existing

    username = _api_username(employee)
    async with in_transaction():
        # 账号也做幂等：复用同名 type=api 账号（如历史残留），避免唯一约束冲突。
        account = await Account.get_or_none(username=username, deleted=False)
        if account is None:
            account = await Account.create(
                username=username,
                password=password_hasher.hash(str(uuid.uuid4())),
                type="api",
                display_name=f"数字员工 {employee.name} 的 API 账号",
                expiration=datetime.fromtimestamp(int(time.time()) + API_TOKEN_EXPIRATION_SECONDS, TZ),
                creator="digital_employee",
                permit_ip=None,
                tenant_id=employee.tenant_id,
            )
        await _sync_rbac_role(account, employee.rbac_role_id, employee.tenant_id)

        credential = await EmployeeApiCredential.create(
            tenant_id=employee.tenant_id,
            employee_id=employee.id,
            account_id=account.id,
            api_token=_encode_api_token(account),
        )

    # 预热账号缓存，供鉴权中间件命中；best-effort——缓存不可用不应阻断建号，
    # 鉴权中间件在 cache miss 时会从 DB 自愈回填（见 authentication.token_decode）。
    await _warm_account_cache(account)

    return credential


async def get_employee_api_token(employee) -> str | None:
    """读取员工的长期 API token；不存在则按需补建（兼容历史员工重新导入前的执行）。"""
    credential = await create_employee_api_account(employee)
    return credential.api_token


async def delete_employee_api_account(employee) -> None:
    """员工删除时清理其 API 账号与凭证。"""
    credential = await EmployeeApiCredential.get_or_none(employee_id=employee.id, deleted=False)
    if not credential:
        return
    try:
        account = await Account.get_or_none(id=credential.account_id, deleted=False)
        if account:
            account.deleted = True
            await account.save(update_fields=["deleted", "utime"])
        await _cache_client().delete(f"account_{credential.account_id}")
    except Exception as exc:
        logger.warning(f"删除数字员工 API 账号失败: employee_id={employee.id}, error={exc}")
    finally:
        credential.deleted = True
        await credential.save(update_fields=["deleted", "utime"])
