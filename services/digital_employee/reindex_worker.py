# coding=utf-8
"""维度变化后的 workspace 重建后台任务。

`services/digital_employee/openviking.py` 在写入新 embedding 配置、重启
lion-viking 并等待就绪之后，若检测到维度变化就调 :func:`create_reindex_task`：
它建一条 ``ReindexTask`` 并把逐个 workspace 的重建丢到后台协程，
API 立刻带着 task_id 返回，前端用 ``GET /reindex-status/<task_id>`` 轮询进度。

设计要点：
1. **单线程逐个处理**。维度变化意味着所有向量都要重算，并发打过去会把
   OpenViking / embedding 供应商打挂，进度也会失真。
2. **单个 workspace 失败不中断整体**。失败项记进 ``details``，
   任务最终以 ``failed`` 收尾（``processed`` 与 ``failed`` 计数分开），
   运维可据此只重试失败的员工。
3. **后台协程绝不向外抛异常**。asyncio 里没人 await 它，异常只会变成
   "Task exception was never retrieved" 的孤儿日志，任务行会永远停在 running。
   所以 :func:`execute_reindex` 自己兜住所有异常并落库。
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from models.command_center import CommandAgent
from models.reindex_task import ReindexTask

logger = logging.getLogger(__name__)

# details 里单条错误的截断长度，避免 JSONField 被堆栈撑爆
MAX_DETAIL_ERROR_LENGTH = 500
# 任务级 error 字段截断长度
MAX_TASK_ERROR_LENGTH = 2048
# 每个 workspace 之间的间隔（秒），给 OpenViking 留喘息空间
REINDEX_INTERVAL = 0.0

DETAIL_STATUS_SUCCESS = "success"
DETAIL_STATUS_FAILED = "failed"

# asyncio.create_task 只持弱引用：不自己留一份强引用，
# 事件循环空闲时后台任务可能被 GC 掉，表现为"进度卡在 0 且没有任何日志"。
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _now() -> datetime:
    """统一用 aware UTC，与 views_embedding.py 的 applied_at 写法保持一致。"""
    return datetime.now(timezone.utc)


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit]


async def _sync_workspace(employee: CommandAgent) -> None:
    """把单个员工的 workspace 重新推给 lion_agent。

    走 ``services/digital_employee/manager.sync_employee_workspace``——
    它是本仓调 lion_agent ``POST /internal/v1/workspaces/<id>/sync`` 的唯一入口，
    负责渲染 files 载荷（该端点的 ``files`` 是必填字段，裸调会 422）
    并回写 ``workspace_etag`` / ``workspace_synced_at``。

    ``force=True`` 是必须的：etag 只由岗位/技能/员工画像算出，
    embedding 维度变化不会改 etag，不强制就会被"内容没变"短路掉、整轮空跑。

    延迟导入避免 manager -> openviking -> reindex_worker 的环形依赖，
    同时给单测留一个便于 monkeypatch 的接缝。
    """
    from services.digital_employee.manager import sync_employee_workspace

    await sync_employee_workspace(employee, force=True)


async def _load_employees(tenant_id: str, workspace_ids: list[str]) -> dict:
    """按 id 重新捞一遍员工。

    后台协程不复用 API 请求里的 ORM 实例：调度到真正执行之间可能已隔了
    容器重启 + 就绪等待，实例早就过期了。
    """
    employees = await CommandAgent.filter(
        id__in=workspace_ids, tenant_id=tenant_id, deleted=False
    ).all()
    return {str(employee.id): employee for employee in employees}


async def execute_reindex(task_id: str, workspace_ids: list[str]) -> None:
    """逐个 workspace 执行重建，并把进度写回 ``ReindexTask``。

    本函数**不抛异常**，所有失败都落到任务行上，供前端轮询呈现。
    """
    task = await ReindexTask.filter(id=task_id, deleted=False).first()
    if task is None:
        logger.error(f"reindex task {task_id} not found, abort")
        return

    task.status = ReindexTask.STATUS_RUNNING
    task.started_at = _now()
    await task.save(update_fields=["status", "started_at", "utime"])

    details: list[dict] = []
    succeeded = 0
    failed = 0

    try:
        employees = await _load_employees(task.tenant_id, workspace_ids)

        for workspace_id in workspace_ids:
            error: Optional[str] = None
            employee = employees.get(workspace_id)
            if employee is None:
                # 调度之后被删/被移出租户，算失败但不影响其它员工
                error = "employee not found"
                logger.warning(
                    f"reindex skip workspace {workspace_id}: employee not found"
                )
            else:
                try:
                    await _sync_workspace(employee)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    error = _truncate(ex, MAX_DETAIL_ERROR_LENGTH)
                    logger.warning(
                        f"reindex workspace {workspace_id} failed: {type(ex).__name__}: {ex}"
                    )

            if error is None:
                succeeded += 1
            else:
                failed += 1
            details.append(
                {
                    "workspace_id": workspace_id,
                    "status": DETAIL_STATUS_SUCCESS if error is None else DETAIL_STATUS_FAILED,
                    "error": error,
                }
            )

            # 每完成一个就落库，前端轮询才能看到进度实时前进
            task.processed_workspaces = succeeded + failed
            task.failed_workspaces = failed
            task.details = list(details)
            await task.save(
                update_fields=[
                    "processed_workspaces",
                    "failed_workspaces",
                    "details",
                    "utime",
                ]
            )

            if REINDEX_INTERVAL:
                await asyncio.sleep(REINDEX_INTERVAL)
    except asyncio.CancelledError:
        # 进程重启/关停：把任务标成 failed，避免永远挂在 running
        await _mark_failed(task, "reindex cancelled")
        raise
    except Exception as ex:
        logger.exception(f"reindex task {task_id} aborted")
        await _mark_failed(task, _truncate(ex, MAX_TASK_ERROR_LENGTH))
        return

    task.status = (
        ReindexTask.STATUS_COMPLETED if failed == 0 else ReindexTask.STATUS_FAILED
    )
    task.completed_at = _now()
    await task.save(update_fields=["status", "completed_at", "utime"])

    logger.info(
        f"reindex task {task_id} finished: "
        f"{succeeded}/{len(workspace_ids)} succeeded, {failed} failed"
    )


async def _mark_failed(task: ReindexTask, error: str) -> None:
    task.status = ReindexTask.STATUS_FAILED
    task.error = error
    task.completed_at = _now()
    try:
        await task.save(update_fields=["status", "error", "completed_at", "utime"])
    except Exception:  # pragma: no cover - 落库都失败时只能留日志
        logger.exception(f"mark reindex task {task.id} failed did not persist")


def spawn_reindex(task_id: str, workspace_ids: list[str]) -> asyncio.Task:
    """把重建丢到后台，立即返回（不阻塞配置写入接口）。"""
    background = asyncio.create_task(execute_reindex(task_id, list(workspace_ids)))
    _BACKGROUND_TASKS.add(background)
    background.add_done_callback(_BACKGROUND_TASKS.discard)
    return background


async def create_reindex_task(config_id: str, tenant_id: str) -> str:
    """建 ReindexTask 并拉起后台重建，返回 task_id。

    员工列表与指挥中心派单口径一致（``deleted=False`` + ``enabled=True``）——
    注意是 ``enabled`` 布尔字段，不是 ``status``：``CommandAgent.status``
    的取值是 idle/busy/offline/error，没有 "enabled" 这个值。
    """
    employees = await CommandAgent.filter(
        tenant_id=tenant_id, deleted=False, enabled=True
    ).all()
    workspace_ids = [str(employee.id) for employee in employees]

    task = await ReindexTask.create(
        config_id=config_id,
        tenant_id=tenant_id,
        status=ReindexTask.STATUS_PENDING,
        total_workspaces=len(workspace_ids),
        details=[],
    )
    task_id = str(task.id)

    if not workspace_ids:
        # 没有启用中的员工，直接收尾；否则前端会一直轮询一个永不动的 pending
        now = _now()
        task.status = ReindexTask.STATUS_COMPLETED
        task.started_at = now
        task.completed_at = now
        await task.save(
            update_fields=["status", "started_at", "completed_at", "utime"]
        )
        logger.info(f"reindex task {task_id} completed immediately: no enabled employee")
        return task_id

    spawn_reindex(task_id, workspace_ids)
    logger.info(
        f"reindex task {task_id} scheduled for {len(workspace_ids)} workspaces"
    )
    return task_id
