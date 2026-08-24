# coding=utf-8
"""OpenViking embedding 配置 API。

高危配置入口：维度变更会清空所有数字员工的向量记忆，
因此所有接口都要求 ``digital_employee:manage`` 权限，且维度变更需二次确认。
"""

from datetime import datetime, timezone
import uuid

from sanic import Blueprint, Request
from sanic.log import logger
from sanic.response import HTTPResponse

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions
from models.embedding_config import EmbeddingConfig
from models.reindex_task import ReindexTask
from services.digital_employee.openviking import (
    restart_openviking_container,
    schedule_reindex,
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
    """写入 OpenViking embedding 配置。

    维度未变化时只换模型/供应商，向量仍可用；
    维度变化时必须携带 ``confirm_dimension_change=true``，
    并在容器重启后触发全员工 reindex。
    """
    await check_permissions(request, EMBEDDING_MANAGE_PERMISSION)

    tenant_id = _tenant_id(request)
    operator = _operator(request)
    data = _json(request)

    content = (data.get("content") or "").strip()
    remark = data.get("remark") or ""

    # 校验配置格式，拿到维度与内容哈希
    dimension, content_hash = validate_embedding_config(content)

    current = await EmbeddingConfig.filter(tenant_id=tenant_id, deleted=False).first()

    # 维度变化需要前端二次确认（会清空所有员工向量记忆）
    dimension_changed = bool(current and current.dimension != dimension)
    if dimension_changed and not data.get("confirm_dimension_change", False):
        raise CustomError(ErrorCode.EMBEDDING_DIMENSION_CHANGE_UNCONFIRMED)

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

    # 落盘（Task 3 实现）
    try:
        write_config_to_disk(content)
    except CustomError:
        raise
    except Exception as ex:
        logger.error(f"write openviking config failed: {ex}")
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_APPLY_FAILED)

    # 重启容器使配置生效（Task 3 实现）
    try:
        await restart_openviking_container()
    except CustomError:
        raise
    except Exception as ex:
        logger.error(f"restart openviking container failed: {ex}")
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_RESTART_FAILED)

    # 等待容器就绪，避免 reindex 打在还没起来的服务上
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

    result = {"config_id": config_id, "dimension_changed": dimension_changed}

    # 维度变化时创建 reindex 任务：只创建 + 拉起后台协程，立即返回 task_id，
    # 真正的逐个 workspace 重建由 reindex_worker 在后台跑，不阻塞本次响应。
    if dimension_changed:
        try:
            task_id = await schedule_reindex(config_id, tenant_id)
        except CustomError:
            raise
        except Exception as ex:
            logger.error(f"schedule reindex failed: {ex}")
            raise CustomError(ErrorCode.EMBEDDING_REINDEX_SCHEDULE_FAILED)
        result["reindex_task_id"] = str(task_id) if task_id else None

    return response_ok(data=result)


@embedding_bp.get("")
async def get_embedding_config(request: Request) -> HTTPResponse:
    """查询当前 embedding 配置，未配置时返回 ``None``。"""
    await check_permissions(request, EMBEDDING_MANAGE_PERMISSION)

    tenant_id = _tenant_id(request)
    config = await EmbeddingConfig.filter(tenant_id=tenant_id, deleted=False).first()
    if not config:
        return response_ok(data=None)
    return response_ok(data=_serialize(config))


@embedding_bp.get("/reindex-status/<task_id:str>")
async def get_reindex_status(request: Request, task_id: str) -> HTTPResponse:
    """查询 reindex 任务进度，供前端轮询。"""
    await check_permissions(request, EMBEDDING_MANAGE_PERMISSION)

    tenant_id = _tenant_id(request)

    # id 是 UUID 主键，非法串会在 ORM 层抛 ValueError 变成 400；
    # 这是个高频轮询接口，直接按"任务不存在"返回语义更清楚。
    try:
        uuid.UUID(str(task_id))
    except (ValueError, AttributeError, TypeError):
        raise CustomError(ErrorCode.EMBEDDING_REINDEX_TASK_NOT_FOUND)

    task = await ReindexTask.filter(
        id=task_id, tenant_id=tenant_id, deleted=False
    ).first()
    if not task:
        raise CustomError(ErrorCode.EMBEDDING_REINDEX_TASK_NOT_FOUND)

    return response_ok(
        data={
            "id": str(task.id),
            "status": task.status,
            "total_workspaces": task.total_workspaces,
            "processed_workspaces": task.processed_workspaces,
            "failed_workspaces": task.failed_workspaces,
            "details": task.details,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "error": task.error,
        }
    )
