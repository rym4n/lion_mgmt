# coding=utf-8
"""OpenViking embedding 配置 API。

高危配置入口：所有接口都要求 ``digital_employee:manage`` 权限。
维度固定为 2048（架构约束），只允许修改模型、供应商与 API 配置。
"""

from datetime import datetime, timezone

from sanic import Blueprint, Request
from sanic.log import logger
from sanic.response import HTTPResponse

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions
from models.embedding_config import EmbeddingConfig
from services.digital_employee.openviking import (
    restart_openviking_container,
    validate_embedding_config,
    wait_for_openviking_ready,
    write_config_to_disk,
)

embedding_bp = Blueprint(
    "digital_employee_embedding", url_prefix="/digital_employee/embedding-config"
)
__all__ = ["embedding_bp"]

EMBEDDING_MANAGE_PERMISSION = "digital_employee:manage"


def _json(request: Request) -> dict:
    return request.json or {}


def _tenant_id(request: Request) -> str:
    tenant_id = getattr(request.ctx, "session", {}).get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return tenant_id


def _operator(request: Request):
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("id")
    return getattr(account, "id", None)


def _serialize(config: EmbeddingConfig) -> dict:
    return {
        "id": str(config.id),
        "content": config.content,
        "content_hash": config.content_hash,
        "dimension": config.dimension,
        "applied_at": config.applied_at.isoformat() if config.applied_at else None,
        "applied_by": config.applied_by,
        "remark": config.remark,
    }


@embedding_bp.post("")
async def set_embedding_config(request: Request) -> HTTPResponse:
    """写入 OpenViking embedding 配置（维度固定 2048）。

    维度强制为 2048，用户只能修改模型名称、API 地址、密钥等参数。
    """
    await check_permissions(request, EMBEDDING_MANAGE_PERMISSION)

    tenant_id = _tenant_id(request)
    operator = _operator(request)
    data = _json(request)

    content = (data.get("content") or "").strip()
    remark = data.get("remark") or ""

    # 校验配置格式（强制维度 = 2048）
    dimension, content_hash = validate_embedding_config(content)

    current = await EmbeddingConfig.filter(tenant_id=tenant_id, deleted=False).first()

    if current:
        current.content = content
        current.content_hash = content_hash
        current.dimension = dimension
        current.remark = remark
        current.applied_by = operator
        await current.save()
        config = current
    else:
        config = await EmbeddingConfig.create(
            tenant_id=tenant_id,
            content=content,
            content_hash=content_hash,
            dimension=dimension,
            remark=remark,
            applied_by=operator,
        )
    config_id = str(config.id)

    # 落盘到 $OPENVIKING_CONFIG_DIR/ov.conf（原地截断写入，保持 inode）
    try:
        write_config_to_disk(content)
    except CustomError:
        raise
    except Exception as ex:
        logger.error(f"write openviking config failed: {ex}")
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_APPLY_FAILED)

    # 重启容器使配置生效（Supervisor 优先，降级 docker CLI）
    try:
        await restart_openviking_container()
    except CustomError:
        raise
    except Exception as ex:
        logger.error(f"restart openviking container failed: {ex}")
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_RESTART_FAILED)

    # 等待 /health 就绪再返回，避免调用方拿到成功响应却打在还没起来的服务上
    try:
        await wait_for_openviking_ready()
    except CustomError:
        raise
    except Exception as ex:
        logger.error(f"wait openviking ready failed: {ex}")
        raise CustomError(ErrorCode.EMBEDDING_SERVICE_NOT_READY)

    # 配置已真正生效，记录应用时间
    config.applied_at = datetime.now(timezone.utc)
    await config.save()

    return response_ok(data={"config_id": config_id, "dimension": dimension})


@embedding_bp.get("")
async def get_embedding_config(request: Request) -> HTTPResponse:
    """查询当前 embedding 配置，未配置时返回 ``None``。"""
    await check_permissions(request, EMBEDDING_MANAGE_PERMISSION)

    tenant_id = _tenant_id(request)
    config = await EmbeddingConfig.filter(tenant_id=tenant_id, deleted=False).first()
    if not config:
        return response_ok(data=None)
    return response_ok(data=_serialize(config))
