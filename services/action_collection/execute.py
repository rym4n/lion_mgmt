"""Run frozen targets concurrently and classify each sanitized outcome."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any

import orjson

from services.action_collection.errors import CollectionError
from services.action_collection.executor import ActionExecutor, KafkaRunlogExecutor
from services.action_collection.outputs import MAX_OUTPUT_BYTES, parse_action_output, scrub_action_output
from services.action_collection.targets import Target


@dataclass(frozen=True)
class ItemOutcome:
    item_id: str
    app_name: str
    action_func: str
    app_version: str
    resource_id: str
    status: str
    output: Any
    observed_at: str
    error_code: str | None
    error_message: str | None
    duration_ms: int
    action_run_id: str | None = None


def text_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def execute_targets(tenant_id: str, targets: list[Target], *, concurrency: int, per_item_timeout: int,
                          executor_id: str, executor: ActionExecutor | None = None,
                          now: datetime | None = None) -> list[ItemOutcome]:
    executor = executor or KafkaRunlogExecutor()
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(target: Target) -> ItemOutcome:
        async with semaphore:
            started = monotonic()
            output = None
            error_code = error_message = None
            duration = 0
            action_run_id = None
            try:
                try:
                    result = await asyncio.wait_for(
                        executor(tenant_id, target, timeout=per_item_timeout, executor_id=executor_id),
                        timeout=per_item_timeout)
                except asyncio.TimeoutError:
                    raise CollectionError("COLLECTION_ITEM_TIMEOUT") from None
                duration = result.duration_ms
                action_run_id = result.action_run_id
                if result.status != 0:
                    message = "COLLECTION_ACTION_FAILED"
                    text = result.result_text
                    if isinstance(text, str) and text:
                        if len(text) <= MAX_OUTPUT_BYTES and len(text.encode("utf-8")) <= MAX_OUTPUT_BYTES:
                            try:
                                payload = scrub_action_output(orjson.loads(text))
                                message = str(payload.get("msg") or message)[:512] if isinstance(payload, dict) else message
                            except Exception:
                                pass
                    raise CollectionError("COLLECTION_ACTION_FAILED", message=message)
                output = parse_action_output(result.result_text)
                if isinstance(output, dict) and output.get("code") not in (0, 200):
                    message = output.get("msg") if isinstance(output, dict) else None
                    raise CollectionError("COLLECTION_ACTION_FAILED", message=str(message or "COLLECTION_ACTION_FAILED")[:512])
            except CollectionError as exc:
                error_code, error_message = exc.code, exc.message
                output = None
            except Exception:
                error_code = error_message = "COLLECTION_ACTION_FAILED"
            return ItemOutcome(target.item_id, target.app_name, target.action_func, target.app_version,
                               target.resource_id, "failed" if error_code else "success", output,
                               text_time(now or datetime.now(timezone.utc)), error_code, error_message,
                               duration or int((monotonic() - started) * 1000), action_run_id)

    return list(await asyncio.gather(*(execute(t) for t in targets)))
