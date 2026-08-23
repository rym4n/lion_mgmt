# coding=utf-8
"""知识库文档状态的定时兜底（SPEC §10.4）。

复用 services/digital_employee/memory_maintenance.py 的模式：
内部 Timed 行 + Kafka timer 契约 + lion_action 内置动作回调。

为什么需要它：用户上传后关掉页面，列表惰性刷新永远不触发，
那条记录会永远卡在 processing，界面显示的状态就是错的。
"""

import logging
from datetime import datetime

from croniter import croniter

from libs.kafka import aproduce
from models import Account, Timed
from settings import TASK_TOPIC, TZ

logger = logging.getLogger(__name__)

KNOWLEDGE_SWEEP_TIMER_END_TIME = "2099-12-31 23:59:59"
KNOWLEDGE_SWEEP_TIMER_TYPE_ACTION = 1
KNOWLEDGE_SWEEP_TIMER_STATUS_CRON = 1
KNOWLEDGE_SWEEP_APP_NAME = "lion_mgmt"
KNOWLEDGE_SWEEP_APP_VER = "v1"
KNOWLEDGE_SWEEP_MAIN = "main.py"
KNOWLEDGE_SWEEP_ACTION = "run_knowledge_status_sweep"

# 每 10 分钟一次。文档解析通常在分钟级完成，10 分钟的粒度
# 既能让界面及时反映终态，又不会给 OpenViking 造成轮询压力。
KNOWLEDGE_SWEEP_CRON = "*/10 * * * *"


def knowledge_sweep_timer_name(tenant_id: str) -> str:
    return f"knowledge_status_sweep_{tenant_id}"


def calculate_next_run_at(cron_expr: str, now: datetime | None = None) -> datetime:
    base = now or datetime.now(TZ)
    if base.tzinfo is None:
        base = TZ.localize(base)
    return croniter(cron_expr, base).get_next(datetime)


def build_sweep_timer_run_args(tenant_id: str) -> dict:
    return {
        "resource": "",
        "node_name": "knowledge_status_sweep",
        "action": KNOWLEDGE_SWEEP_ACTION,
        "action_name": KNOWLEDGE_SWEEP_ACTION,
        "app_name": KNOWLEDGE_SWEEP_APP_NAME,
        "name": "knowledge_status_sweep",
        "app_ver": KNOWLEDGE_SWEEP_APP_VER,
        "main": KNOWLEDGE_SWEEP_MAIN,
        "action_input": {"tenant_id": tenant_id, "timeout": 600},
        # 失败不重试：下一个 10 分钟窗口会自然补偿，
        # 重试只会在故障期间放大请求量
        "retry_times": 0,
        "timeout": 600,
        "executor": "system",
    }


def build_sweep_timer_message(tenant_id: str, timed: Timed, *, remove: bool = False) -> dict:
    message = {
        "run_type": "action",
        "run_args": build_sweep_timer_run_args(tenant_id),
        "timer_id": str(timed.id),
        "tenant_id": tenant_id,
    }
    if remove:
        message["timer"] = "remove"
        message["timer_rule"] = {}
    else:
        message["timer"] = "cron"
        message["timer_rule"] = {
            "end_date": getattr(timed, "end_time", None) or KNOWLEDGE_SWEEP_TIMER_END_TIME,
            "crontab": KNOWLEDGE_SWEEP_CRON,
        }
    return message


async def sync_knowledge_sweep_timer(tenant_id: str, operator: Account) -> dict:
    """为租户创建或更新兜底定时器（幂等）。"""
    timed = await Timed.filter(
        name=knowledge_sweep_timer_name(tenant_id), tenant_id=tenant_id, deleted=False
    ).first()

    run_args = build_sweep_timer_run_args(tenant_id)

    if timed:
        timed.type = KNOWLEDGE_SWEEP_TIMER_TYPE_ACTION
        timed.playbook = None
        timed.playbook_version = None
        timed.playbook_input = {}
        timed.action = None
        timed.action_input = run_args
        timed.status = KNOWLEDGE_SWEEP_TIMER_STATUS_CRON
        timed.tactics = KNOWLEDGE_SWEEP_CRON
        timed.end_time = KNOWLEDGE_SWEEP_TIMER_END_TIME
        timed.enable = 1
        timed.internal = True
        timed.updator = operator
        if not getattr(timed, "creator_id", None):
            timed.creator = operator
        await timed.save()
    else:
        timed = await Timed.create(
            name=knowledge_sweep_timer_name(tenant_id),
            type=KNOWLEDGE_SWEEP_TIMER_TYPE_ACTION,
            playbook=None,
            playbook_version=None,
            playbook_input={},
            action=None,
            action_input=run_args,
            status=KNOWLEDGE_SWEEP_TIMER_STATUS_CRON,
            tactics=KNOWLEDGE_SWEEP_CRON,
            end_time=KNOWLEDGE_SWEEP_TIMER_END_TIME,
            enable=1,
            internal=True,
            creator=operator,
            updator=operator,
            tenant_id=tenant_id,
        )

    message = build_sweep_timer_message(tenant_id, timed)
    await aproduce(TASK_TOPIC, message)
    return {"timed": timed, "message": message, "published": True}


async def execute_knowledge_sweep(tenant_id: str) -> dict:
    """由 lion_action 内置动作回调执行。"""
    from services.knowledge.status import sweep_stale

    try:
        return {"status": "ok", **await sweep_stale(tenant_id)}
    except Exception as exc:
        logger.exception("knowledge_sweep_failed tenant_id=%s", tenant_id)
        return {"status": "failed", "error": str(exc)[:2048]}
