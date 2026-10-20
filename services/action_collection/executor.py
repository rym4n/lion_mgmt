"""Execute one AIOE action through lion_action and await its Runlog."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import orjson

from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import Runlog
from services.action_collection.errors import CollectionError
from services.action_collection.targets import Target
from settings import TASK_TOPIC


@dataclass(frozen=True)
class ExecResult:
    status: int
    result_text: str | None
    duration_ms: int
    action_run_id: str | None = None


class ActionExecutor(Protocol):
    async def __call__(self, tenant_id: str, target: Target, *, timeout: int, executor_id: str) -> ExecResult: ...


class KafkaRunlogExecutor:
    async def __call__(self, tenant_id, target, *, timeout, executor_id):
        started = monotonic()
        run_id = gen_run_id()
        await aproduce(TASK_TOPIC, {"run_type": "action", "run_id": run_id, "tenant_id": tenant_id,
            "run_args": {"app_name": target.app_name, "name": target.app_name, "app_ver": target.app_version,
                         "main": target.main, "action": target.action_func, "action_name": target.action_name,
                         "action_input": target.action_input, "resource": target.resource_id,
                         "node_name": target.action_name, "retry_times": 0, "timeout": timeout, "executor": executor_id}},
                       raise_on_error=True)
        while monotonic() - started < timeout:
            log = await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).order_by("-utime").first()
            if log and ((log.status == 0 and log.result != "执行中") or log.status in (-1, 2, 3)):
                text = log.result if isinstance(log.result, str) or log.result is None else orjson.dumps(log.result).decode()
                return ExecResult(log.status, text, int((monotonic() - started) * 1000), run_id)
            await asyncio.sleep(min(1, max(0, timeout - (monotonic() - started))))
        raise CollectionError("COLLECTION_ITEM_TIMEOUT")
