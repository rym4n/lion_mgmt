# coding=utf-8
"""报告取数核心：与文档格式（docx / HTML）无关的占位符解析与动作执行。"""

import asyncio
import re
from collections.abc import Callable

import orjson
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import check_action_execute_permission
from libs.utils import gen_run_id
from models import Account
from models.runlog import Runlog
from settings import TASK_TOPIC

FETCH_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(\.[^}]+)?\}")
MISSING_FETCH_VALUE = object()
LEGACY_WHOLE_RESULT_PATH = ".result"


def get_fetch_value_by_path(data, path: str):
    if not path:
        return data
    for part in path.lstrip(".").split("."):
        if isinstance(data, dict):
            if part not in data:
                return MISSING_FETCH_VALUE
            data = data[part]
        elif isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError):
                return MISSING_FETCH_VALUE
        else:
            return MISSING_FETCH_VALUE
    return data


def get_fetch_value_for_placeholder(data, path: str):
    """Resolve a configured path, including the legacy default whole-result path.

    The report editor historically required ``result`` even when users wanted the
    complete action payload. Action responses are unwrapped from ``data`` before
    path lookup, so a missing top-level ``result`` in that legacy case means the
    whole unwrapped payload.
    """
    value = get_fetch_value_by_path(data, path)
    if value is MISSING_FETCH_VALUE and path == LEGACY_WHOLE_RESULT_PATH:
        return data
    return value


def stringify_fetch(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return orjson.dumps(value).decode()
    return str(value)


def parse_fetch_result(raw: str):
    if not raw:
        return raw
    try:
        parsed = orjson.loads(raw)
        if isinstance(parsed, dict) and "data" in parsed:
            return parsed["data"]
        return parsed
    except Exception:
        return raw


async def execute_fetch_action(action_cfg: dict, tenant_id: str, operator_id) -> str:
    if not isinstance(action_cfg, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if isinstance(operator_id, Account):
        operator = operator_id
    else:
        operator_key = str(operator_id or "").strip()
        if not operator_key:
            raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
        operator = await Account.get_or_none(id=operator_key, deleted=False).prefetch_related("roles")
    if not operator:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

    app_name = action_cfg.get("app_name") or action_cfg.get("tool_name") or ""
    action_name = action_cfg.get("action") or action_cfg.get("func") or ""
    resource = action_cfg.get("resource") or ""
    if not await check_action_execute_permission(
        operator,
        app_name,
        action_name,
        resource,
        tenant_id,
        False,
        action_cfg.get("app_ver") or action_cfg.get("version"),
    ):
        logger.warning(
            "拒绝无动作执行权的报告取数配置 tenant_id=%s operator_id=%s app_name=%s action=%s resource=%s",
            tenant_id,
            operator.id,
            app_name,
            action_name,
            resource,
        )
        raise CustomError(ErrorCode.UNAUTH)

    params = {
        "resource": action_cfg.get("resource", ""),
        "node_name": action_cfg.get("node_name") or action_cfg.get("description", ""),
        "action": action_cfg.get("action") or action_cfg.get("func", ""),
        "action_name": action_cfg.get("action_name") or action_cfg.get("name", ""),
        "app_name": action_cfg.get("app_name") or action_cfg.get("tool_name", ""),
        "name": action_cfg.get("name") or action_cfg.get("description", ""),
        "app_ver": action_cfg.get("app_ver") or action_cfg.get("version", ""),
        "main": action_cfg.get("main") or action_cfg.get("tool_main", ""),
        "action_input": action_cfg.get("action_input") or {},
        "retry_times": int(action_cfg.get("retry_times") or 3),
        "timeout": int(action_cfg.get("timeout") or 300),
        "executor": operator.id,
    }
    run_id = gen_run_id()
    await aproduce(TASK_TOPIC, {"run_type": "action", "run_id": run_id, "run_args": params, "tenant_id": tenant_id})

    wait_seconds = max(1, min(int(action_cfg.get("timeout") or 300), 300))
    for _ in range(wait_seconds):
        log = await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).order_by("-utime").first()
        if log and log.status in (-1, 2):
            logger.error("报告取数动作执行失败 run_id=%s status=%s", run_id, log.status)
            raise RuntimeError(f"report fetch action failed run_id={run_id}")
        if log and log.result and log.result != "执行中":
            return log.result or ""
        await asyncio.sleep(1)

    logger.warning(f"取数动作执行超时 run_id={run_id}")
    raise CustomError(ErrorCode.ACTION_TIMEOUT)


async def resolve_fetch_values(
    text: str,
    fetch_config: dict,
    tenant_id: str,
    operator_id,
) -> dict:
    """扫描 text 中的占位符，并发执行取数动作，返回 {配置键: 已解析结果}。

    无占位符时返回 {}；缺少动作配置、动作执行失败、结果路径不存在均抛 CustomError。
    """
    matches = list(FETCH_PLACEHOLDER_RE.finditer(text or ""))
    placeholder_keys = {match.group(1) for match in matches}
    if not placeholder_keys:
        return {}

    config = fetch_config if isinstance(fetch_config, dict) else {}
    missing_configs = sorted(placeholder_keys - set(config.keys()))
    if missing_configs:
        logger.error("报告取数占位符缺少动作配置 keys=%s", missing_configs)
        raise CustomError(ErrorCode.SERVER_ERROR)

    active_keys = sorted(placeholder_keys)
    fetched = await asyncio.gather(
        *(execute_fetch_action(config[key], tenant_id, operator_id) for key in active_keys),
        return_exceptions=True,
    )

    results: dict = {}
    failures: dict[str, Exception] = {}
    for key, raw in zip(active_keys, fetched):
        if isinstance(raw, Exception):
            logger.warning(f"取数动作 {key} 执行异常: {raw}")
            failures[key] = raw
        else:
            results[key] = parse_fetch_result(raw)

    if failures:
        logger.error("报告取数失败 keys=%s", sorted(failures))
        for key in sorted(failures):
            if isinstance(failures[key], CustomError):
                raise failures[key]
        raise CustomError(ErrorCode.SERVER_ERROR)

    missing_paths = sorted(
        {
            match.group(0)
            for match in matches
            if match.group(1) in results
            and get_fetch_value_for_placeholder(
                results[match.group(1)], match.group(2) or ""
            )
            is MISSING_FETCH_VALUE
        }
    )
    if missing_paths:
        logger.error("报告取数结果路径不存在 placeholders=%s", missing_paths)
        raise CustomError(ErrorCode.SERVER_ERROR)

    return results


def build_placeholder_replacer(results: dict) -> Callable[[re.Match], str | None]:
    """返回用于 re.sub / replace_paragraph_text_matches 的替换函数。"""

    def _replace(match: re.Match) -> str | None:
        key = match.group(1)
        if key not in results:
            return None
        return stringify_fetch(
            get_fetch_value_for_placeholder(results[key], match.group(2) or "")
        )

    return _replace
