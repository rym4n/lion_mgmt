# coding=utf-8
"""自动节点动作派发。范式照 services/large_screen/action_executor.py:101-131：
投 Kafka TASK_TOPIC（run_type=action）+ 轮询 Runlog。

本计划只提供派发与同步等待两个入口，供引擎与动态审批人调用；
出参写回表单字段等能力在计划 2 补齐。
"""

from libs.kafka import aproduce
from libs.utils import gen_run_id
from settings import TASK_TOPIC


def _build_params(action: dict) -> dict:
    return {
        "resource": action.get("resource", ""),
        "node_name": action.get("node_name") or action.get("name", ""),
        "action": action.get("action") or action.get("func", ""),
        "action_name": action.get("action_name") or action.get("name", ""),
        "app_name": action.get("app_name") or action.get("tool_name", ""),
        "app_ver": action.get("app_ver") or action.get("version", ""),
        "main": action.get("main") or action.get("tool_main", ""),
        "action_input": action.get("action_input") or {},
        "retry_times": int(action.get("retry_times") or 3),
        "timeout": int(action.get("timeout") or 300),
    }


async def dispatch_action(ticket, action: dict) -> str:
    """投递动作并立即返回 run_id。结果由 sla_scheduler 的 sweep 取回。"""
    run_id = gen_run_id()
    await aproduce(TASK_TOPIC, {
        "run_type": "action",
        "run_id": run_id,
        "run_args": _build_params(action),
        "tenant_id": ticket.tenant_id,
    })
    return run_id


async def run_action_sync(ticket, action: dict, timeout: int = 30) -> dict:
    """同步执行并等结果，供动态审批人解析用（SPEC §4.4，上限 30 秒）。"""
    import asyncio

    from models import Runlog

    run_id = await dispatch_action(ticket, action)
    for _ in range(max(1, timeout)):
        log = await Runlog.filter(
            run_id=run_id, tenant_id=ticket.tenant_id, deleted=False
        ).order_by("-utime").first()
        if log and log.result and log.result != "执行中":
            return {"run_id": run_id, "status": log.status, "result": log.result}
        if log and log.status in (-1, 2):
            return {"run_id": run_id, "status": log.status, "result": log.result}
        await asyncio.sleep(1)
    raise TimeoutError(f"动态审批人取值动作超时: run_id={run_id}")
