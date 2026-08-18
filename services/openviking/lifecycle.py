# coding=utf-8
"""OpenViking 租户生命周期管理。

负责租户创建、删除、恢复时的 OpenViking account/user/key 管理。
"""

import logging
from typing import Optional

from models.openviking import TenantOpenVikingCredential
from services.openviking.client import OpenVikingAdminClient
from settings import OPENVIKING_ADMIN_BASE_URL, OPENVIKING_ROOT_API_KEY

logger = logging.getLogger(__name__)


async def provision_tenant_knowledge(tenant_id: str) -> Optional[TenantOpenVikingCredential]:
    """为租户创建知识库凭证（幂等）。

    创建 OpenViking account + user + API key，并加密存储。

    Args:
        tenant_id: 租户 ID（同时作为 OpenViking account_id）

    Returns:
        TenantOpenVikingCredential 实例，失败时返回 None

    Raises:
        Exception: OpenViking API 调用失败且非幂等错误
    """
    if not OPENVIKING_ROOT_API_KEY:
        logger.warning("OPENVIKING_ROOT_API_KEY not configured, skip knowledge provisioning")
        return None

    # 检查是否已存在凭证（幂等）
    existing = await TenantOpenVikingCredential.get_or_none(tenant_id=tenant_id, deleted=False)
    if existing:
        logger.info(f"Knowledge credential already exists for tenant {tenant_id}")
        return existing

    try:
        client = OpenVikingAdminClient(
            base_url=OPENVIKING_ADMIN_BASE_URL,
            root_api_key=OPENVIKING_ROOT_API_KEY,
        )

        # 使用 tenant_id 作为 account_id（便于对应）
        account_id = tenant_id
        user_id = "default"

        # 幂等创建 account + user + key
        api_key = await client.provision_tenant(account_id, user_id)

        # 加密存储凭证
        credential = await TenantOpenVikingCredential.create(
            tenant_id=tenant_id,
            account_id=account_id,
            user_id=user_id,
            api_key=api_key,  # EncryptedCharField 自动加密
            credential_version=1,
        )

        logger.info(f"Knowledge credential provisioned for tenant {tenant_id}")
        return credential

    except Exception as e:
        logger.error(f"Failed to provision knowledge for tenant {tenant_id}: {e}", exc_info=True)
        raise


async def deprovision_tenant_knowledge(tenant_id: str, soft_delete: bool = True) -> None:
    """租户删除时处理知识库凭证。

    软删除：只标记凭证为 deleted，保留数据
    永久清除：调用 OpenViking API 删除 account

    Args:
        tenant_id: 租户 ID
        soft_delete: True=软删除（保留数据），False=永久清除

    Raises:
        Exception: 永久清除时 OpenViking API 调用失败
    """
    credential = await TenantOpenVikingCredential.get_or_none(tenant_id=tenant_id, deleted=False)
    if not credential:
        logger.info(f"No knowledge credential found for tenant {tenant_id}")
        return

    if soft_delete:
        # 软删除：只标记 deleted，不删除 OpenViking account
        credential.deleted = True
        await credential.save()
        logger.info(f"Knowledge credential soft-deleted for tenant {tenant_id}")

        # TODO: 调用 ReMePool.invalidate_knowledge(tenant_id, reason="tenant_soft_delete")
        # 这需要在 lion_agent 侧实现后集成

    else:
        # 永久清除：删除 OpenViking account
        if not OPENVIKING_ROOT_API_KEY:
            logger.warning("OPENVIKING_ROOT_API_KEY not configured, skip account deletion")
            return

        try:
            client = OpenVikingAdminClient(
                base_url=OPENVIKING_ADMIN_BASE_URL,
                root_api_key=OPENVIKING_ROOT_API_KEY,
            )

            await client.delete_account(credential.account_id)
            logger.info(f"OpenViking account {credential.account_id} deleted")

        except Exception as e:
            logger.error(f"Failed to delete OpenViking account for tenant {tenant_id}: {e}", exc_info=True)
            raise

        finally:
            # 无论远端删除成功与否，都删除本地凭证记录
            await credential.delete()


async def restore_tenant_knowledge(tenant_id: str) -> Optional[TenantOpenVikingCredential]:
    """租户恢复时重新签发知识库凭证。

    Args:
        tenant_id: 租户 ID

    Returns:
        更新后的凭证，失败时返回 None
    """
    credential = await TenantOpenVikingCredential.get_or_none(tenant_id=tenant_id)
    if not credential:
        logger.warning(f"No credential record found for tenant {tenant_id}, creating new")
        return await provision_tenant_knowledge(tenant_id)

    if not credential.deleted:
        logger.info(f"Credential for tenant {tenant_id} is not deleted, no action needed")
        return credential

    if not OPENVIKING_ROOT_API_KEY:
        logger.warning("OPENVIKING_ROOT_API_KEY not configured, skip knowledge restore")
        return None

    try:
        client = OpenVikingAdminClient(
            base_url=OPENVIKING_ADMIN_BASE_URL,
            root_api_key=OPENVIKING_ROOT_API_KEY,
        )

        # 重新签发 key（account 和 user 在软删时未删除）
        new_api_key = await client.create_api_key(credential.account_id, credential.user_id)

        # 更新凭证：递增版本号（关键：不递增会导致缓存命中旧 client）
        credential.api_key = new_api_key
        credential.credential_version += 1
        credential.deleted = False
        await credential.save()

        logger.info(f"Knowledge credential restored for tenant {tenant_id}, version={credential.credential_version}")

        # TODO: 调用 ReMePool.invalidate_knowledge(tenant_id, reason="tenant_restore")

        return credential

    except Exception as e:
        logger.error(f"Failed to restore knowledge for tenant {tenant_id}: {e}", exc_info=True)
        raise


async def rotate_tenant_knowledge_key(tenant_id: str) -> Optional[TenantOpenVikingCredential]:
    """轮换租户知识库 API key。

    Args:
        tenant_id: 租户 ID

    Returns:
        更新后的凭证，失败时返回 None
    """
    credential = await TenantOpenVikingCredential.get_or_none(tenant_id=tenant_id, deleted=False)
    if not credential:
        logger.warning(f"No active credential found for tenant {tenant_id}")
        return None

    if not OPENVIKING_ROOT_API_KEY:
        logger.warning("OPENVIKING_ROOT_API_KEY not configured, skip key rotation")
        return None

    try:
        client = OpenVikingAdminClient(
            base_url=OPENVIKING_ADMIN_BASE_URL,
            root_api_key=OPENVIKING_ROOT_API_KEY,
        )

        # 签发新 key
        new_api_key = await client.create_api_key(credential.account_id, credential.user_id)

        # 更新凭证：递增版本号（关键）
        credential.api_key = new_api_key
        credential.credential_version += 1
        await credential.save()

        logger.info(f"Knowledge key rotated for tenant {tenant_id}, version={credential.credential_version}")

        # TODO: 调用 ReMePool.invalidate_knowledge(tenant_id, reason="key_rotation")

        return credential

    except Exception as e:
        logger.error(f"Failed to rotate knowledge key for tenant {tenant_id}: {e}", exc_info=True)
        raise
