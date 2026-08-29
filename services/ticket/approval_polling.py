# coding=utf-8
"""审批轮询定时任务管理。

审批节点激活时，如果配置了 polling，创建一个 cron 定时任务（Timed 表 + Kafka）。
审批完成或节点异常时，删除对应的定时任务。

定时任务可见性：用户在"定时任务"页面能看到审批轮询任务及其执行历史，不是黑盒。
"""

from __future__ import annotations

from sanic.log import logger

from libs.kafka import aproduce
from models import Timed
from models.ticket import TicketNodeInstance
from settings import TASK_TOPIC


POLL_TIMER_INTERNAL = True  # 内部定时任务，不归属任何剧本


def _poll_timer_name(ticket_no: str, node_id: str) -> str:
    return f"审批轮询：{ticket_no}/{node_id}"


def _interval_to_cron(minutes: int) -> str:
    """将分钟间隔转成 crontab 表达式（最小 1 分钟，最大 60 分钟）。"""
    minutes = max(1, min(60, minutes))
    if minutes < 60:
        return f"*/{minutes} * * * *"
    return "0 * * * *"  # 每小时


# --- PLACEHOLDER: more content appended below ---


def _build_poll_run_args(polling: dict, ticket, node_def: dict) -> dict:
    """构建轮询动作的执行参数。"""
    return {
        "app_name": polling.get("app_name", ""),
        "name": polling.get("action_name", ""),
        "app_ver": polling.get("app_ver", ""),
        "main": polling.get("main", ""),
        "action_input": polling.get("action_input") or {},
        "resource": polling.get("resource", ""),
        "node_name": f"审批轮询-{node_def.get('name', '')}",
        "action_name": polling.get("action_name", ""),
        "action": polling.get("action_func", ""),
        "executor": "",
        "retry_times": 0,
        "timeout": min(int(polling.get("interval", 60)), 30),
    }


async def create_poll_timer(
    ticket,
    node_instance: TicketNodeInstance,
    node_def: dict,
    operator=None,
) -> Timed | None:
    """为审批节点创建轮询定时任务。

    返回创建的 Timed 记录，或 None（未启用轮询时）。
    轮询间隔 1~60 分钟，到期时间 1~24 小时（强制上限）。
    """
    from datetime import datetime, timedelta
    from settings import TZ

    props = node_def.get("properties") or {}
    polling = props.get("polling") or {}
    if not polling.get("enabled"):
        return None

    action_id = polling.get("action_id")
    if not action_id:
        return None

    interval_minutes = max(1, min(60, int(polling.get("interval_minutes", 5))))
    expiry_hours = max(1, min(24, int(polling.get("expiry_hours", 4))))
    end_time = (datetime.now(TZ) + timedelta(hours=expiry_hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    ticket_no = getattr(ticket, "ticket_no", "") or str(ticket.id)
    name = _poll_timer_name(ticket_no, node_instance.node_id)
    cron_expr = _interval_to_cron(interval_minutes)
    run_args = _build_poll_run_args(polling, ticket, node_def)

    # 写 Timed 表，让用户在定时任务页面看到
    timed = await Timed.create(
        name=name,
        type=1,  # 1=动作
        playbook=None,
        playbook_version=None,
        playbook_input={},
        action=None,
        action_input=run_args,
        status=1,  # 1=cron
        tactics=cron_expr,
        end_time=end_time,
        enable=1,
        internal=POLL_TIMER_INTERNAL,
        creator=operator,
        updator=operator,
        tenant_id=ticket.tenant_id,
    )

    # 投 Kafka 启动定时任务
    await aproduce(
        TASK_TOPIC,
        {
            "run_type": "action",
            "run_args": run_args,
            "timer_id": str(timed.id),
            "timer": "cron",
            "timer_rule": {"crontab": cron_expr, "end_date": end_time},
            "tenant_id": ticket.tenant_id,
        },
    )

    # 把 timed_id 记到节点 form_data 上，方便后续删除
    form_data = dict(node_instance.form_data or {})
    form_data["_poll_timed_id"] = str(timed.id)
    node_instance.form_data = form_data
    await node_instance.save(update_fields=["form_data", "utime"])

    logger.info(
        "创建审批轮询定时任务: ticket_no=%s node_id=%s timed_id=%s cron=%s",
        ticket_no,
        node_instance.node_id,
        timed.id,
        cron_expr,
    )
    return timed


async def remove_poll_timer(node_instance: TicketNodeInstance) -> bool:
    """审批完成或节点异常时，停止并删除轮询定时任务。"""
    form_data = dict(node_instance.form_data or {})
    timed_id = form_data.get("_poll_timed_id")
    if not timed_id:
        return False

    timed = await Timed.filter(id=timed_id, deleted=False).first()
    if timed is None:
        return False

    # 发 remove 消息让 lion_action 停掉定时调度
    await aproduce(
        TASK_TOPIC,
        {
            "run_type": "action",
            "run_args": timed.action_input or {},
            "timer_id": str(timed.id),
            "timer": "remove",
            "timer_rule": {},
            "tenant_id": timed.tenant_id,
        },
    )

    # 软删除 Timed 记录
    timed.enable = 0
    timed.deleted = True
    await timed.save(update_fields=["enable", "deleted", "utime"])

    logger.info(
        "删除审批轮询定时任务: timed_id=%s node_id=%s",
        timed_id,
        node_instance.node_id,
    )
    return True
