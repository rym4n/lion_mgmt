# coding=utf-8
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import logging
import math
from itertools import islice
from typing import Any
from tortoise.transactions import in_transaction

from models.command_center import (
    COMMAND_RUN_STATUS_CANCELLED,
    COMMAND_RUN_STATUS_CHECKED_OUT,
    COMMAND_RUN_STATUS_COMPLETED,
    COMMAND_RUN_STATUS_CONTINUATION_READY,
    COMMAND_RUN_STATUS_FAILED,
    COMMAND_RUN_STATUS_HANDED_OFF,
    COMMAND_RUN_STATUS_REJECTED,
    COMMAND_RUN_STATUS_RUNNING,
    COMMAND_RUN_STATUS_STALE,
    COMMAND_RUN_STATUS_WAITING_APPROVAL,
    COMMAND_RUN_TERMINAL_STATUSES,
    COMMAND_RUNTIME_LION_AGENT,
    COMMAND_TASK_STATUS_CANCELLED,
    COMMAND_TASK_STATUS_COMPLETED,
    COMMAND_TASK_STATUS_DISPATCHED,
    COMMAND_TASK_STATUS_FAILED,
    COMMAND_TASK_STATUS_HANDED_OFF,
    COMMAND_TASK_STATUS_PENDING_APPROVAL,
    COMMAND_TASK_STATUS_RUNNING,
    COMMAND_TASK_TERMINAL_STATUSES,
    CommandAgent,
    CommandTask,
    CommandTaskRun,
)
from settings import TZ

from services.command_center.approval_contract import CONTINUATION_MODE
from services.digital_employee.prompt_builder import build_workspace_snapshots


DEFAULT_LEASE_SECONDS = 300
LEASE_HEARTBEAT_SECONDS = max(1, DEFAULT_LEASE_SECONDS // 3)
logger = logging.getLogger(__name__)
RECOVERABLE_RUN_STATUSES = {
    COMMAND_RUN_STATUS_CHECKED_OUT,
    COMMAND_RUN_STATUS_RUNNING,
}
CURRENT_RUN_STATUSES = {
    COMMAND_RUN_STATUS_CHECKED_OUT,
    COMMAND_RUN_STATUS_RUNNING,
    COMMAND_RUN_STATUS_WAITING_APPROVAL,
}
FINALIZABLE_RUN_STATUSES = {
    COMMAND_RUN_STATUS_COMPLETED,
    COMMAND_RUN_STATUS_FAILED,
    COMMAND_RUN_STATUS_CANCELLED,
    COMMAND_RUN_STATUS_STALE,
    COMMAND_RUN_STATUS_HANDED_OFF,
}
RuntimeEventHandler = Callable[[str, dict], Awaitable[dict | None]]
RunCheckedOutHandler = Callable[[CommandTaskRun], Awaitable[None]]
RUNTIME_EVENT_MAX_BYTES = 64 * 1024
RUNTIME_EVENT_MAX_STRING_BYTES = 16 * 1024
RUNTIME_EVENT_MAX_COLLECTION_ITEMS = 100
RUNTIME_EVENT_MAX_NODES = 512
RUNTIME_TOOL_CALLS_MAX = 100
RUNTIME_DIAGNOSTICS_MAX = 100
RUNTIME_BINARY_MARKER = "[binary runtime output omitted]"
RUNTIME_TRUNCATION_MARKER = "[runtime event truncated]"
RUNTIME_EVENT_BUFFER_TTL_SECONDS = 24 * 60 * 60
RUNTIME_EVENT_BUFFER_KEY_PREFIX = "command_center:runtime_events"
RUNTIME_EVENT_PERSISTED_COUNT_KEY = "_runtime_event_persisted_count"
RUN_SYNC_FIELDS = (
    "status",
    "heartbeat_at",
    "lease_expires_at",
    "finished_at",
    "metadata",
    "tool_calls",
    "error",
    "usage",
    "evidence_refs",
)


class RunCheckoutConflict(ValueError):
    """The task was already claimed or left the dispatchable state."""


class RunRetryConflict(ValueError):
    """The failed run is no longer the current retryable task run."""


def _now() -> datetime:
    return datetime.now(TZ)


class TaskExecutionDeadlineExceeded(TimeoutError):
    """Only the command run's own execution deadline expired."""


def _remaining_task_execution_seconds(task: CommandTask) -> float:
    timeout_seconds = getattr(task, "timeout_seconds", 3600)
    started_at = getattr(task, "started_at", None)
    if started_at is None:
        return float(timeout_seconds)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=TZ)
    return max(0.0, float(timeout_seconds) - (_now() - started_at).total_seconds())


async def _run_with_execution_deadline(task: CommandTask, work):
    timeout_scope = asyncio.timeout(_remaining_task_execution_seconds(task))
    try:
        async with timeout_scope:
            return await work()
    except TimeoutError as exc:
        if timeout_scope.expired():
            raise TaskExecutionDeadlineExceeded("command run execution deadline exceeded") from exc
        raise


def _sync_run(run: CommandTaskRun, locked_run: CommandTaskRun) -> None:
    for field in RUN_SYNC_FIELDS:
        setattr(run, field, getattr(locked_run, field))


def _event_payload(payload: Any) -> dict:
    return payload if isinstance(payload, dict) else {"value": payload}


def _runtime_text(value: str) -> str:
    """Keep runtime text JSON-safe without persisting binary tool output."""
    if any(
        (ord(character) < 32 and character not in "\t\r\n") or ord(character) == 127
        for character in value
    ):
        return RUNTIME_BINARY_MARKER
    raw = value.encode("utf-8")
    if len(raw) <= RUNTIME_EVENT_MAX_STRING_BYTES:
        return value
    marker = RUNTIME_TRUNCATION_MARKER
    available = max(0, RUNTIME_EVENT_MAX_STRING_BYTES - len(marker.encode("utf-8")))
    return raw[:available].decode("utf-8", "ignore") + marker


def _sanitize_runtime_value(value: Any, depth: int = 0, remaining_nodes: list[int] | None = None) -> Any:
    """Recursively normalize values before they reach JSONB/event history."""
    if remaining_nodes is None:
        remaining_nodes = [RUNTIME_EVENT_MAX_NODES]
    if depth > 12 or remaining_nodes[0] <= 0:
        return RUNTIME_TRUNCATION_MARKER
    remaining_nodes[0] -= 1
    if isinstance(value, str):
        return _runtime_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return RUNTIME_BINARY_MARKER
    if isinstance(value, dict):
        return {
            _runtime_text(str(key)): _sanitize_runtime_value(item, depth + 1, remaining_nodes)
            for key, item in islice(value.items(), RUNTIME_EVENT_MAX_COLLECTION_ITEMS)
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _sanitize_runtime_value(item, depth + 1, remaining_nodes)
            for item in islice(iter(value), RUNTIME_EVENT_MAX_COLLECTION_ITEMS)
        ]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return _runtime_text(str(value))


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return RUNTIME_EVENT_MAX_BYTES + 1


def _bound_runtime_value(value: Any, limit: int = RUNTIME_EVENT_MAX_BYTES) -> Any:
    """Fit a sanitized value into a bounded JSON document, preserving early fields."""
    if _json_size(value) <= limit:
        return value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        marker = RUNTIME_TRUNCATION_MARKER
        available = max(0, limit - len(marker.encode("utf-8")))
        return raw[:available].decode("utf-8", "ignore") + marker
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            remaining = limit - _json_size(result) - len(json.dumps(str(key), ensure_ascii=False).encode("utf-8")) - 4
            if remaining <= 0:
                break
            candidate = {**result, key: _bound_runtime_value(item, remaining)}
            if _json_size(candidate) > limit:
                break
            result = candidate
        return result
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            remaining = limit - _json_size(result) - 2
            if remaining <= 0:
                break
            candidate = [*result, _bound_runtime_value(item, remaining)]
            if _json_size(candidate) > limit:
                break
            result = candidate
        return result
    return RUNTIME_TRUNCATION_MARKER


def _sanitize_runtime_event(event: dict) -> dict:
    return _bound_runtime_value(_sanitize_runtime_value(event))


def runtime_event_buffer_key(task: CommandTask, run: CommandTaskRun) -> str:
    """Return the Redis key for one isolated execution transcript."""
    return ":".join(
        (
            RUNTIME_EVENT_BUFFER_KEY_PREFIX,
            str(task.tenant_id),
            str(task.id),
            str(run.run_id),
        )
    )


def _runtime_event_cache():
    from aiocache import caches

    return caches.get("redis_mgmt")


def _runtime_event_envelope(
    task: CommandTask,
    run: CommandTaskRun,
    normalized: dict,
    operator_id: str | None,
) -> dict:
    return {
        "event_id": uuid.uuid4().hex,
        "task_id": str(task.id),
        "tenant_id": str(task.tenant_id),
        "run_id": str(run.run_id),
        "ctime": _now().isoformat(),
        "status": normalized.get("task_status") or getattr(task, "status", None),
        "progress": getattr(task, "progress", None),
        "agent_key": getattr(task, "assigned_agent_key", None),
        "operator_id": operator_id,
        "event": normalized,
    }


async def _buffer_runtime_event(
    task: CommandTask,
    run: CommandTaskRun,
    envelope: dict,
) -> bool:
    """Append a sanitized runtime event to Redis without touching PostgreSQL."""
    key = runtime_event_buffer_key(task, run)
    try:
        client = _runtime_event_cache().client
        await client.rpush(
            key,
            json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
    except Exception as exc:
        logger.warning(
            "command-center runtime event Redis buffer failed: task_id=%s run_id=%s error=%s",
            getattr(task, "id", None),
            getattr(run, "run_id", None),
            exc,
        )
        return False
    try:
        await client.expire(key, RUNTIME_EVENT_BUFFER_TTL_SECONDS)
    except Exception as exc:
        logger.warning(
            "command-center runtime event Redis buffer expiry failed: task_id=%s run_id=%s error=%s",
            getattr(task, "id", None),
            getattr(run, "run_id", None),
            exc,
        )
    return True


async def _read_runtime_event_buffer(task: CommandTask, run: CommandTaskRun) -> list[dict]:
    try:
        values = await _runtime_event_cache().client.lrange(runtime_event_buffer_key(task, run), 0, -1)
    except Exception as exc:
        logger.warning(
            "command-center runtime event Redis buffer read failed: task_id=%s run_id=%s error=%s",
            getattr(task, "id", None),
            getattr(run, "run_id", None),
            exc,
        )
        raise
    events = []
    for value in values or []:
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        try:
            envelope = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid runtime event buffer entry for run {run.run_id}") from exc
        if not isinstance(envelope, dict) or not isinstance(envelope.get("event"), dict):
            raise ValueError(f"invalid runtime event buffer entry for run {run.run_id}")
        events.append(envelope)
    return events


async def _clear_runtime_event_buffer(task: CommandTask, run: CommandTaskRun) -> None:
    try:
        await _runtime_event_cache().client.delete(runtime_event_buffer_key(task, run))
    except Exception as exc:
        logger.warning(
            "command-center runtime event Redis buffer cleanup failed: task_id=%s run_id=%s error=%s",
            getattr(task, "id", None),
            getattr(run, "run_id", None),
            exc,
        )


def _runtime_event_row(task: CommandTask, run: CommandTaskRun, envelope: dict):
    from models.command_center import CommandTaskEvent

    event = envelope["event"]
    metadata = dict(event.get("metadata") or {})
    metadata["run_id"] = envelope.get("run_id") or run.run_id
    for field in ("code", "blocking", "tool_call", "requested_action"):
        if field in event:
            metadata[field] = event[field]
    created_at = envelope.get("ctime")
    try:
        created_at = datetime.fromisoformat(created_at) if created_at else None
    except (TypeError, ValueError):
        created_at = None
    kwargs = {
        "id": envelope.get("event_id") or uuid.uuid4().hex,
        "tenant_id": envelope.get("tenant_id") or task.tenant_id,
        "task": task,
        "event_type": event.get("event_type") or "runtime_progress",
        "status": envelope.get("status") or getattr(task, "status", None),
        "progress": envelope.get("progress"),
        "agent_key": envelope.get("agent_key"),
        "message": event.get("message"),
        "metadata": metadata,
        "operator_id": envelope.get("operator_id"),
    }
    if created_at is not None:
        kwargs["ctime"] = created_at
        kwargs["utime"] = created_at
    return CommandTaskEvent(**kwargs)


async def _persist_runtime_event_buffer(
    task: CommandTask,
    run: CommandTaskRun,
    connection,
) -> int:
    """Persist a run's buffered stream in one database batch."""
    envelopes = await _read_runtime_event_buffer(task, run)
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    persisted_count = metadata.get(RUNTIME_EVENT_PERSISTED_COUNT_KEY, 0)
    if not isinstance(persisted_count, int) or persisted_count < 0:
        raise ValueError(f"invalid runtime event persistence cursor for run {run.run_id}")
    if len(envelopes) <= persisted_count:
        return 0
    from models.command_center import CommandTaskEvent

    rows = [_runtime_event_row(task, run, envelope) for envelope in envelopes[persisted_count:]]
    await CommandTaskEvent.bulk_create(
        rows,
        batch_size=500,
        ignore_conflicts=True,
        using_db=connection,
    )
    run.metadata = {**metadata, RUNTIME_EVENT_PERSISTED_COUNT_KEY: len(envelopes)}
    await run.save(using_db=connection, update_fields=["metadata", "utime"])
    return len(rows)


def _runtime_control_action(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    action = data.get("requested_action") if isinstance(data.get("requested_action"), dict) else data
    return action if isinstance(action, dict) else {}


def _runtime_tool_name(data: dict) -> str | None:
    return data.get("name") or data.get("tool_name") or data.get("tool") or data.get("tool_call_name")


def _merge_runtime_diagnostics(metadata: Any, diagnostic: Any) -> dict:
    current = metadata if isinstance(metadata, dict) else {}
    items = list(current.get("runtime_diagnostics") or [])
    if isinstance(diagnostic, dict):
        items.append(diagnostic)
    return {**current, "runtime_diagnostics": items[-RUNTIME_DIAGNOSTICS_MAX:]}


def _safe_runtime_error(exc: BaseException) -> dict:
    payload = getattr(exc, "payload", None)
    if callable(payload):
        try:
            payload = payload()
        except TypeError:
            payload = None
    if isinstance(payload, dict):
        return {
            "layer": "runtime",
            "code": str(payload.get("code") or "runtime_internal_error"),
            "message": str(payload.get("message") or "数字伙伴运行异常"),
            "blocking": bool(payload.get("blocking", True)),
        }
    return {
        "layer": "runtime",
        "code": "runtime_internal_error",
        "message": "数字伙伴运行异常，请查看运行详情",
        "blocking": True,
    }


def _existing_runtime_error(run: CommandTaskRun) -> dict | None:
    """Keep a structured runtime event when a later wrapper raises generically.

    Streaming runtimes persist the error event before the coordinator raises its
    control-flow exception. If that wrapper loses the original exception type,
    the persisted code is still the most specific operator-facing evidence.
    """
    error = getattr(run, "error", None)
    if not isinstance(error, dict):
        return None
    primary = error.get("primary") if isinstance(error.get("primary"), dict) else error
    code = primary.get("code")
    if not code or code == "runtime_internal_error":
        return None
    return {
        "layer": "runtime",
        "code": str(code),
        "message": str(primary.get("message") or "数字伙伴运行异常"),
        "blocking": bool(primary.get("blocking", True)),
    }


def _safe_business_message(runlog: Any) -> str:
    node = str(getattr(runlog, "node_name", None) or getattr(runlog, "action_display_name", None) or "剧本节点")
    result = str(getattr(runlog, "result", None) or "").strip()
    if not result or result == "执行中":
        return f"剧本节点“{node}”执行失败"
    # Keep the operator-facing cause useful while avoiding model/schema dumps.
    lowered = result.lower()
    if "jsonschema" in lowered or "validationerror" in lowered or "context_to_preserve" in lowered:
        return f"剧本节点“{node}”执行失败（业务动作返回失败，运行时校验异常已隔离）"
    return f"剧本节点“{node}”执行失败：{result[:500]}"


def _runlog_business_failed(runlog: Any) -> bool:
    status = getattr(runlog, "status", None)
    if status in {-1, 3}:
        return True
    raw = getattr(runlog, "result", None)
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == "执行中":
        return False
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    return (
        code is not None and code >= 400
    ) or str(payload.get("status") or "").lower() in {"failed", "failure", "error"} \
        or payload.get("success") is False


async def _collect_failure_layers(run: CommandTaskRun, exc: BaseException) -> dict[str, Any]:
    runtime = _safe_runtime_error(exc)
    persisted_runtime = _existing_runtime_error(run)
    if persisted_runtime and runtime.get("code") == "runtime_internal_error":
        runtime = persisted_runtime
    layers: dict[str, Any] = {"runtime": runtime}
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    memory = metadata.get("memory_status")
    if isinstance(memory, dict):
        layers["memory"] = memory
    linked_ids = metadata.get("linked_run_ids")
    linked_ids = [str(item) for item in linked_ids if item] if isinstance(linked_ids, list) else []
    if linked_ids:
        try:
            from models.runlog import Runlog

            logs = await Runlog.filter(
                tenant_id=getattr(run, "tenant_id", None),
                run_id__in=linked_ids,
                deleted=False,
                status__in=[-1, 0, 3],
            ).order_by("ctime", "id").all()
            logs = [log for log in logs if _runlog_business_failed(log)]
            if logs:
                primary = logs[0]
                layers["business"] = {
                    "layer": "business",
                    "code": "playbook_execution_failed",
                    "message": _safe_business_message(primary),
                    "run_id": str(getattr(primary, "run_id", "")),
                    "node_id": str(getattr(primary, "node_id", "")),
                    "node_name": str(getattr(primary, "node_name", "") or ""),
                    "status": getattr(primary, "status", None),
                }
        except Exception as query_exc:
            logger.warning("command-center failure aggregation skipped error_code=%s", type(query_exc).__name__)
    return layers


async def _persist_failure_layers(run: CommandTaskRun, layers: dict[str, Any]) -> None:
    """Merge layered failure evidence even when a prior runtime event closed the run."""
    async with in_transaction() as connection:
        locked = (
            await CommandTaskRun.filter(id=run.id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked:
            return
        metadata = locked.metadata if isinstance(locked.metadata, dict) else {}
        metadata = {**metadata, "failure_layers": layers}
        primary = layers.get("business") or layers.get("runtime") or {}
        error = {
            "layer": primary.get("layer") or "runtime",
            "code": primary.get("code") or "runtime_internal_error",
            "message": primary.get("message") or "数字伙伴运行异常，请查看运行详情",
            "primary": primary,
            "secondary": [value for key, value in layers.items() if key != primary.get("layer")],
        }
        locked.error = error
        locked.metadata = metadata
        await locked.save(using_db=connection, update_fields=["error", "metadata", "utime"])
    _sync_run(run, locked)


def normalize_runtime_event(event_type: str, payload: Any) -> dict:
    data = _event_payload(payload)
    if event_type == "delta":
        return {
            "event_type": "runtime_delta",
            "message": data.get("content") or data.get("delta"),
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "thinking":
        return {
            "event_type": "runtime_thinking",
            "message": data.get("content"),
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "tool_call":
        tool_data = data.get("data") if isinstance(data.get("data"), dict) else data
        tool_name = _runtime_tool_name(tool_data)
        status = data.get("status") or tool_data.get("status") or "started"
        return {
            "event_type": "runtime_tool_call",
            "message": f"{tool_name or 'tool'} {status}",
            "tool_call": {"tool_name": tool_name, "status": status, "data": tool_data},
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "tool_error":
        tool_data = data.get("data") if isinstance(data.get("data"), dict) else data
        return {
            "event_type": "runtime_tool_call",
            "message": tool_data.get("message") or "工具调用被拒绝",
            "tool_call": {"tool_name": _runtime_tool_name(tool_data), "status": "denied", "data": tool_data},
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "approval_required":
        requested_action = _runtime_control_action(data)
        return {
            "event_type": "approval_required",
            "message": "数字伙伴请求人工审批",
            "run_status": COMMAND_RUN_STATUS_WAITING_APPROVAL,
            "task_status": COMMAND_TASK_STATUS_PENDING_APPROVAL,
            "requested_action": requested_action,
            "metadata": {"runtime_event": event_type, "payload": data, "requested_action": requested_action},
        }
    if event_type == "policy_denied":
        denied_action = _runtime_control_action(data)
        return {
            "event_type": "policy_denied",
            "message": data.get("message") or "策略拒绝本次运行",
            "run_status": COMMAND_RUN_STATUS_FAILED,
            "task_status": COMMAND_TASK_STATUS_FAILED,
            "error": {"message": data.get("message") or "policy denied", "payload": data},
            "requested_action": denied_action,
            "metadata": {"runtime_event": event_type, "payload": data, "requested_action": denied_action},
        }
    if event_type == "error":
        code = data.get("code") or "runtime_internal_error"
        blocking = data.get("blocking", True)
        return {
            "event_type": "runtime_error" if blocking else "runtime_degraded",
            "code": code,
            "message": data.get("message") or "数字伙伴运行异常",
            "blocking": blocking,
            "run_status": COMMAND_RUN_STATUS_FAILED if blocking else None,
            "task_status": COMMAND_TASK_STATUS_FAILED if blocking else None,
            "error": {
                "code": code,
                "message": data.get("message") or "数字伙伴运行异常",
                "blocking": blocking,
                "payload": data,
            } if blocking else None,
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "runtime_degraded":
        return {
            "event_type": "runtime_degraded",
            "code": data.get("code") or "runtime_degraded",
            "message": data.get("message") or "数字伙伴运行已降级继续",
            "blocking": False,
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "runtime_memory":
        return {
            "event_type": "runtime_memory",
            "message": "记忆写回状态已记录",
            "blocking": False,
            "metadata": {
                "runtime_event": event_type,
                "payload": data,
                "memory_status": data,
            },
        }
    if event_type == "end":
        return {
            "event_type": "runtime_completed",
            "message": "数字伙伴运行完成",
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    return {
        "event_type": "runtime_progress",
        "message": data.get("message"),
        "metadata": {"runtime_event": event_type, "payload": data},
    }


async def _workspace_snapshots(employee: CommandAgent) -> dict:
    role = await employee.role
    if role is None:
        return build_workspace_snapshots(employee, None, [])
    from services.digital_employee.manager import get_visible_skills

    skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
    return build_workspace_snapshots(employee, role, skills)


async def checkout_run(
    task: CommandTask,
    employee: CommandAgent,
    operator_id: str | None = None,
    lease_owner: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    metadata: dict | None = None,
    policy_snapshot_override: dict | None = None,
    workspace_etag_override: str | None = None,
    session_id: str | None = None,
) -> CommandTaskRun:
    from services.command_center.manager import record_task_event

    now = _now()
    snapshots = await _workspace_snapshots(employee)
    run_metadata = {"operator_id": operator_id, "continuation_mode": CONTINUATION_MODE}
    if metadata:
        run_metadata.update(metadata)
    async with in_transaction() as connection:
        locked_task = (
            await CommandTask.filter(id=task.id, tenant_id=task.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_task or locked_task.status != COMMAND_TASK_STATUS_DISPATCHED:
            raise RunCheckoutConflict("task is not dispatchable")
        next_run_id = uuid.uuid4().hex
        run = await CommandTaskRun.create(
            using_db=connection,
            tenant_id=locked_task.tenant_id,
            task=locked_task,
            employee=employee,
            run_id=next_run_id,
            session_id=session_id or next_run_id,
            runtime=COMMAND_RUNTIME_LION_AGENT,
            status=COMMAND_RUN_STATUS_CHECKED_OUT,
            lease_owner=lease_owner or f"lion_mgmt:{locked_task.id}",
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            workspace_etag=workspace_etag_override or snapshots["workspace_etag"],
            manifest_snapshot=snapshots["manifest_snapshot"],
            policy_snapshot=policy_snapshot_override or snapshots["policy_snapshot"],
            started_at=now,
            metadata=run_metadata,
        )
        locked_task.run_id = run.run_id
        locked_task.status = COMMAND_TASK_STATUS_RUNNING
        if not locked_task.started_at:
            locked_task.started_at = now
        await locked_task.save(
            using_db=connection,
            update_fields=["run_id", "status", "started_at", "utime"],
        )
        await record_task_event(
            locked_task,
            "run_checked_out",
            "数字伙伴运行已 checkout",
            operator_id,
            {"run_id": run.run_id, "lease_owner": run.lease_owner, "workspace_etag": run.workspace_etag},
            using_db=connection,
        )
    task.run_id = locked_task.run_id
    task.status = locked_task.status
    task.started_at = locked_task.started_at
    return run


def _read_only_policy_snapshot(policy_snapshot: dict) -> dict:
    policy = deepcopy(policy_snapshot or {})
    policy["continuation_mutation"] = {
        "mode": "read_only",
        "filesystem": {"write": "deny", "delete": "deny"},
        "tool_permissions": {"writes": "deny", "mutations": "deny"},
    }
    filesystem = policy.get("filesystem") if isinstance(policy.get("filesystem"), dict) else {}
    policy["filesystem"] = {**filesystem, "write": "deny", "delete": "deny"}
    tool_permissions = policy.get("tool_permissions") if isinstance(policy.get("tool_permissions"), dict) else {}
    rules = tool_permissions.get("rules") if isinstance(tool_permissions.get("rules"), dict) else {}
    policy["tool_permissions"] = {
        **tool_permissions,
        "mode": "read_only",
        "rules": {**rules, "write": "deny", "delete": "deny", "mutate": "deny"},
    }
    approval = policy.get("approval") if isinstance(policy.get("approval"), dict) else {}
    skill_policy = dict(approval.get("skills") or {})
    for skill_key, decision in list(skill_policy.items()):
        if decision == "allow":
            skill_policy[skill_key] = "approval_required"
    policy["approval"] = {**approval, "default": "approval_required", "skills": skill_policy}
    return policy


async def _continuation_workspace(
    employee: CommandAgent,
    approved_policy_mutation: str | None,
    continuation_approval_id: str | None,
) -> dict | None:
    if approved_policy_mutation != "read_only" or not continuation_approval_id:
        return None
    suffix = hashlib.sha256(f"{employee.id}:{continuation_approval_id}:read_only".encode("utf-8")).hexdigest()[:12]
    workspace_id = f"{employee.id}:approval-{suffix}:read-only"
    role = await employee.role
    if role is None:
        from services.digital_employee.prompt_builder import build_workspace_payload, render_workspace_files

        payload = build_workspace_payload(employee, None, [])
        files = render_workspace_files(employee, None, [])
        policy_snapshot = _read_only_policy_snapshot(payload["policy"])
        files["policy.json"] = policy_snapshot
        workspace_etag = f"read-only-{suffix}"
        manifest = dict(payload["manifest"])
        manifest["workspace_etag"] = workspace_etag
        manifest["continuation_mutation"] = {"mode": "read_only", "approval_id": continuation_approval_id}
        files["manifest.json"] = manifest
    else:
        from services.digital_employee.manager import get_visible_skills
        from services.digital_employee.prompt_builder import build_workspace_payload, render_workspace_files, workspace_etag

        skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
        payload = build_workspace_payload(employee, role, skills)
        policy_snapshot = _read_only_policy_snapshot(payload["policy"])
        files = render_workspace_files(employee, role, skills)
        files["policy.json"] = policy_snapshot
        role_version = getattr(role, "version", None)
        skill_versions = {skill.skill_key: skill.version for skill in skills}
        etag = workspace_etag(role, skills, employee)
        workspace_etag = f"{etag[:32]}-ro-{suffix}"
        manifest = dict(payload["manifest"])
        manifest["workspace_etag"] = workspace_etag
        manifest["continuation_mutation"] = {"mode": "read_only", "approval_id": continuation_approval_id}
        files["manifest.json"] = manifest
        return {
            "workspace_id": workspace_id,
            "files": files,
            "role_version": role_version,
            "skill_versions": skill_versions,
            "workspace_etag": workspace_etag,
            "policy_snapshot": policy_snapshot,
        }
    return {
        "workspace_id": workspace_id,
        "files": files,
        "role_version": None,
        "skill_versions": {},
        "workspace_etag": workspace_etag,
        "policy_snapshot": policy_snapshot,
    }


async def heartbeat_run(run: CommandTaskRun) -> CommandTaskRun | None:
    async with in_transaction() as connection:
        locked_run = (
            await CommandTaskRun.filter(id=run.id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_run or locked_run.status not in RECOVERABLE_RUN_STATUSES:
            if locked_run:
                _sync_run(run, locked_run)
            return None

        locked_run.heartbeat_at = _now()
        locked_run.lease_expires_at = locked_run.heartbeat_at + timedelta(seconds=DEFAULT_LEASE_SECONDS)
        update_fields = ["heartbeat_at", "lease_expires_at", "utime"]
        if locked_run.status == COMMAND_RUN_STATUS_CHECKED_OUT:
            locked_run.status = COMMAND_RUN_STATUS_RUNNING
            update_fields.append("status")
        await locked_run.save(using_db=connection, update_fields=update_fields)

    _sync_run(run, locked_run)
    return run


async def _maintain_run_lease(run: CommandTaskRun) -> None:
    while run.status in RECOVERABLE_RUN_STATUSES:
        await asyncio.sleep(LEASE_HEARTBEAT_SECONDS)
        try:
            if await heartbeat_run(run) is None:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("command-center run heartbeat failed: run_id=%s error=%s", run.run_id, exc)


async def record_runtime_event(
    task: CommandTask,
    run: CommandTaskRun,
    event_type: str,
    payload: Any,
    operator_id: str | None = None,
) -> dict | None:
    normalized = _sanitize_runtime_event(normalize_runtime_event(event_type, payload))

    # Runtime stream data stays in Redis while the run is active. Keep the
    # bounded run-local aggregates in memory so the terminal write can save
    # them together with the final state.
    if normalized.get("tool_call"):
        run.tool_calls = _sanitize_runtime_value(
            [*(getattr(run, "tool_calls", None) or []), normalized["tool_call"]][-RUNTIME_TOOL_CALLS_MAX:]
        )
        run._runtime_tool_calls_dirty = True
    if normalized.get("error"):
        run.error = normalized["error"]
        run._runtime_error_dirty = True
    if normalized["event_type"] == "runtime_degraded":
        run.metadata = _merge_runtime_diagnostics(
            getattr(run, "metadata", None),
            normalized.get("metadata", {}).get("payload"),
        )
        run._runtime_metadata_dirty = True
    elif normalized["event_type"] == "runtime_memory":
        metadata = getattr(run, "metadata", None) if isinstance(getattr(run, "metadata", None), dict) else {}
        run.metadata = {
            **metadata,
            "memory_status": normalized.get("metadata", {}).get("memory_status"),
        }
        run._runtime_metadata_dirty = True

    if getattr(run, "status", None) not in RECOVERABLE_RUN_STATUSES:
        return None
    if getattr(task, "run_id", None) != getattr(run, "run_id", None):
        return None
    if getattr(task, "status", None) not in {COMMAND_TASK_STATUS_DISPATCHED, COMMAND_TASK_STATUS_RUNNING}:
        return None
    if run.status == COMMAND_RUN_STATUS_CHECKED_OUT:
        run.status = COMMAND_RUN_STATUS_RUNNING

    envelope = _runtime_event_envelope(task, run, normalized, operator_id)
    buffered = await _buffer_runtime_event(task, run, envelope)
    # Normal stream events do not open a PostgreSQL transaction. Lease renewal
    # is maintained independently by _maintain_run_lease().
    if not buffered:
        logger.warning(
            "command-center runtime event falling back to PostgreSQL: task_id=%s run_id=%s event_type=%s",
            getattr(task, "id", None),
            getattr(run, "run_id", None),
            normalized.get("event_type"),
        )
    elif not normalized.get("run_status") and not normalized.get("task_status"):
        return normalized

    from services.command_center.manager import (
        apply_task_progress_in_transaction,
        complete_task_progress_after_commit,
        record_task_event,
    )

    task_transition = None
    buffer_persisted = False
    async with in_transaction() as connection:
        locked_run = (
            await CommandTaskRun.filter(id=run.id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_run or locked_run.status not in RECOVERABLE_RUN_STATUSES:
            if locked_run:
                _sync_run(run, locked_run)
            return None

        locked_task = (
            await CommandTask.filter(id=task.id, tenant_id=task.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if (
            not locked_task
            or locked_task.status not in {COMMAND_TASK_STATUS_DISPATCHED, COMMAND_TASK_STATUS_RUNNING}
            or locked_task.run_id != locked_run.run_id
        ):
            _sync_run(run, locked_run)
            return None

        now = _now()
        locked_run.heartbeat_at = now
        locked_run.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
        update_fields = ["heartbeat_at", "lease_expires_at", "utime"]
        if normalized.get("tool_call"):
            locked_run.tool_calls = run.tool_calls
            update_fields.append("tool_calls")
        if normalized.get("error"):
            locked_run.error = run.error
            update_fields.append("error")
        if normalized["event_type"] == "runtime_degraded" or normalized["event_type"] == "runtime_memory":
            locked_run.metadata = run.metadata
            update_fields.append("metadata")
        run_status = normalized.get("run_status")
        if run_status:
            locked_run.status = run_status
            update_fields.append("status")
            if run_status in COMMAND_RUN_TERMINAL_STATUSES:
                locked_run.finished_at = now
                update_fields.append("finished_at")
        elif locked_run.status == COMMAND_RUN_STATUS_CHECKED_OUT:
            locked_run.status = COMMAND_RUN_STATUS_RUNNING
            update_fields.append("status")
        await locked_run.save(using_db=connection, update_fields=sorted(set(update_fields)))

        task_status = normalized.get("task_status")
        if task_status in COMMAND_TASK_TERMINAL_STATUSES:
            task_transition = await apply_task_progress_in_transaction(
                connection,
                task,
                {
                    "status": task_status,
                    "message": normalized.get("message"),
                    "metadata": {"run_id": locked_run.run_id, **(normalized.get("metadata") or {})},
                },
                operator_id,
                locked_task=locked_task,
                expected_run_id=locked_run.run_id,
            )
            if task_transition is None:
                return None
            locked_task = task_transition[0]
        elif task_status:
            locked_task.status = task_status
            await locked_task.save(using_db=connection, update_fields=["status", "utime"])

        if buffered:
            await _persist_runtime_event_buffer(locked_task, locked_run, connection)
            buffer_persisted = True
        else:
            from models.command_center import CommandTaskEvent

            await CommandTaskEvent.bulk_create(
                [_runtime_event_row(locked_task, locked_run, envelope)],
                ignore_conflicts=True,
                using_db=connection,
            )
        if normalized["event_type"] == "approval_required":
            from services.command_center.approval_service import create_approval_request

            requested_action = normalized.get("requested_action") or {}
            await create_approval_request(
                locked_task,
                locked_run,
                trigger_type="runtime",
                requested_action=requested_action,
                risk_level=requested_action.get("risk_level") or "medium",
                operator_id=operator_id,
                reason="数字伙伴请求人工审批",
            )

    _sync_run(run, locked_run)
    if buffer_persisted:
        await _clear_runtime_event_buffer(task, locked_run)
    if task_transition is not None:
        await complete_task_progress_after_commit(
            task,
            task_transition,
            {"metadata": normalized.get("metadata") or {}},
        )
    elif locked_task is not task:
        task.status = locked_task.status
    return normalized


async def _finalize_run_in_transaction(
    connection,
    run: CommandTaskRun,
    status: str,
    *,
    result: dict | None = None,
    error: dict | None = None,
    usage: dict | None = None,
    evidence_refs: list[str] | None = None,
) -> tuple[CommandTaskRun | None, bool]:
    if status not in FINALIZABLE_RUN_STATUSES:
        raise ValueError(f"invalid terminal run status: {status}")
    locked_run = (
        await CommandTaskRun.filter(id=run.id, deleted=False)
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if not locked_run:
        return None, False
    await locked_run.refresh_from_db(fields=["metadata"], using_db=connection)
    if locked_run.status in COMMAND_RUN_TERMINAL_STATUSES:
        return locked_run, False

    if getattr(run, "_runtime_metadata_dirty", False):
        persisted_metadata = locked_run.metadata if isinstance(locked_run.metadata, dict) else {}
        local_metadata = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        locked_run.metadata = {**persisted_metadata, **local_metadata}
    if getattr(run, "_runtime_tool_calls_dirty", False):
        locked_run.tool_calls = list(getattr(run, "tool_calls", None) or [])
    if getattr(run, "_runtime_error_dirty", False) and error is None:
        locked_run.error = run.error

    locked_run.status = status
    locked_run.finished_at = _now()
    locked_run.heartbeat_at = locked_run.finished_at
    if result is not None:
        metadata = locked_run.metadata if isinstance(locked_run.metadata, dict) else {}
        locked_run.metadata = {**metadata, "result": result}
    if error is not None:
        locked_run.error = error
    if usage is not None:
        locked_run.usage = usage
    if evidence_refs is not None:
        locked_run.evidence_refs = [*(locked_run.evidence_refs or []), *evidence_refs]
    await locked_run.save(
        using_db=connection,
        update_fields=[
            "status",
            "finished_at",
            "heartbeat_at",
            "metadata",
            "tool_calls",
            "error",
            "usage",
            "evidence_refs",
            "utime",
        ],
    )
    return locked_run, True


async def finalize_run(
    run: CommandTaskRun,
    status: str,
    *,
    result: dict | None = None,
    error: dict | None = None,
    usage: dict | None = None,
    evidence_refs: list[str] | None = None,
) -> CommandTaskRun:
    task = None
    async with in_transaction() as connection:
        locked_run, _changed = await _finalize_run_in_transaction(
            connection,
            run,
            status,
            result=result,
            error=error,
            usage=usage,
            evidence_refs=evidence_refs,
        )
        if locked_run:
            task = await (
                CommandTask.filter(id=run.task_id, tenant_id=run.tenant_id, deleted=False)
                .using_db(connection)
                .first()
            )
            if task:
                await _persist_runtime_event_buffer(task, locked_run, connection)
    if locked_run:
        _sync_run(run, locked_run)
    if locked_run and task and locked_run.status in COMMAND_RUN_TERMINAL_STATUSES:
        await _clear_runtime_event_buffer(task, locked_run)
    return run


async def finalize_run_and_task(
    run: CommandTaskRun,
    task: CommandTask,
    status: str,
    task_data: dict,
    *,
    operator_id: str | None = None,
    result: dict | None = None,
    error: dict | None = None,
    usage: dict | None = None,
    evidence_refs: list[str] | None = None,
) -> CommandTaskRun:
    from services.command_center.manager import (
        apply_task_progress_in_transaction,
        complete_task_progress_after_commit,
    )

    task_transition = None
    async with in_transaction() as connection:
        locked_run, changed = await _finalize_run_in_transaction(
            connection,
            run,
            status,
            result=result,
            error=error,
            usage=usage,
            evidence_refs=evidence_refs,
        )
        if changed:
            task_transition = await apply_task_progress_in_transaction(
                connection,
                task,
                task_data,
                operator_id,
                expected_run_id=locked_run.run_id,
            )
        if locked_run:
            await _persist_runtime_event_buffer(task, locked_run, connection)

    if locked_run:
        _sync_run(run, locked_run)
    if locked_run and locked_run.status in COMMAND_RUN_TERMINAL_STATUSES:
        await _clear_runtime_event_buffer(task, locked_run)
    if task_transition is not None:
        await complete_task_progress_after_commit(task, task_transition, task_data)
    return run


async def execute_task_run(
    task: CommandTask,
    employee: CommandAgent,
    operator_id: str | None = None,
    lease_owner: str | None = None,
    *,
    continuation_approval_id: str | None = None,
    continued_from_run_id: str | None = None,
    skip_pre_run_policy: bool = False,
    approved_policy_mutation: str | None = None,
    on_run_checked_out: RunCheckedOutHandler | None = None,
    session_id: str | None = None,
    retried_from_run_id: str | None = None,
) -> dict:
    continuation_workspace = await _continuation_workspace(employee, approved_policy_mutation, continuation_approval_id)
    continuation_metadata = None
    if continuation_approval_id:
        continuation_metadata = {
            "continuation_approval_id": continuation_approval_id,
            "continued_from_run_id": continued_from_run_id,
            "skip_pre_run_policy": skip_pre_run_policy,
            "approved_policy_mutation": approved_policy_mutation,
        }
        if continuation_workspace:
            continuation_metadata["workspace_id"] = continuation_workspace["workspace_id"]
    if retried_from_run_id:
        continuation_metadata = {
            **(continuation_metadata or {}),
            "retried_from_run_id": retried_from_run_id,
        }
    run = await checkout_run(
        task,
        employee,
        operator_id=operator_id,
        lease_owner=lease_owner,
        metadata=continuation_metadata,
        policy_snapshot_override=continuation_workspace["policy_snapshot"] if continuation_workspace else None,
        workspace_etag_override=continuation_workspace["workspace_etag"] if continuation_workspace else None,
        session_id=session_id,
    )
    lease_heartbeat_task = None
    try:
        async def _on_runtime_event(event_type: str, payload: dict) -> dict | None:
            return await record_runtime_event(task, run, event_type, payload, operator_id)

        lease_heartbeat_task = asyncio.create_task(_maintain_run_lease(run))
        from services.digital_employee.execute_coordinator import run as execute_run

        async def _execute_task():
            if on_run_checked_out:
                await on_run_checked_out(run)
            if continuation_approval_id:
                from services.command_center.manager import record_task_event

                await record_task_event(
                    task,
                    "continuation_started",
                    "审批已通过，重新启动数字伙伴运行",
                    operator_id,
                    {
                        "approval_id": continuation_approval_id,
                        "run_id": run.run_id,
                        "continued_from_run_id": continued_from_run_id,
                        "policy_mutation": approved_policy_mutation,
                    },
                )
            from services.command_center.approval_service import enforce_pre_run_policy

            if not skip_pre_run_policy:
                policy_result = await enforce_pre_run_policy(task, run, operator_id=operator_id)
                if policy_result["decision"] != "allow":
                    return {"content": "", "policy": policy_result["decision"], "run_id": run.run_id}, True
            result = await execute_run(
                task,
                employee,
                operator_id=operator_id,
                command_run=run,
                on_runtime_event=_on_runtime_event,
                continuation_workspace=continuation_workspace,
            )
            return result, False

        result, pre_run_stopped = await _run_with_execution_deadline(task, _execute_task)
        result = {**(result or {}), "run_id": run.run_id}
        if pre_run_stopped:
            return result
        if result.get("policy") == "approval_required":
            return result
        if result.get("policy") == "policy_denied":
            runtime_event = result.get("runtime_event") or {}
            await finalize_run_and_task(
                run,
                task,
                COMMAND_RUN_STATUS_FAILED,
                {
                    "status": COMMAND_TASK_STATUS_FAILED,
                    "message": runtime_event.get("message") or "策略拒绝本次运行",
                    "metadata": {"run_id": run.run_id, "runtime_event": runtime_event},
                },
                operator_id=operator_id,
                error=runtime_event.get("error") or {"message": "policy denied"},
                usage=result.get("usage"),
            )
            return result
        await finalize_run_and_task(
            run,
            task,
            COMMAND_RUN_STATUS_COMPLETED,
            {
                "status": COMMAND_TASK_STATUS_COMPLETED,
                "progress": 100,
                "result": result.get("content"),
                "message": "数字伙伴执行完成",
                "metadata": {"run_id": run.run_id},
            },
            operator_id=operator_id,
            result=result,
            usage=result.get("usage"),
        )
        return result
    except TaskExecutionDeadlineExceeded:
        error = {
            "layer": "runtime",
            "code": "task_timeout",
            "message": "任务执行超过设定时限",
            "timeout_seconds": getattr(task, "timeout_seconds", 3600),
        }
        await finalize_run_and_task(
            run,
            task,
            COMMAND_RUN_STATUS_FAILED,
            {
                "status": COMMAND_TASK_STATUS_FAILED,
                "message": error["message"],
                "metadata": {"run_id": run.run_id, "code": "task_timeout", "timeout_seconds": error["timeout_seconds"]},
            },
            operator_id=operator_id,
            error=error,
        )
        return {"content": "", "run_id": run.run_id, "error": error}
    except Exception as exc:
        failure_layers = await _collect_failure_layers(run, exc)
        try:
            await _persist_failure_layers(run, failure_layers)
        except Exception as persist_exc:
            logger.warning("failure layer persistence skipped error_code=%s", type(persist_exc).__name__)
        primary = failure_layers.get("business") or failure_layers.get("runtime") or {}
        primary_message = primary.get("message") or "数字伙伴执行失败，请查看运行详情"
        try:
            from services.command_center.manager import record_task_event

            await record_task_event(
                task,
                "failure_aggregated",
                primary_message,
                operator_id,
                {"run_id": run.run_id, "failure_layers": failure_layers},
            )
        except Exception as event_exc:
            logger.warning("failure aggregation event skipped error_code=%s", type(event_exc).__name__)
        await finalize_run_and_task(
            run,
            task,
            COMMAND_RUN_STATUS_FAILED,
            {
                "status": COMMAND_TASK_STATUS_FAILED,
                "message": primary_message,
                "metadata": {"run_id": run.run_id, "failure_layers": failure_layers},
            },
            operator_id=operator_id,
            error={
                "layer": primary.get("layer") or "runtime",
                "code": primary.get("code") or "runtime_internal_error",
                "message": primary_message,
                "failure_layers": failure_layers,
            },
        )
        raise
    finally:
        if lease_heartbeat_task is not None:
            lease_heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_heartbeat_task


async def cancel_run(run: CommandTaskRun, operator_id: str | None = None, reason: str | None = None) -> CommandTaskRun:
    from services.command_center.manager import record_task_event

    task = await run.task
    await finalize_run_and_task(
        run,
        task,
        COMMAND_RUN_STATUS_CANCELLED,
        {"status": COMMAND_TASK_STATUS_CANCELLED, "message": reason or "运行已取消"},
        operator_id=operator_id,
        error={"message": reason or "cancelled"},
    )
    await record_task_event(task, "run_cancelled", reason or "运行已取消", operator_id, {"run_id": run.run_id})
    return run


async def handoff_run(run: CommandTaskRun, operator_id: str | None = None, reason: str | None = None) -> CommandTaskRun:
    from services.command_center.manager import record_task_event

    task = await run.task
    await finalize_run_and_task(
        run,
        task,
        COMMAND_RUN_STATUS_HANDED_OFF,
        {"status": COMMAND_TASK_STATUS_HANDED_OFF, "message": reason or "运行已人工接管"},
        operator_id=operator_id,
        error={"message": reason or "handed off"},
    )
    await record_task_event(task, "run_handed_off", reason or "运行已人工接管", operator_id, {"run_id": run.run_id})
    return run


async def retry_run(
    run: CommandTaskRun,
    operator_id: str | None = None,
    *,
    task_override: CommandTask | None = None,
    employee_override: CommandAgent | None = None,
) -> dict:
    if (task_override is None) != (employee_override is None):
        raise ValueError("retry task and employee overrides must be supplied together")
    if task_override is None:
        task, employee = await prepare_run_retry(run, operator_id)
    else:
        task, employee = task_override, employee_override
    if task.status != COMMAND_TASK_STATUS_DISPATCHED:
        raise RunRetryConflict("run retry was not prepared")
    retry_session_id = run.session_id or run.run_id
    return await execute_task_run(
        task,
        employee,
        operator_id=operator_id,
        session_id=retry_session_id,
        retried_from_run_id=run.run_id,
    )


async def prepare_run_retry(
    run: CommandTaskRun,
    operator_id: str | None = None,
) -> tuple[CommandTask, CommandAgent]:
    """Claim one failed task retry and restore its employee workload."""
    from services.command_center.manager import record_task_event
    from services.command_center.realtime import publish_command_center_update

    async with in_transaction() as connection:
        locked_task = (
            await CommandTask.filter(id=run.task_id, tenant_id=run.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        locked_run = (
            await CommandTaskRun.filter(id=run.id, tenant_id=run.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if (
            not locked_task
            or not locked_run
            or locked_task.status != COMMAND_TASK_STATUS_FAILED
            or locked_run.status != COMMAND_RUN_STATUS_FAILED
            or locked_task.run_id != locked_run.run_id
        ):
            raise RunRetryConflict("run is not retryable")

        employee = (
            await CommandAgent.filter(
                id=locked_run.employee_id,
                tenant_id=locked_run.tenant_id,
                deleted=False,
                enabled=True,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if (
            employee is None
            or employee.status in {"offline", "error"}
            or employee.load >= employee.max_concurrent
        ):
            raise RunRetryConflict("run employee is unavailable")

        locked_task.status = COMMAND_TASK_STATUS_DISPATCHED
        locked_task.progress = 0
        locked_task.run_id = None
        locked_task.result = None
        locked_task.started_at = None
        locked_task.finished_at = None
        await locked_task.save(
            using_db=connection,
            update_fields=[
                "status", "progress", "run_id", "result",
                "started_at", "finished_at", "utime",
            ],
        )
        employee.load += 1
        employee.current_task_id = locked_task.id
        if employee.load >= employee.max_concurrent:
            employee.status = "busy"
        await employee.save(
            using_db=connection,
            update_fields=["load", "current_task_id", "status"],
        )
        await record_task_event(
            locked_task,
            "task_retry_requested",
            "失败任务已进入重试",
            operator_id,
            {
                "retried_from_run_id": locked_run.run_id,
                "session_id": locked_run.session_id or locked_run.run_id,
            },
            using_db=connection,
        )

    original_task = await run.task
    for field in ("status", "progress", "run_id", "result", "started_at", "finished_at"):
        setattr(original_task, field, getattr(locked_task, field))
    await publish_command_center_update(locked_task.tenant_id, "task_retry_requested", locked_task.id)
    return locked_task, employee


def schedule_prepared_run_retry(
    run: CommandTaskRun,
    task: CommandTask,
    employee: CommandAgent,
    operator_id: str | None = None,
) -> None:
    async def _runner() -> None:
        try:
            await retry_run(
                run,
                operator_id=operator_id,
                task_override=task,
                employee_override=employee,
            )
        except Exception as exc:
            logger.warning(
                "command-center retry failed run_id=%s error_code=%s",
                run.run_id,
                type(exc).__name__,
            )
            failed_task = await CommandTask.get_or_none(
                id=task.id, tenant_id=task.tenant_id, deleted=False
            )
            if failed_task and failed_task.status == COMMAND_TASK_STATUS_DISPATCHED:
                from services.command_center.manager import update_task_progress

                await update_task_progress(
                    failed_task,
                    {
                        "status": COMMAND_TASK_STATUS_FAILED,
                        "message": "数字伙伴重试启动失败",
                        "metadata": {"retried_from_run_id": run.run_id},
                    },
                    operator_id,
                )

    asyncio.create_task(_runner())


async def recover_stale_runs(tenant_id: str | None = None, now: datetime | None = None) -> list[CommandTaskRun]:
    from services.command_center.manager import (
        apply_task_progress_in_transaction,
        complete_task_progress_after_commit,
        record_task_event,
    )

    current = now or _now()
    query = CommandTaskRun.filter(
        status__in=RECOVERABLE_RUN_STATUSES,
        lease_expires_at__lt=current,
        deleted=False,
    )
    if tenant_id:
        query = query.filter(tenant_id=tenant_id)
    stale_candidates = await query.all()
    recovered: list[CommandTaskRun] = []
    for candidate in stale_candidates:
        task_transition = None
        task = None
        async with in_transaction() as connection:
            run = (
                await CommandTaskRun.filter(
                    id=candidate.id,
                    status__in=RECOVERABLE_RUN_STATUSES,
                    lease_expires_at__lt=current,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update(skip_locked=True)
                .first()
            )
            if not run:
                continue
            task = (
                await CommandTask.filter(id=run.task_id, deleted=False)
                .using_db(connection)
                .first()
            )
            if task and task.status not in COMMAND_TASK_TERMINAL_STATUSES:
                task_transition = await apply_task_progress_in_transaction(
                    connection,
                    task,
                    {
                        "status": COMMAND_TASK_STATUS_FAILED,
                        "message": "数字伙伴运行租约过期",
                        "metadata": {"run_id": run.run_id},
                    },
                    None,
                    expected_run_id=run.run_id,
                )
            run.status = COMMAND_RUN_STATUS_STALE
            run.finished_at = current
            run.heartbeat_at = current
            run.error = {"message": "run lease expired"}
            await run.save(
                using_db=connection,
                update_fields=["status", "finished_at", "heartbeat_at", "error", "utime"],
            )
            if task:
                await record_task_event(
                    task,
                    "run_stale_recovered",
                    "运行租约过期，已标记 stale",
                    None,
                    {"run_id": run.run_id},
                    using_db=connection,
                )
            recovered.append(run)
        if task_transition is not None:
            await complete_task_progress_after_commit(
                task,
                task_transition,
                {"metadata": {"run_id": run.run_id}},
            )
    return recovered


async def recover_orphaned_dispatched_tasks(
    tenant_id: str | None = None,
    now: datetime | None = None,
) -> list[CommandTask]:
    """Fail dispatched tasks that never reached a live current run."""
    from settings import COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS
    from models.command_center import CommandApproval
    from services.command_center.approval_service import approval_resume_claim_is_active
    from services.command_center.manager import (
        apply_task_progress_in_transaction,
        complete_task_progress_after_commit,
    )

    current = now or _now()
    deadline = current - timedelta(seconds=COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS)
    query = CommandTask.filter(
        status=COMMAND_TASK_STATUS_DISPATCHED,
        deleted=False,
        utime__lt=deadline,
    )
    if tenant_id:
        query = query.filter(tenant_id=tenant_id)
    candidates = await query.all()
    recovered: list[CommandTask] = []

    for candidate in candidates:
        task_transition = None
        target_status = COMMAND_TASK_STATUS_FAILED
        run_ref = None
        if candidate.run_id:
            run_ref = await CommandTaskRun.filter(
                tenant_id=candidate.tenant_id,
                run_id=candidate.run_id,
                deleted=False,
            ).first()
        async with in_transaction() as connection:
            matching_approval = None
            if run_ref:
                matching_approval = (
                    await CommandApproval.filter(
                        tenant_id=candidate.tenant_id,
                        task_id=candidate.id,
                        run_id=run_ref.id,
                        status__in={"approved", "approved_read_only"},
                        deleted=False,
                    )
                    .using_db(connection)
                    .select_for_update()
                    .order_by("-ctime")
                    .first()
                )
            current_run = None
            if run_ref:
                current_run = (
                    await CommandTaskRun.filter(
                        id=run_ref.id,
                        tenant_id=candidate.tenant_id,
                        run_id=candidate.run_id,
                        deleted=False,
                    )
                    .using_db(connection)
                    .select_for_update()
                    .first()
                )
            locked_task = (
                await CommandTask.filter(
                    id=candidate.id,
                    tenant_id=candidate.tenant_id,
                    status=COMMAND_TASK_STATUS_DISPATCHED,
                    deleted=False,
                    utime__lt=deadline,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not locked_task or locked_task.run_id != candidate.run_id:
                continue
            if current_run and current_run.status in CURRENT_RUN_STATUSES:
                continue
            current_run_status = current_run.status if current_run else None
            if (
                current_run_status == COMMAND_RUN_STATUS_CONTINUATION_READY
                and matching_approval
                and approval_resume_claim_is_active(matching_approval, current)
            ):
                continue
            if current_run:
                if current_run_status == COMMAND_RUN_STATUS_COMPLETED:
                    target_status = COMMAND_TASK_STATUS_COMPLETED
                elif current_run_status in {COMMAND_RUN_STATUS_CANCELLED, COMMAND_RUN_STATUS_REJECTED}:
                    target_status = COMMAND_TASK_STATUS_CANCELLED
                elif current_run_status == COMMAND_RUN_STATUS_HANDED_OFF:
                    target_status = COMMAND_TASK_STATUS_HANDED_OFF

            task_transition = await apply_task_progress_in_transaction(
                connection,
                locked_task,
                {
                    "status": target_status,
                    "message": "任务未进入有效运行，已自动恢复终态",
                    "metadata": {
                        "reason": "orphaned_dispatched_task",
                        "run_id": locked_task.run_id,
                        "run_status": current_run_status,
                        "timeout_seconds": COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS,
                    },
                },
                None,
                locked_task=locked_task,
            )
            if current_run and current_run_status not in COMMAND_RUN_TERMINAL_STATUSES:
                current_run.status = COMMAND_RUN_STATUS_FAILED
                current_run.finished_at = current
                current_run.heartbeat_at = current
                current_run.lease_owner = None
                current_run.lease_expires_at = current
                current_run.error = {
                    **(getattr(current_run, "error", None) if isinstance(getattr(current_run, "error", None), dict) else {}),
                    "message": "task did not enter a valid continuation run",
                    "reason": "orphaned_dispatched_task",
                }
                await current_run.save(
                    using_db=connection,
                    update_fields=[
                        "status",
                        "finished_at",
                        "heartbeat_at",
                        "lease_owner",
                        "lease_expires_at",
                        "error",
                        "utime",
                    ],
                )
            recovered.append(locked_task)
        if task_transition is not None:
            await complete_task_progress_after_commit(
                candidate,
                task_transition,
                {"metadata": {"run_id": candidate.run_id}},
            )
    return recovered
