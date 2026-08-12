# coding=utf-8

import asyncio
import ast

import orjson

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import Runlog
from settings import TASK_TOPIC


def extract_config_actions(config):
    """
    兼容巡检自定义报告动作配置结构，提取动作列表。
    支持：
    1. [{"func": "...", "input": [...]}]
    2. {"callback_data": [{"func": "..."}]}
    3. {"actions": [{"func": "..."}]}
    4. {"func": "...", "action_input": {...}} 单动作配置
    """
    if not config:
        return []

    if isinstance(config, list):
        return [item for item in config if isinstance(item, dict)]

    if isinstance(config, dict):
        actions = config.get("callback_data") or config.get("actions")
        if isinstance(actions, list):
            return [item for item in actions if isinstance(item, dict)]
        if any(key in config for key in ("func", "action", "tool_name", "app_name", "input", "action_input")):
            return [config]

    return []


def get_action_value(action, *keys, default=""):
    for key in keys:
        value = action.get(key)
        if value not in (None, ""):
            return value
    return default


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_account_id(account_info):
    if isinstance(account_info, dict):
        return account_info.get("id")
    return getattr(account_info, "id", account_info)


def _build_action_input(action, override_action_input=None):
    if override_action_input is not None:
        if not isinstance(override_action_input, dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
        return override_action_input

    if isinstance(action.get("action_input"), dict):
        return action["action_input"]

    data = {}
    for input_data in action.get("input", []):
        if not isinstance(input_data, dict):
            continue
        key = input_data.get("key")
        if not key:
            continue
        data[key] = input_data.get("value", "")
    return data


def _parse_action_result(raw_result):
    if raw_result in (None, ""):
        return raw_result

    if isinstance(raw_result, (bytes, bytearray)):
        raw_result = raw_result.decode()

    if isinstance(raw_result, str):
        try:
            return orjson.loads(raw_result)
        except orjson.JSONDecodeError:
            try:
                return ast.literal_eval(raw_result)
            except (ValueError, SyntaxError):
                return raw_result

    return raw_result


async def execute_action(action, request, tenant_id, override_action_input=None):
    action_input = _build_action_input(action, override_action_input)
    params = {
        "resource": get_action_value(action, "resource"),
        "node_name": get_action_value(action, "node_name", "description", "name"),
        "action": get_action_value(action, "action", "func"),
        "action_name": get_action_value(action, "action_name", "name"),
        "app_name": get_action_value(action, "app_name", "tool_name"),
        "name": get_action_value(action, "name", "description"),
        "app_ver": get_action_value(action, "app_ver", "version"),
        "main": get_action_value(action, "main", "tool_main"),
        "action_input": action_input,
        "retry_times": _safe_int(action.get("retry_times"), 3),
        "timeout": _safe_int(action.get("timeout"), 300),
        "executor": _get_account_id(request.ctx.session.get("account_info")),
    }

    run_id = gen_run_id()
    msg = {"run_type": "action", "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    await aproduce(TASK_TOPIC, msg)

    wait_seconds = max(1, min(_safe_int(action.get("timeout"), 300), 300))
    for _ in range(wait_seconds):
        log = await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).order_by("-utime").first()
        if log and log.result and log.result != "执行中":
            return {"run_id": run_id, "status": log.status, "result": _parse_action_result(log.result)}
        if log and log.status in (-1, 2):
            return {"run_id": run_id, "status": log.status, "result": _parse_action_result(log.result)}
        await asyncio.sleep(1)

    raise CustomError(ErrorCode.ACTION_TIMEOUT)
