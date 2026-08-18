# coding=utf-8
"""ReMe 记忆维护调度（阶段二 Task #12）。

按租户定时触发 auto_dream + optimize_index，复用 services/agent/schedule_runner.py
的既有模式（内部 Timed 行 + timer Kafka 契约）。
"""

import logging
from datetime import datetime

from croniter import croniter

from libs.kafka import aproduce
from models import Account, Timed
from models.command_center import CommandAgent
from services.llm_config.resolver import resolve_llm
from settings import TASK_TOPIC, TZ

logger = logging.getLogger(__name__)

# 维护定时器配置（复用 schedule_runner 模式）
MEMORY_MAINTENANCE_TIMER_END_TIME = "2099-12-31 23:59:59"
MEMORY_MAINTENANCE_TIMER_TYPE_ACTION = 1
MEMORY_MAINTENANCE_TIMER_STATUS_CRON = 1
MEMORY_MAINTENANCE_APP_NAME = "lion_mgmt"
MEMORY_MAINTENANCE_APP_VER = "v1"
MEMORY_MAINTENANCE_MAIN = "main.py"
MEMORY_MAINTENANCE_ACTION = "run_memory_maintenance"

# 默认 cron：每天凌晨 3 点（避开 ReMe 原 cron 的 23:00/02:00 与业务高峰）
DEFAULT_MEMORY_MAINTENANCE_CRON = "0 3 * * *"


def memory_maintenance_timer_name(tenant_id: str) -> str:
    """每个租户一个定时器。"""
    return f"memory_maintenance_{tenant_id}"


def calculate_next_run_at(cron_expr: str, now: datetime | None = None) -> datetime:
    base = now or datetime.now(TZ)
    if base.tzinfo is None:
        base = TZ.localize(base)
    return croniter(cron_expr, base).get_next(datetime)


def build_memory_maintenance_timer_run_args(tenant_id: str) -> dict:
    """构造传给 lion_action 内置动作的参数。"""
    return {
        "resource": "",
        "node_name": "memory_maintenance",
        "action": MEMORY_MAINTENANCE_ACTION,
        "action_name": MEMORY_MAINTENANCE_ACTION,
        "app_name": MEMORY_MAINTENANCE_APP_NAME,
        "name": "memory_maintenance",
        "app_ver": MEMORY_MAINTENANCE_APP_VER,
        "main": MEMORY_MAINTENANCE_MAIN,
        "action_input": {
            "tenant_id": tenant_id,
            "timeout": 3600,  # 维护窗口 1 小时
        },
        "retry_times": 0,  # 维护失败不自动重试，由下一个窗口补偿
        "timeout": 3600,
        "executor": "system",
    }


def build_memory_maintenance_timer_message(
    tenant_id: str,
    timed: Timed,
    *,
    remove: bool = False,
) -> dict:
    """构造 Kafka timer 消息。"""
    message = {
        "run_type": "action",
        "run_args": build_memory_maintenance_timer_run_args(tenant_id),
        "timer_id": str(timed.id),
        "tenant_id": tenant_id,
    }
    if remove:
        message["timer"] = "remove"
        message["timer_rule"] = {}
    else:
        message["timer"] = "cron"
        message["timer_rule"] = {
            "end_date": getattr(timed, "end_time", None) or MEMORY_MAINTENANCE_TIMER_END_TIME,
            "crontab": DEFAULT_MEMORY_MAINTENANCE_CRON,
        }
    return message


async def sync_memory_maintenance_timer(tenant_id: str, operator: Account) -> dict:
    """为租户创建或更新维护定时器。"""
    timed = await Timed.filter(
        name=memory_maintenance_timer_name(tenant_id),
        tenant_id=tenant_id,
        deleted=False,
    ).first()

    run_args = build_memory_maintenance_timer_run_args(tenant_id)
    enable = 1

    if timed:
        timed.type = MEMORY_MAINTENANCE_TIMER_TYPE_ACTION
        timed.playbook = None
        timed.playbook_version = None
        timed.playbook_input = {}
        timed.action = None
        timed.action_input = run_args
        timed.status = MEMORY_MAINTENANCE_TIMER_STATUS_CRON
        timed.tactics = DEFAULT_MEMORY_MAINTENANCE_CRON
        timed.end_time = MEMORY_MAINTENANCE_TIMER_END_TIME
        timed.enable = enable
        timed.internal = True
        timed.updator = operator
        if not getattr(timed, "creator_id", None):
            timed.creator = operator
        await timed.save()
    else:
        timed = await Timed.create(
            name=memory_maintenance_timer_name(tenant_id),
            type=MEMORY_MAINTENANCE_TIMER_TYPE_ACTION,
            playbook=None,
            playbook_version=None,
            playbook_input={},
            action=None,
            action_input=run_args,
            status=MEMORY_MAINTENANCE_TIMER_STATUS_CRON,
            tactics=DEFAULT_MEMORY_MAINTENANCE_CRON,
            end_time=MEMORY_MAINTENANCE_TIMER_END_TIME,
            enable=enable,
            internal=True,
            creator=operator,
            updator=operator,
            tenant_id=tenant_id,
        )

    message = build_memory_maintenance_timer_message(tenant_id, timed)
    await aproduce(TASK_TOPIC, message)

    return {
        "timed": timed,
        "message": message,
        "published": True,
    }


async def execute_memory_maintenance(tenant_id: str) -> dict:
    """执行一次记忆维护（由 lion_action 内置动作回调）。

    按租户循环：
    1. 查询该租户所有启用的数字员工
    2. 解析每个员工的 LLM 配置
    3. 调用 lion_agent 的 /internal/v1/memory/maintain
    """
    from services.digital_employee.lion_agent_adapter import LionAgentClient

    # 查询该租户的所有启用员工
    employees = await CommandAgent.filter(
        tenant_id=tenant_id,
        deleted=False,
        status="enabled",
    ).all()

    if not employees:
        return {"status": "skipped", "reason": "no_enabled_employees"}

    # 构造 workspaces 列表
    workspaces = []
    for emp in employees:
        try:
            # 解析员工的 LLM 配置
            llm_config = await resolve_llm(tenant_id, config_id=emp.llm_config_id)
            workspaces.append({
                "employee_id": str(emp.id),
                "llm": {
                    "base_url": llm_config.base_url,
                    "api_key": llm_config.api_key,
                    "model": llm_config.model_name,
                },
            })
        except Exception as exc:
            logger.warning(
                "memory_maintenance_skip_employee tenant_id=%s employee_id=%s reason=%s",
                tenant_id, emp.id, type(exc).__name__,
            )
            continue

    if not workspaces:
        return {"status": "skipped", "reason": "no_valid_llm_configs"}

    # 调用 lion_agent 维护端点
    client = LionAgentClient()
    try:
        result = await client.maintain_memory(
            tenant_id=tenant_id,
            jobs=["update_index", "auto_dream", "optimize_index"],
            workspaces=workspaces,
            concurrency=1,
        )
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("memory_maintenance_failed tenant_id=%s", tenant_id)
        return {"status": "failed", "error": str(exc)[:2048]}
