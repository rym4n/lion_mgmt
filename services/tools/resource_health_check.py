# coding=utf-8
"""Ephemeral connection testing for tool resources.

This module deliberately keeps the submitted resource values out of Kafka and
run logs. The action runner reads them once from a short-lived Redis resource
entry, exactly as it does for a saved resource.
"""

import asyncio
import json
import math
import uuid

from aiocache import caches

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import Runlog, Tool, ToolAction, ToolResourcesModel
from settings import TASK_TOPIC


_TEMP_RESOURCE_TTL = 120
_POLL_SECONDS = 60
_TERMINAL_STATUSES = {-1, 0, 3}


def _param_error():
    # Keep all validation errors on the existing public error contract.
    raise CustomError(ErrorCode.PARAM_ERROR)


async def _health_check_action(tool_name, version, tenant_id):
    filters = {
        "tool_name": tool_name,
        "version_number": version,
        "deleted": False,
    }
    filters["tenant_id"] = tenant_id
    actions = await ToolAction.filter(**filters).all()
    for action in actions:
        value = action.action if isinstance(action.action, dict) else {}
        if value.get("func") == "health_check":
            return value
    return None


async def health_check_capability(tool_name, tenant_id, maintenance_mode):
    if not isinstance(tool_name, str) or not tool_name.strip():
        _param_error()
    tool = await Tool.filter(
        name=tool_name,
        deleted=False,
        maintain=maintenance_mode,
        tenant_id=tenant_id,
    ).order_by("-ctime").first()
    if not tool:
        return {"supported": False, "version": ""}
    supported = await _health_check_action(tool.name, tool.version, tenant_id) is not None
    return {"supported": supported, "version": tool.version if supported else ""}


async def resolve_health_check(tool_name, version, tenant_id, maintenance_mode):
    filters = {
        "name": tool_name,
        "deleted": False,
        "maintain": maintenance_mode,
        "tenant_id": tenant_id,
    }
    if version:
        filters["version"] = version
        tool = await Tool.get_or_none(**filters)
    else:
        tool = await Tool.filter(**filters).order_by("-ctime").first()
    if not tool:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)
    action = await _health_check_action(tool.name, tool.version, tenant_id)
    if not action:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)
    return tool, action


async def _resource_schema(tool_name, tenant_id, maintenance_mode):
    return await ToolResourcesModel.filter(
        tool_name=tool_name,
        tenant_id=tenant_id,
        internal=maintenance_mode,
        deleted=False,
    ).order_by("-ctime").first()


def _validate_value(field, value):
    constraints = field.get("constraints") if isinstance(field.get("constraints"), dict) else {}
    data_type = field.get("data_type") or field.get("type") or constraints.get("data_type") or "string"
    if value is None or (isinstance(value, str) and not value.strip()):
        if field.get("required"):
            _param_error()
        return value if value is not None else ""
    if data_type in {"integer", "long", "number", "float", "double"}:
        if isinstance(value, str):
            try:
                value = float(value) if data_type in {"number", "float", "double"} else int(value)
            except (ValueError, OverflowError):
                _param_error()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _param_error()
        if isinstance(value, float) and not math.isfinite(value):
            _param_error()
        if data_type in {"integer", "long"} and isinstance(value, float) and not value.is_integer():
            _param_error()
        minimum = field.get("minimum", field.get("min", field.get("min_value", constraints.get("minimum", constraints.get("min")))))
        maximum = field.get("maximum", field.get("max", field.get("max_value", constraints.get("maximum", constraints.get("max")))))
        try:
            if minimum is not None and value < float(minimum):
                _param_error()
            if maximum is not None and value > float(maximum):
                _param_error()
        except (TypeError, ValueError, OverflowError):
            _param_error()
    if data_type == "boolean" and not isinstance(value, bool):
        _param_error()
    if data_type in {"string", "text", "password"} and not isinstance(value, str):
        _param_error()
    max_length = field.get("maxLength", field.get("max_length", constraints.get("maxLength", constraints.get("max_length"))))
    if max_length is not None and isinstance(value, str) and len(value) > int(max_length):
        _param_error()
    return value


def resource_values(schema, submitted, existing=None):
    """Build runtime values from the server-owned schema without mutation."""
    fields = schema.resources if hasattr(schema, "resources") else schema
    if not isinstance(fields, dict) or not isinstance(submitted, dict):
        _param_error()
    unknown = set(submitted) - set(fields)
    if unknown:
        _param_error()
    existing = existing if isinstance(existing, dict) else {}
    values = {}
    for key, field in fields.items():
        if not isinstance(field, dict):
            _param_error()
        entry = submitted.get(key)
        if entry is not None and not isinstance(entry, dict):
            _param_error()
        value = entry["value"] if entry is not None and "value" in entry else field.get("default_value", "")
        if value == "******" and (field.get("data_type") or field.get("type")) == "password":
            old = existing.get(key)
            if isinstance(old, dict) and "value" in old:
                value = old.get("value")
            else:
                _param_error()
        values[key] = _validate_value(field, value)
    return values


def health_check_succeeded(status, result):
    if status != 0:
        return False
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            # Preserve legacy actions that return a plain success message.
            text = result.strip()
            return bool(text) and text.lower() not in {
                "false", "none", "null", "failed", "failure", "error",
                "unhealthy", "unavailable", "执行中",
            }
    if result is None or result is False:
        return False
    if not isinstance(result, dict):
        return bool(result)
    containers = [(result, 0)]
    observed = False
    for container, depth in containers:
        if "success" in container:
            if container["success"] is not True:
                return False
            observed = True
        for key in ("code", "status", "status_code", "statusCode", "return_code", "errcode", "error_code", "errorCode"):
            value = container.get(key)
            if value is False:
                return False
            if value is not None and value != "":
                if value is not True and value not in (0, 200, "0", "200", "ok", "OK", "success", "SUCCESS", "healthy", "available"):
                    return False
                observed = True
        if depth < 4:
            for key in ("data", "result", "summary", "output"):
                if container.get(key) is False:
                    return False
                if isinstance(container.get(key), dict):
                    containers.append((container[key], depth + 1))
    return observed


async def execute_health_check(*, tool, action, values, name, engine, tenant_id, executor):
    run_id = gen_run_id()
    temp_id = str(uuid.uuid4())
    cache_key = f"resource_{temp_id}"
    cache = caches.get("redis_chat")
    cached_values = dict(values)
    cached_values[f"{temp_id}_name"] = name or "临时测试资源"
    payload = {
        "run_type": "action",
        "run_id": run_id,
        "tenant_id": tenant_id,
        "run_args": {
            "resource": temp_id,
            "node_name": tool.description or tool.name,
            "action": "health_check",
            "action_name": action.get("name") or "健康检查",
            "app_name": tool.name,
            "name": tool.description or tool.name,
            "app_ver": tool.version,
            "main": tool.main,
            "action_input": {},
            "action_engine": engine,
            "retry_times": 0,
            "timeout": 60,
            "executor": executor,
        },
    }
    try:
        async with asyncio.timeout(_POLL_SECONDS):
            await cache.set(cache_key, cached_values, _TEMP_RESOURCE_TTL)
            await aproduce(TASK_TOPIC, payload, raise_on_error=True)
            for _ in range(_POLL_SECONDS):
                log = await Runlog.filter(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    deleted=False,
                    node_type="action-node",
                    tool_name=tool.name,
                    tool_version=tool.version,
                    action_name="health_check",
                ).order_by("-utime", "-ctime", "-id").first()
                if log and log.status in _TERMINAL_STATUSES:
                    return {"success": health_check_succeeded(log.status, log.result), "run_id": run_id}
                await asyncio.sleep(1)
        raise CustomError(ErrorCode.ACTION_TIMEOUT)
    except TimeoutError as exc:
        raise CustomError(ErrorCode.ACTION_TIMEOUT) from exc
    except CustomError:
        raise
    except Exception:
        return {"success": False, "run_id": run_id}
    finally:
        try:
            await cache.delete(cache_key)
        except Exception:
            # The Redis TTL remains a cleanup fallback during an outage.
            pass
