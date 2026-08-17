#!/usr/bin/env python
# coding=utf-8

"""
巡检模块API路由层
Inspection Module API Routes Layer
"""

import ast
import asyncio
import orjson
import os
import re

from datetime import datetime
from sanic import Blueprint, Request
from sanic.log import logger
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.maintenance import DEFAULT_TENANT_ID
from core.response import response_ok
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.kafka import aproduce
from libs.utils import gen_run_id, upload_to_s3
from models import Timed, Playbook, PlaybookVersion, InspectionTarget, InspectionCategory, InspectionResult, Runlog
from settings import TASK_TOPIC
from . import logic
from sanic.response import raw

inspection_bp = Blueprint("inspection", url_prefix="/inspection")
__all__ = ["inspection_bp"]

CUSTOM_REPORT_PLACEHOLDER = "${自定义报告}"
INSPECTION_RESULT_ALL_OUTPUT_REFS = ("all", "全部", "所有", "全部巡检项", "所有巡检项", "*")
AUTOMATION_INSPECTION_PLAYBOOK_NAME = "自动化巡检"


def _resolve_authenticated_tenant_id(request: Request, data: dict) -> str:
    tenant_id = request.ctx.session.get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.SESSION_INVALID)

    requested_tenant_id = data.get("tenant_id")
    if requested_tenant_id is not None and requested_tenant_id != tenant_id:
        raise CustomError(ErrorCode.FORBIDDEN)
    return tenant_id


async def _get_automation_inspection_playbook():
    playbook = await Playbook.filter(
        name=AUTOMATION_INSPECTION_PLAYBOOK_NAME,
        deleted=False,
        tenant_id=DEFAULT_TENANT_ID,
        internal=True,
    ).first()
    if not playbook:
        return None, None

    playbook_version = await PlaybookVersion.filter(
        playbook_id=playbook.id,
        tenant_id=DEFAULT_TENANT_ID,
        deleted=False,
        status=1,
    ).order_by("-utime").first()
    return playbook, playbook_version


def _serialize_category(category):
    category_type = logic.normalize_category_type(getattr(category, "category_type", None))
    return {
        "id": category.id,
        "name": category.name,
        "schedule_id": category.schedule_id,
        "app_name": category.app_name,
        "category_type": category_type,
        "resource_ids": category.resource_ids,
        "enabled": category.enabled,
        "ctime": category.ctime.isoformat() if category.ctime else None,
        "utime": category.utime.isoformat() if category.utime else None,
    }


def _serialize_target(target, app_name=None, category_type=None):
    data = {
        "id": target.id,
        "content": target.content,
        "category_id": target.category_id,
        "standard": target.standard,
        "judgment_rule": target.judgment_rule,
        "action_name": target.action_name,
        "version": target.version,
        "action_input": target.action_input or {},
        "enabled": target.enabled,
        "ctime": target.ctime.isoformat() if target.ctime else None,
        "utime": target.utime.isoformat() if target.utime else None,
    }
    if app_name is not None:
        data["app_name"] = app_name
    if category_type is not None:
        normalized_type = logic.normalize_category_type(category_type)
        data["category_type"] = normalized_type
    return data


def _normalize_action_input(action_input):
    """
    规范化巡检项动作输入参数配置。
    前端可传 JSON 对象，也兼容传入 JSON 字符串；未传时按空配置处理。
    """
    if action_input in (None, ""):
        return {}

    if isinstance(action_input, str):
        try:
            action_input = orjson.loads(action_input)
        except orjson.JSONDecodeError:
            try:
                action_input = ast.literal_eval(action_input)
            except (ValueError, SyntaxError):
                raise CustomError(ErrorCode.PARAM_ERROR)

    if not isinstance(action_input, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)

    return action_input


def _extract_config_actions(config):
    """
    兼容通知配置结构，提取动作列表。
    支持：
    1. [{"func": "...", "input": [...]}]
    2. {"callback_data": [{"func": "..."}]}
    3. {"actions": [{"func": "..."}]}
    4. {"func": "...", "input": [...]} 单动作配置
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


def _replace_builtin_placeholders(value, replacements):
    if not isinstance(value, str):
        return value

    for placeholder, replacement in replacements.items():
        if placeholder in value:
            value = value.replace(placeholder, replacement or "")

    return value


def _serialize_inspection_result_reference(result_data):
    if result_data in (None, ""):
        return ""
    return orjson.dumps(result_data, option=orjson.OPT_NON_STR_KEYS).decode()


def _build_inspection_result_context(created_result_items):
    """
    构建本次 /result/add 的巡检结果引用上下文。
    """
    results = []
    by_target_id = {}

    for item in created_result_items:
        if not isinstance(item, dict):
            continue

        output = item.get("output")
        result_info = {
            "id": item.get("id"),
            "target_id": item.get("target_id"),
            "schedule_id": item.get("schedule_id"),
            "status": item.get("status"),
            "execution_status": item.get("execution_status"),
            "output": output,
            "result": output,
            "error_message": item.get("error_message"),
            "duration": item.get("duration"),
        }
        results.append(result_info)

        if item.get("target_id"):
            by_target_id[item["target_id"]] = result_info

    return {"all": results, "by_target_id": by_target_id}


def _get_value_by_path(data, path):
    current = data
    for part in path:
        if part == "":
            continue

        if isinstance(current, str):
            try:
                current = orjson.loads(current)
            except orjson.JSONDecodeError:
                try:
                    current = ast.literal_eval(current)
                except (ValueError, SyntaxError):
                    return ""

        if isinstance(current, dict):
            if part not in current:
                return ""
            current = current[part]
            continue

        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return ""
            continue

        return ""

    return current


def _parse_inspection_field_path(field_path):
    if not field_path:
        return []

    raw_parts = []
    if isinstance(field_path, str):
        raw_parts = [part.strip() for part in field_path.split(".") if part.strip()]
    elif isinstance(field_path, (list, tuple)):
        for part in field_path:
            if part in (None, ""):
                continue
            if isinstance(part, str):
                raw_parts.extend(item.strip() for item in part.split(".") if item.strip())
            else:
                raw_parts.append(str(part))
    else:
        return []

    parsed_parts = []
    for part in raw_parts:
        matches = list(re.finditer(r"([^\[\]]+)|\[(\d+)\]", part))
        if matches and "".join(match.group(0) for match in matches) == part:
            for match in matches:
                parsed_parts.append((match.group(1) or match.group(2)).strip())
        else:
            parsed_parts.append(part)

    return parsed_parts


def _get_inspection_output_reference_value(reference, field_path, result_context, stringify=True):
    if reference in INSPECTION_RESULT_ALL_OUTPUT_REFS:
        if field_path:
            return ""
        all_outputs = [item.get("output") for item in result_context.get("all", [])]
        return _serialize_inspection_result_reference(all_outputs) if stringify else all_outputs

    target_result = result_context.get("by_target_id", {}).get(reference)
    if not target_result:
        return ""

    output = target_result.get("output")
    parsed_field_path = _parse_inspection_field_path(field_path)
    value = _get_value_by_path(output, parsed_field_path) if parsed_field_path else output
    return _serialize_inspection_result_reference(value) if stringify else value


def _replace_inspection_result_placeholders(value, result_context):
    if not isinstance(value, str):
        return value

    output_pattern = r"\$\{([^}.]+)\.output(?:\.([^}]+))?\}"

    def replace_output_ref(match):
        reference = match.group(1).strip()
        field_path = _parse_inspection_field_path(match.group(2) or "")
        return _stringify_action_result(_get_inspection_output_reference_value(reference, field_path, result_context, stringify=False))

    value = re.sub(output_pattern, replace_output_ref, value)

    shorthand_pattern = r"\$\{([^}.]+)\.(?!output(?:\.|}))(.*?)\}"

    def replace_shorthand_ref(match):
        reference = match.group(1).strip()
        if reference not in result_context.get("by_target_id", {}):
            return match.group(0)
        field_path = _parse_inspection_field_path(match.group(2) or "")
        return _stringify_action_result(_get_inspection_output_reference_value(reference, field_path, result_context, stringify=False))

    return re.sub(shorthand_pattern, replace_shorthand_ref, value)


def _replace_action_input_placeholders(value, replacements, result_context=None):
    if isinstance(value, dict):
        return {key: _replace_action_input_placeholders(item_value, replacements, result_context) for key, item_value in value.items()}

    if isinstance(value, list):
        return [_replace_action_input_placeholders(item_value, replacements, result_context) for item_value in value]

    if result_context:
        value = _replace_inspection_result_placeholders(value, result_context)

    return _replace_builtin_placeholders(value, replacements)


def _stringify_action_result(value):
    if value in (None, ""):
        return ""

    if isinstance(value, (dict, list)):
        return orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS).decode()

    return str(value)


def _custom_report_result_to_text(raw_result):
    if raw_result in (None, "", "执行中"):
        return ""

    parsed_result = raw_result
    if isinstance(raw_result, (bytes, bytearray)):
        raw_result = raw_result.decode()

    if isinstance(raw_result, str):
        try:
            parsed_result = orjson.loads(raw_result)
        except orjson.JSONDecodeError:
            try:
                parsed_result = ast.literal_eval(raw_result)
            except (ValueError, SyntaxError):
                return raw_result

    if isinstance(parsed_result, dict):
        if parsed_result.get("code") == 500:
            return _stringify_action_result(parsed_result)

        data = parsed_result.get("data", parsed_result)
        if isinstance(data, dict):
            for key in ("download_link", "url", "file_url", "file_id", "result", "content", "message"):
                if data.get(key) not in (None, ""):
                    return _stringify_action_result(data.get(key))
        return _stringify_action_result(data)

    return _stringify_action_result(parsed_result)


def _get_account_id(account_info):
    if isinstance(account_info, dict):
        return account_info.get("id")
    return getattr(account_info, "id", account_info)


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_action_input(action, replacements, result_context=None):
    if isinstance(action.get("action_input"), dict):
        return _replace_action_input_placeholders(action.get("action_input"), replacements, result_context)

    data = {}
    for input_data in action.get("input", []):
        if not isinstance(input_data, dict):
            continue

        key = input_data.get("key")
        if not key:
            continue

        if input_data.get("source") == "inspection_result":
            reference = input_data.get("target_id") or input_data.get("target") or "all"
            field_path = input_data.get("field_path") or input_data.get("path") or []
            field_path = _parse_inspection_field_path(field_path)
            data[key] = _get_inspection_output_reference_value(reference, field_path, result_context or {}, stringify=False)
            continue

        data[key] = _replace_action_input_placeholders(input_data.get("value", ""), replacements, result_context)

    return data


def _get_action_value(action, *keys, default=""):
    for key in keys:
        value = action.get(key)
        if value not in (None, ""):
            return value
    return default


async def _execute_custom_report_action(action, action_input, request, tenant_id):
    account_id = _get_account_id(request.ctx.session.get("account_info"))
    params = {
        "resource": _get_action_value(action, "resource"),
        "node_name": _get_action_value(action, "node_name", "description", "name"),
        "action": _get_action_value(action, "action", "func"),
        "action_name": _get_action_value(action, "action_name", "name"),
        "app_name": _get_action_value(action, "app_name", "tool_name"),
        "name": _get_action_value(action, "name", "description"),
        "app_ver": _get_action_value(action, "app_ver", "version"),
        "main": _get_action_value(action, "main", "tool_main"),
        "action_input": action_input,
        "retry_times": _safe_int(action.get("retry_times"), 3),
        "timeout": _safe_int(action.get("timeout"), 300),
        "executor": account_id,
    }

    run_id = gen_run_id()
    msg = {"run_type": "action", "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    await aproduce(TASK_TOPIC, msg)

    wait_seconds = max(1, min(_safe_int(action.get("timeout"), 300), 300))
    for _ in range(wait_seconds):
        log = await Runlog.filter(run_id=run_id, deleted=False).order_by("-utime").first()
        if log and log.result and log.result != "执行中":
            return _custom_report_result_to_text(log.result)
        if log and log.status in (-1, 2):
            return _custom_report_result_to_text(log.result)
        await asyncio.sleep(1)

    logger.warning(f"自定义报告动作执行超时: {action.get('name', '')}, run_id={run_id}")
    return ""


async def _generate_custom_report_content(schedule, request, tenant_id, replacements, result_context):
    if not schedule or not schedule.custom_report_config:
        return ""

    report_actions = _extract_config_actions(schedule.custom_report_config)
    if not report_actions:
        return ""

    report_contents = []
    for action in report_actions:
        try:
            action_input = _build_action_input(action, replacements, result_context)
            report_content = await _execute_custom_report_action(action, action_input, request, tenant_id)
            if report_content:
                report_contents.append(report_content)
        except Exception as e:
            logger.warning(f"执行自定义报告动作失败: {action.get('name', '')}, error={e}")

    return "\n".join(report_contents)


def _actions_contain_custom_report_placeholder(actions):
    for action in actions:
        for input_data in action.get("input", []):
            if isinstance(input_data, dict) and CUSTOM_REPORT_PLACEHOLDER in str(input_data.get("value", "")):
                return True
    return False


# ==================== Schedule Management ====================


@inspection_bp.post("/schedule/save")
@openapi.definition(
    summary="创建或更新巡检任务",
    description="创建或更新巡检任务，如果提供id则更新，否则创建新的",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_cron", "name"],
                "properties": {
                    "id": {"type": "string", "description": "巡检任务ID（更新时必填）"},
                    "schedule_cron": {"type": "string", "description": "定时周期(Cron表达式)"},
                    "name": {"type": "string", "description": "任务名称"},
                    "owner": {"type": "string", "description": "负责人"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "description": {"type": "string", "description": "任务描述"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "保存成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "schedule_cron": {"type": "string"},
                                    "owner": {"type": "string"},
                                    "enabled": {"type": "boolean"},
                                    "description": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def save_schedule(request: Request):
    """
    创建或更新巡检调度配置
    Create or update inspection schedule configuration
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    # 验证必需字段
    if not data.get("schedule_cron") or not data.get("name"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule = await logic.save_schedule(data, tenant_id)

    return response_ok(
        data={
            "id": schedule.id,
            "name": schedule.name,
            "schedule_cron": schedule.schedule_cron,
            "owner": schedule.owner,
            "enabled": schedule.enabled,
            "description": schedule.description,
            "ctime": schedule.ctime.isoformat() if schedule.ctime else None,
            "utime": schedule.utime.isoformat() if schedule.utime else None,
        }
    )


@inspection_bp.post("/schedule/get")
@openapi.definition(
    summary="获取巡检任务配置",
    description="根据ID获取巡检任务配置",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "巡检任务ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "schedule_cron": {"type": "string"},
                                    "owner": {"type": "string"},
                                    "enabled": {"type": "boolean"},
                                    "description": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def get_schedule(request: Request):
    """
    获取巡检任务配置
    Get inspection schedule configuration
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule = await logic.get_schedule_by_id(data["id"], tenant_id)
    if not schedule:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(
        data={
            "id": schedule.id,
            "name": schedule.name,
            "schedule_cron": schedule.schedule_cron,
            "owner": schedule.owner,
            "enabled": schedule.enabled,
            "description": schedule.description,
            "ctime": schedule.ctime.isoformat() if schedule.ctime else None,
            "utime": schedule.utime.isoformat() if schedule.utime else None,
        }
    )


@inspection_bp.post("/schedule/list")
@openapi.definition(
    summary="获取巡检任务列表",
    description="获取租户下所有巡检任务配置列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "page_size": {"type": "integer", "description": "每页数量"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "keyword": {"type": "string", "description": "搜索关键词"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "total": {"type": "integer"},
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "schedule_cron": {"type": "string"},
                                                "owner": {"type": "string"},
                                                "enabled": {"type": "boolean"},
                                                "description": {"type": "string"},
                                                "result_dates": {"type": "array", "items": {"type": "string"}, "description": "存在巡检结果的日期列表"},
                                                "custom_report_config": {"type": "object"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def list_schedules(request: Request):
    """
    获取巡检任务列表
    Get inspection schedule list
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    enabled = data.get("enabled")
    keyword = data.get("keyword")

    result = await logic.list_schedules(page=page, page_size=page_size, tenant_id=tenant_id, enabled=enabled, keyword=keyword)

    return response_ok(data=result)


@inspection_bp.post("/schedule/delete")
@openapi.definition(
    summary="删除巡检任务",
    description="删除指定的巡检任务配置",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "巡检任务ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:d")
async def delete_schedule(request: Request):
    """
    删除巡检任务
    Delete inspection schedule
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    success = await logic.delete_schedule(data["id"], tenant_id)
    if not success:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(data={"message": "删除成功"})


# ==================== Notify Config Management ====================


@inspection_bp.post("/schedule/notify")
@openapi.definition(
    summary="巡检通知配置",
    description="为指定巡检任务配置通知（注意：通知动作配置是覆盖式）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id", "notify_enabled", "notify_config"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "notify_enabled": {"type": "boolean", "description": "是否启用通知"},
                    "notify_config": {"type": "object", "description": "通知动作配置"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "schedule_id": {"type": "string"},
                                    "notify_enabled": {"type": "boolean"},
                                    "notify_config": {"type": "object"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def create_notify_config(request: Request):
    """
    巡检通知配置
    Create inspection notification configuration
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    schedule_id = data.get("schedule_id")
    notify_enabled = data.get("notify_enabled")
    notify_config = data.get("notify_config")

    if not schedule_id or notify_enabled is None or notify_config is None:
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule = await logic.notify_config(schedule_id, notify_enabled, notify_config, tenant_id)

    return response_ok(
        data={
            "id": schedule.id,
            "schedule_id": schedule_id,
            "notify_enabled": schedule.notify_enabled,
            "notify_config": schedule.notify_config,
            "utime": schedule.utime.isoformat() if schedule.utime else None,
        }
    )


@inspection_bp.post("/schedule/custom-report")
@openapi.definition(
    summary="巡检自定义报告配置",
    description="为指定巡检任务配置自定义报告动作（注意：自定义报告动作配置是覆盖式）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id", "custom_report_config"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "custom_report_config": {"type": "object", "description": "自定义报告动作配置"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "配置成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "schedule_id": {"type": "string"},
                                    "custom_report_config": {"type": "object"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def create_custom_report_config(request: Request):
    """
    巡检自定义报告配置
    Create inspection custom report configuration
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    schedule_id = data.get("schedule_id")
    custom_report_config = data.get("custom_report_config")

    if not schedule_id or custom_report_config is None:
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule = await logic.custom_report_config(schedule_id, custom_report_config, tenant_id)

    return response_ok(
        data={
            "id": schedule.id,
            "schedule_id": schedule_id,
            "custom_report_config": schedule.custom_report_config,
            "utime": schedule.utime.isoformat() if schedule.utime else None,
        }
    )


# ==================== Category Management ====================


@inspection_bp.post("/category/create")
@openapi.definition(
    summary="创建巡检类别",
    description="创建巡检类别（支持批量创建）",
    body={
        "application/json": {
            "schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "schedule_id", "app_name"],
                    "properties": {
                        "name": {"type": "string", "description": "类别名称"},
                        "schedule_id": {"type": "string", "description": "关联巡检任务ID"},
                        "app_name": {"type": "string", "description": "应用名称；类别类型为 playbook 时填写剧本名称或剧本ID"},
                        "category_type": {"type": "string", "description": "类别类型: action动作/playbook剧本，默认action"},
                        "resource_ids": {"type": "array", "items": {"type": "string"}, "description": "资源ID列表；action类型必填，playbook类型无需填写"},
                        "enabled": {"type": "boolean", "description": "是否启用"},
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "name": {"type": "string"},
                                        "schedule_id": {"type": "string"},
                                        "app_name": {"type": "string"},
                                        "category_type": {"type": "string"},
                                        "resource_ids": {"type": "array", "items": {"type": "string"}},
                                        "enabled": {"type": "boolean"},
                                        "ctime": {"type": "string"},
                                        "utime": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def create_category(request: Request):
    """
    创建巡检类别
    Create inspection category
    """
    raw_data = request.json
    if raw_data is None:
        raw_data = []

    tenant_id = request.ctx.session.get("tenant_id")

    # 强制要求输入为列表
    if not isinstance(raw_data, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    data_list = raw_data

    # 验证必需字段。剧本类别不要求选择资源，app_name 用剧本名称/ID。
    required_fields = ["name", "schedule_id", "app_name"]
    processed_list = []

    for item in data_list:
        if not isinstance(item, dict):
            continue

        category_type = logic.normalize_category_type(item.get("category_type"))

        # 验证必需字段
        for field in required_fields:
            if field not in item:
                raise CustomError(ErrorCode.PARAM_ERROR)
        if category_type == logic.INSPECTION_CATEGORY_TYPE_ACTION and "resource_ids" not in item:
            raise CustomError(ErrorCode.PARAM_ERROR)

        processed_list.append(item)

    if not processed_list and data_list:
        if not data_list:
            return response_ok(data=[])
        raise CustomError(ErrorCode.PARAM_ERROR)

    categories = await logic.batch_create_categories(processed_list, tenant_id)

    result_data = [_serialize_category(category) for category in categories]

    return response_ok(data=result_data)


@inspection_bp.post("/category/list")
@openapi.definition(
    summary="获取巡检类别列表",
    description="获取巡检类别列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "page_size": {"type": "integer", "description": "每页数量"},
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "total": {"type": "integer"},
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "schedule_id": {"type": "string"},
                                                "app_name": {"type": "string"},
                                                "category_type": {"type": "string"},
                                                "resource_ids": {"type": "array", "items": {"type": "string"}},
                                                "enabled": {"type": "boolean"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def list_categories(request: Request):
    """
    获取巡检类别列表
    Get inspection category list
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    schedule_id = data.get("schedule_id")
    enabled = data.get("enabled")

    result = await logic.list_categories(page=page, page_size=page_size, tenant_id=tenant_id, schedule_id=schedule_id, enabled=enabled)

    return response_ok(data=result)


@inspection_bp.post("/category/get")
@openapi.definition(
    summary="获取巡检类别详情",
    description="获取巡检类别详情",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "类别ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "schedule_id": {"type": "string"},
                                    "app_name": {"type": "string"},
                                    "category_type": {"type": "string"},
                                    "resource_ids": {"type": "array", "items": {"type": "string"}},
                                    "enabled": {"type": "boolean"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def get_category(request: Request):
    """
    获取巡检类别详情
    Get inspection category details
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    category = await logic.get_category(data["id"], tenant_id)
    if not category:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(data=_serialize_category(category))


@inspection_bp.post("/category/update")
@openapi.definition(
    summary="更新巡检类别",
    description="更新巡检类别",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "类别ID"},
                    "name": {"type": "string", "description": "类别名称"},
                    "app_name": {"type": "string", "description": "应用名称；类别类型为 playbook 时填写剧本名称或剧本ID"},
                    "category_type": {"type": "string", "description": "类别类型: action动作/playbook剧本"},
                    "resource_ids": {"type": "array", "items": {"type": "string"}, "description": "资源ID列表；action类型使用，playbook类型会保存为空列表"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "schedule_id": {"type": "string"},
                                    "app_name": {"type": "string"},
                                    "category_type": {"type": "string"},
                                    "resource_ids": {"type": "array", "items": {"type": "string"}},
                                    "enabled": {"type": "boolean"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:u")
async def update_category(request: Request):
    """
    更新巡检类别
    Update inspection category
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    category_id = data.pop("id")
    current_category = await logic.get_category(category_id, tenant_id)
    if not current_category:
        raise CustomError(ErrorCode.CATEGORY_NOT_FOUND)

    type_provided = "category_type" in data
    requested_type = logic.normalize_category_type(data.get("category_type", getattr(current_category, "category_type", None)))
    current_type = logic.normalize_category_type(getattr(current_category, "category_type", None))
    if requested_type == logic.INSPECTION_CATEGORY_TYPE_ACTION and (
        type_provided and current_type == logic.INSPECTION_CATEGORY_TYPE_PLAYBOOK and "resource_ids" not in data
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    category = await logic.update_category(category_id, data, tenant_id)

    return response_ok(data=_serialize_category(category))


@inspection_bp.post("/category/delete")
@openapi.definition(
    summary="删除巡检类别",
    description="删除巡检类别",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "类别ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:d")
async def delete_category(request: Request):
    """
    删除巡检类别
    Delete inspection category
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    success = await logic.delete_category(data["id"], tenant_id)
    if not success:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(data={"message": "删除成功"})


# ==================== Target Management ====================


@inspection_bp.post("/target/create")
@openapi.definition(
    summary="创建巡检项",
    description="创建巡检项（仅支持批量创建）",
    body={
        "application/json": {
            "schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["content", "category_id", "standard"],
                    "properties": {
                        "content": {"type": "string", "description": "巡检内容"},
                        "category_id": {"type": "string", "description": "关联巡检类别ID"},
                        "standard": {"type": "string", "description": "巡检标准"},
                        "judgment_rule": {"type": "object", "description": "巡检判断规则；为空时首次添加结果自动生成"},
                        "action_name": {"type": "string", "description": "动作名称；playbook类别未传时默认使用剧本名称/ID"},
                        "version": {"type": "string", "description": "动作或剧本版本；为空使用最新版本"},
                        "action_input": {"type": "object", "description": "动作输入参数或剧本 flow_input 扩展参数"},
                        "enabled": {"type": "boolean", "description": "是否启用"},
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "content": {"type": "string"},
                                        "category_id": {"type": "string"},
                                        "standard": {"type": "string"},
                                        "judgment_rule": {"type": "object"},
                                        "action_name": {"type": "string"},
                                        "version": {"type": "string"},
                                        "category_type": {"type": "string"},
                                        "app_name": {"type": "string"},
                                        "action_input": {"type": "object"},
                                        "enabled": {"type": "boolean"},
                                        "ctime": {"type": "string"},
                                        "utime": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def create_target(request: Request):
    """
    创建巡检项
    Create inspection target
    """
    raw_data = request.json
    if raw_data is None:
        raw_data = []

    tenant_id = request.ctx.session.get("tenant_id")

    # 强制要求输入为列表
    if not isinstance(raw_data, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    data_list = raw_data

    # 验证必需字段并处理JSON字符串。剧本类别不要求动作名，默认使用类别 app_name。
    base_required_fields = ["content", "category_id", "standard"]
    category_ids = list({item.get("category_id") for item in data_list if isinstance(item, dict) and item.get("category_id")})
    categories = await InspectionCategory.filter(id__in=category_ids, deleted=False, tenant_id=tenant_id).all()
    category_map = {cat.id: cat for cat in categories}
    processed_list = []

    for item in data_list:
        if not isinstance(item, dict):
            continue

        # 先验证基础必填字段，再按类别类型决定是否需要动作名。
        for field in base_required_fields:
            if field not in item:
                raise CustomError(ErrorCode.PARAM_ERROR)

        category = category_map.get(item.get("category_id"))
        if not category:
            raise CustomError(ErrorCode.NOT_FOUND)

        category_type = logic.normalize_category_type(getattr(category, "category_type", None))
        if category_type == logic.INSPECTION_CATEGORY_TYPE_ACTION and "action_name" not in item:
            raise CustomError(ErrorCode.PARAM_ERROR)

        # 处理可能为字符串的JSON字段
        processed_item = item.copy()
        if category_type == logic.INSPECTION_CATEGORY_TYPE_PLAYBOOK and not processed_item.get("action_name"):
            processed_item["action_name"] = category.app_name
        processed_item["action_input"] = _normalize_action_input(processed_item.get("action_input"))
        processed_list.append(processed_item)

    if not processed_list and data_list:
        if not data_list:
            return response_ok(data=[])
        raise CustomError(ErrorCode.PARAM_ERROR)

    targets = await logic.batch_create_targets(processed_list, tenant_id)

    result_data = [
        _serialize_target(
            target,
            app_name=category_map.get(target.category_id).app_name if category_map.get(target.category_id) else None,
            category_type=getattr(category_map.get(target.category_id), "category_type", None) if category_map.get(target.category_id) else None,
        )
        for target in targets
    ]

    return response_ok(data=result_data)


@inspection_bp.post("/target/list")
@openapi.definition(
    summary="获取巡检项列表",
    description="获取巡检项列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "page_size": {"type": "integer", "description": "每页数量"},
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "keyword": {"type": "string", "description": "搜索关键词"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "total": {"type": "integer"},
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "content": {"type": "string"},
                                                "category_id": {"type": "string"},
                                                "category_type": {"type": "string"},
                                                "app_name": {"type": "string"},
                                                "standard": {"type": "string"},
                                                "judgment_rule": {"type": "object"},
                                                "action_name": {"type": "string"},
                                                "version": {"type": "string"},
                                                "action_input": {"type": "object"},
                                                "enabled": {"type": "boolean"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def list_targets(request: Request):
    """
    获取巡检项列表
    Get inspection target list
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    schedule_id = data.get("schedule_id")
    enabled = data.get("enabled")
    keyword = data.get("keyword")

    if not schedule_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    result = await logic.list_targets(page=page, page_size=page_size, tenant_id=tenant_id, schedule_id=schedule_id, enabled=enabled, keyword=keyword)

    return response_ok(data=result)


@inspection_bp.post("/target/get")
@openapi.definition(
    summary="获取巡检项详情",
    description="获取巡检项详情",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "目标ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "content": {"type": "string"},
                                    "category_id": {"type": "string"},
                                    "app_name": {"type": "string"},
                                    "category_type": {"type": "string"},
                                    "standard": {"type": "string"},
                                    "judgment_rule": {"type": "object"},
                                    "action_name": {"type": "string"},
                                    "version": {"type": "string"},
                                    "action_input": {"type": "object"},
                                    "enabled": {"type": "boolean"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def get_target(request: Request):
    """
    获取巡检项详情
    Get inspection target details
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    target = await logic.get_target(data["id"], tenant_id)
    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    category = await InspectionCategory.filter(id=target.category_id, deleted=False, tenant_id=tenant_id).first()

    return response_ok(
        data=_serialize_target(
            target,
            app_name=category.app_name if category else None,
            category_type=getattr(category, "category_type", None) if category else None,
        )
    )


@inspection_bp.post("/target/update")
@openapi.definition(
    summary="更新巡检项",
    description="更新巡检项",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "巡检项ID"},
                    "content": {"type": "string", "description": "巡检内容"},
                    "standard": {"type": "string", "description": "巡检标准"},
                    "judgment_rule": {"type": "object", "description": "巡检判断规则；传 null 可清空并在下次添加结果时自动生成"},
                    "action_name": {"type": "string", "description": "动作名称；playbook类别可填写剧本显示名称"},
                    "version": {"type": "string", "description": "动作或剧本版本；空则使用最新版本"},
                    "action_input": {"type": "object", "description": "动作输入参数或剧本 flow_input 扩展参数"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "content": {"type": "string"},
                                    "category_id": {"type": "string"},
                                    "standard": {"type": "string"},
                                    "judgment_rule": {"type": "object"},
                                    "action_name": {"type": "string"},
                                    "version": {"type": "string"},
                                    "category_type": {"type": "string"},
                                    "app_name": {"type": "string"},
                                    "action_input": {"type": "object"},
                                    "enabled": {"type": "boolean"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:u")
async def update_target(request: Request):
    """
    更新巡检项
    Update inspection target
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    target_id = data.pop("id")
    if "action_input" in data:
        data["action_input"] = _normalize_action_input(data.get("action_input"))

    target = await logic.update_target(target_id, data, tenant_id)
    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    category = await InspectionCategory.filter(id=target.category_id, deleted=False, tenant_id=tenant_id).first()

    return response_ok(
        data=_serialize_target(
            target,
            app_name=category.app_name if category else None,
            category_type=getattr(category, "category_type", None) if category else None,
        )
    )


@inspection_bp.post("/target/delete")
@openapi.definition(
    summary="删除巡检项",
    description="删除巡检项",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "目标ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:d")
async def delete_target(request: Request):
    """
    删除巡检目标
    Delete inspection target
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    success = await logic.delete_target(data["id"], tenant_id)
    if not success:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(data={"message": "删除成功"})


@inspection_bp.post("/target/all_configs")
@openapi.definition(
    summary="获取全部巡检目标配置",
    description="获取全部巡检目标配置，用于执行器调用",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "target_id": {"type": "string", "description": "巡检项ID（可选，如果提供则只返回该巡检项的配置）"},
                    "tenant_id": {"type": "string", "description": "租户ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "main": {"type": "string"},
                                        "timeout": {"type": "string"},
                                        "version": {"type": "string"},
                                        "resources": {"type": "string"},
                                        "tool_name": {"type": "string"},
                                        "action_name": {"type": "string"},
                                        "action_input": {"type": "object"},
                                        "type": {"type": "string"},
                                        "flow_id": {"type": "string"},
                                        "flow_input": {"type": "object"},
                                        "playbook_id": {"type": "string"},
                                        "playbook_name": {"type": "string"},
                                        "target_id": {"type": "string"},
                                        "schedule_id": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def get_all_configs(request: Request):
    """
    获取全部巡检目标配置
    Get all inspection target configurations
    """
    data = request.json or {}
    schedule_ids = data.get("schedule_id")
    if not schedule_ids:
        raise CustomError(ErrorCode.PARAM_ERROR)
    target_ids = data.get("target_id") if data.get("target_id") != "None" else None
    tenant_id = _resolve_authenticated_tenant_id(request, data)
    result = await logic.get_all_configs(tenant_id, schedule_ids, target_ids)
    return response_ok(data=result)


@inspection_bp.post("/parse")
@openapi.definition(
    summary="解析巡检内容",
    description="使用AI解析巡检内容，提取巡检字段",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["category_id", "text"],
                "properties": {
                    "category_id": {"type": "string", "description": "巡检类别ID"},
                    "text": {"type": "string", "description": "巡检描述文本"},
                    "version": {"type": "string", "description": "动作或剧本版本；空则使用最新版本"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "解析成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "standard": {"type": "string"},
                                    "action_name": {"type": "string"},
                                    "confidence": {"type": "number"},
                                    "suggestions": {"type": "array", "items": {"type": "string"}},
                                    "category_type": {"type": "string"},
                                    "flow_id": {"type": "string"},
                                    "version": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def parse_inspection(request: Request):
    """
    解析巡检内容
    Parse inspection content using AI
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    category_id = data.get("category_id")
    text = data.get("text")

    if not category_id or not text:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 查询巡检类别获取 app_name
    category = await logic.get_category(category_id, tenant_id)
    if not category:
        raise CustomError(ErrorCode.NOT_FOUND)

    result = await logic.parse_inspection_content(
        category.app_name,
        text,
        tenant_id,
        category_type=getattr(category, "category_type", None),
        version=data.get("version"),
    )
    result["app_name"] = category.app_name
    result["category_type"] = logic.normalize_category_type(getattr(category, "category_type", None))
    return response_ok(data=result)


@inspection_bp.post("/start")
@openapi.definition(
    summary="启动巡检",
    description="启动指定巡检任务的定时任务",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "启动成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def start_inspection(request: Request):
    """
    启动巡检
    Start inspection
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "schedule_id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule_id = data["schedule_id"]

    # 查询巡检调度配置
    schedule = await logic.get_schedule_by_id(schedule_id, tenant_id)
    if not schedule:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 查询是否已经存在定时任务
    timed_name = f"inspection_{schedule_id}"
    timed = await Timed.filter(name=timed_name, deleted=False, tenant_id=tenant_id).first()

    if timed:
        # 如果已存在，更新 enable=1
        if timed.enable == 0:
            timed.enable = 1
            timed.updator = request.ctx.session["account_info"]
            timed.tactics = schedule.schedule_cron  # 更新定时规则，确保修改了调度配置后能生效
            await timed.save()

            # 发送消息到 Kafka
            msg = {"run_type": "flow", "run_args": timed.playbook_input, "timer": "cron", "timer_rule": {"end_date": timed.end_time, "crontab": schedule.schedule_cron}, "timer_id": timed.id, "tenant_id": tenant_id}
            await aproduce(TASK_TOPIC, msg)
    else:
        # 创建新的定时任务
        # 维护剧本只存在于 default 租户，业务数据仍由当前会话租户隔离。
        playbook, playbook_version = await _get_automation_inspection_playbook()

        if not playbook:
            return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

        if not playbook_version:
            return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

        playbook_version_id = playbook_version.id

        # 构建剧本输入参数（传入 schedule_id，剧本会查询该 schedule 下所有启用的巡检项）
        playbook_input = {"flow_id": playbook_version_id, "flow_input": {"schedule_id": schedule_id, "target_id": "None", "tenant_id": tenant_id}, "executor": request.ctx.session.get("account_info").id}

        # 创建定时任务
        timed = await Timed.create(
            name=timed_name,
            type=0,  # 执行剧本
            playbook=playbook,
            playbook_version=playbook_version.version,
            playbook_input=playbook_input,
            status=1,  # 周期执行
            tactics=schedule.schedule_cron,
            end_time="2099-12-31 23:59:59",
            enable=1,
            creator=request.ctx.session["account_info"],
            updator=request.ctx.session["account_info"],
            internal=True,
        )

        # 发送消息到 Kafka
        msg = {"run_type": "flow", "run_args": playbook_input, "timer": "cron", "timer_rule": {"end_date": timed.end_time, "crontab": schedule.schedule_cron}, "timer_id": timed.id, "tenant_id": tenant_id}
        await aproduce(TASK_TOPIC, msg)

    # 更新巡检调度配置为启用状态
    if not schedule.enabled:
        schedule.enabled = True
        await schedule.save()

    return response_ok(data={"message": "启动成功"})


@inspection_bp.post("/pause")
@openapi.definition(
    summary="暂停巡检",
    description="暂停指定巡检任务的定时任务",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "暂停成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def pause_inspection(request: Request):
    """
    暂停巡检
    Pause inspection
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "schedule_id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule_id = data["schedule_id"]

    # 查询定时任务
    timed_name = f"inspection_{schedule_id}"
    timed = await Timed.filter(name=timed_name, deleted=False, tenant_id=tenant_id).first()

    if not timed:
        # 更新巡检调度配置为暂停状态
        schedule = await logic.get_schedule_by_id(schedule_id, tenant_id)
        if schedule and schedule.enabled:
            schedule.enabled = False
            await schedule.save()
        return response_ok(data={"message": "暂停成功"})

    # 更新 enable=0
    if timed.enable == 1:
        timed.enable = 0
        timed.updator = request.ctx.session["account_info"]
        await timed.save()

        # 发送消息到 Kafka（通知停止定时任务）
        msg = {"run_type": "flow", "run_args": timed.playbook_input, "timer": "remove", "timer_rule": {}, "timer_id": timed.id, "tenant_id": tenant_id}
        await aproduce(TASK_TOPIC, msg)

    # 更新巡检调度配置为暂停状态
    schedule = await logic.get_schedule_by_id(schedule_id, tenant_id)
    if schedule and schedule.enabled:
        schedule.enabled = False
        await schedule.save()

    return response_ok(data={"message": "暂停成功"})


@inspection_bp.post("/execute")
@openapi.definition(
    summary="手动执行巡检项",
    description="手动执行巡检项",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "target_id": {"type": "array", "items": {"type": "string"}, "description": "巡检项ID列表"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "巡检成功",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
@requires_permission("inspection:c")
async def execute_inspection(request: Request):
    """
    手动执行巡检项
    Start inspection
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "schedule_id" not in data or "target_id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule_id = data["schedule_id"]
    target_ids = data["target_id"]
    if not isinstance(target_ids, list) or not target_ids:
        raise CustomError(ErrorCode.PARAM_ERROR)
    start_time = datetime.now()

    # 查询巡检调度配置
    schedule = await logic.get_schedule_by_id(schedule_id, tenant_id)
    if not schedule:
        raise CustomError(ErrorCode.PARAM_ERROR)

    playbook, playbook_version = await _get_automation_inspection_playbook()

    if not playbook:
        return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

    if not playbook_version:
        return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

    playbook_version_id = playbook_version.id

    # 构建剧本输入参数（传入 schedule_id，剧本会查询该 schedule 下所有启用的巡检项）
    playbook_input = {"flow_id": playbook_version_id, "flow_input": {"schedule_id": schedule_id, "target_id": target_ids, "tenant_id": tenant_id}, "executor": request.ctx.session.get("account_info").id}

    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    # 发送消息到 Kafka
    msg = {"run_type": "flow", "run_id": gen_run_id(), "run_args": playbook_input, "tenant_id": tenant_id}
    await aproduce(TASK_TOPIC, msg)
    await resp.send(f"data: {orjson.dumps({'message': '手动执行已启动'}).decode()}\n\n")

    sent_id = []
    for _ in range(60):
        results = (
            await InspectionResult.filter(
                deleted=False,
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                target_id__in=target_ids,
                ctime__gte=start_time,
            )
            .order_by("ctime")
            .all()
        )

        for result in results:
            if result.id in sent_id:
                continue
            sent_id.append(result.id)
            result_data = {
                "id": result.id,
                "target_id": result.target_id,
                "schedule_id": result.schedule_id,
                "start_time": result.start_time.isoformat() if result.start_time else None,
                "end_time": result.end_time.isoformat() if result.end_time else None,
                "status": result.status,
                "execution_status": result.execution_status,
                "result_data": result.result_data,
                "error_message": result.error_message,
                "duration": result.duration,
            }
            await resp.send(f"data: {orjson.dumps(result_data).decode()}\n\n")

        if len(sent_id) >= len(target_ids):
            break

        await asyncio.sleep(1)

    await resp.eof()
    return resp


# ==================== Result Management ====================


@inspection_bp.post("/result/list")
@openapi.definition(
    summary="获取巡检结果列表",
    description="获取巡检结果列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "page_size": {"type": "integer", "description": "每页数量"},
                    "target_id": {"type": "string", "description": "目标ID"},
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "status": {"type": "string", "description": "执行结果状态"},
                    "execution_status": {"type": "string", "description": "执行状态"},
                    "start_time": {"type": "string", "description": "开始时间"},
                    "end_time": {"type": "string", "description": "结束时间"},
                    "keyword": {"type": "string", "description": "搜索关键词"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "total": {"type": "integer"},
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "target_id": {"type": "string"},
                                                "schedule_id": {"type": "string"},
                                                "start_time": {"type": "string"},
                                                "end_time": {"type": "string"},
                                                "status": {"type": "string"},
                                                "execution_status": {"type": "string"},
                                                "result_data": {"type": "object"},
                                                "error_message": {"type": "string"},
                                                "duration": {"type": "integer"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def list_results(request: Request):
    """
    获取巡检结果列表
    Get inspection result list
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    target_id = data.get("target_id")
    schedule_id = data.get("schedule_id")
    status = data.get("status")
    execution_status = data.get("execution_status")
    keyword = data.get("keyword")

    # 解析时间参数
    start_time = None
    end_time = None
    if data.get("start_time"):
        try:
            start_time = datetime.fromisoformat(str(data["start_time"]).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    if data.get("end_time"):
        try:
            end_time = datetime.fromisoformat(str(data["end_time"]).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    result = await logic.list_results(
        page=page,
        page_size=page_size,
        tenant_id=tenant_id,
        target_id=target_id,
        schedule_id=schedule_id,
        status=status,
        execution_status=execution_status,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
    )

    return response_ok(data=result)


@inspection_bp.post("/result/get")
@openapi.definition(
    summary="获取巡检结果详情",
    description="获取巡检结果详情",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "结果ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "target_id": {"type": "string"},
                                    "start_time": {"type": "string"},
                                    "end_time": {"type": "string"},
                                    "status": {"type": "string"},
                                    "execution_status": {"type": "string"},
                                    "result_data": {"type": "object"},
                                    "error_message": {"type": "string"},
                                    "duration": {"type": "integer"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def get_result(request: Request):
    """
    获取巡检结果详情
    Get inspection result details
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    if "id" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    result = await logic.get_result(data["id"], tenant_id)
    if not result:
        raise CustomError(ErrorCode.NOT_FOUND)

    target = await InspectionTarget.get_or_none(id__in=[result.target_id], tenant_id=tenant_id, deleted=False)

    return response_ok(
        data={
            "id": result.id,
            "target_id": result.target_id,
            "schedule_id": result.schedule_id,
            "start_time": result.start_time.isoformat() if result.start_time else None,
            "end_time": result.end_time.isoformat() if result.end_time else None,
            "status": result.status,
            "execution_status": result.execution_status,
            "result_data": result.result_data,
            "error_message": result.error_message,
            "duration": result.duration,
            "target_content": logic.result_target_snapshot(
                result, target, "content"
            ),
            "target_standard": logic.result_target_snapshot(
                result, target, "standard"
            ),
        }
    )


@inspection_bp.post("/result/stats")
@openapi.definition(
    summary="获取巡检统计信息",
    description="获取巡检统计信息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "start_time": {"type": "string", "description": "开始时间"},
                    "end_time": {"type": "string", "description": "结束时间"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "success_rate": {"type": "number", "description": "巡检成功率"},
                                    "success_count": {"type": "integer", "description": "巡检项成功数量"},
                                    "failed_count": {"type": "integer", "description": "巡检项失败数量"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:r")
async def get_stats(request: Request):
    """
    获取巡检统计信息
    Get inspection statistics
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    schedule_id = data.get("schedule_id")
    if not schedule_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 解析时间参数
    start_time = None
    end_time = None
    if data.get("start_time"):
        try:
            start_time = datetime.fromisoformat(str(data["start_time"]).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    if data.get("end_time"):
        try:
            end_time = datetime.fromisoformat(str(data["end_time"]).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    stats = await logic.get_stats(tenant_id=tenant_id, schedule_id=schedule_id, start_time=start_time, end_time=end_time)

    return response_ok(data=stats)


@inspection_bp.post("/result/export")
@openapi.definition(
    summary="导出巡检结果为Excel文件",
    description="导出巡检结果为Excel文件，支持自定义导出列",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_id", "columns", "start_time", "end_time"],
                "properties": {
                    "schedule_id": {"type": "string", "description": "巡检任务ID"},
                    "start_time": {"type": "string", "description": "开始时间 (ISO 8601)"},
                    "end_time": {"type": "string", "description": "结束时间 (ISO 8601)"},
                    "columns": {"type": "array", "items": {"type": "string"}, "description": "需要导出的列名列表，可选值：巡检内容、巡检标准、巡检状态、执行结果状态、执行状态、耗时、错误信息、开始时间、结束时间、AI裁决结果、执行结果"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "导出成功，返回Excel文件",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
@requires_permission("inspection:r")
async def export_results(request: Request):
    """
    导出巡检结果为Excel文件
    Export inspection results as Excel file
    """
    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    schedule_id = data.get("schedule_id")
    columns = data.get("columns")

    if not schedule_id or not columns or not isinstance(columns, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 解析时间参数
    start_time = None
    end_time = None
    if data.get("start_time"):
        try:
            start_time = datetime.fromisoformat(str(data["start_time"]).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    if data.get("end_time"):
        try:
            end_time = datetime.fromisoformat(str(data["end_time"]).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    excel_data = await logic.export_results_to_excel(schedule_id=schedule_id, start_time=start_time, end_time=end_time, columns=columns, tenant_id=tenant_id)

    # 生成文件名
    filename = f"inspection_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    # 返回Excel文件
    response = raw(excel_data)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response


@inspection_bp.post("/sql/execute")
@openapi.definition(
    summary="执行SQL语句",
    description="执行SQL语句，输入参数为sql字符串",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["sql"],
                "properties": {
                    "sql": {"type": "string", "description": "SQL语句"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "执行成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "array", "items": {"type": "object"}},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def execute_sql(request: Request):
    """
    执行SQL语句
    Execute SQL statement
    """
    data = request.json or {}
    sql = data.get("sql")

    if not sql:
        raise CustomError(ErrorCode.PARAM_ERROR)

    async with in_transaction() as conn:
        try:
            # 判断是否为查询语句
            # Determine if it is a query statement
            if sql.strip().upper().startswith("SELECT"):
                result = await conn.execute_query_dict(sql)
            else:
                # 执行非查询语句（如 INSERT, UPDATE, DELETE）
                # Execute non-query statements (e.g. INSERT, UPDATE, DELETE)
                # execute_query 返回 (affected_rows, result)
                row_count, _ = await conn.execute_query(sql)
                result = [{"affected_rows": row_count, "message": "SQL executed successfully"}]
        except Exception as e:
            response_ok(f"SQL执行失败 {e}")

    return response_ok(data=result)


@inspection_bp.post("/result/add")
@openapi.definition(
    summary="添加巡检结果",
    description="批量添加巡检结果记录，首次自动生成判断规则，后续根据判断规则判断执行结果状态，并保存动作执行状态",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["result_data"],
                "properties": {
                    "result_data": {
                        "type": "array",
                        "description": "巡检结果数据数组",
                        "items": {
                            "type": "object",
                            "required": ["target_id", "result"],
                            "properties": {
                                "target_id": {"type": "string", "description": "巡检目标ID"},
                                "schedule_id": {"type": "string", "description": "巡检任务ID"},
                                "tool_name": {"type": "string", "description": "工具名称"},
                                "action_name": {"type": "string", "description": "动作名称"},
                                "resource_name": {"type": "string", "description": "资源名称"},
                                "result": {"type": "object", "description": "执行结果数据"},
                                "execution_time": {"type": "number", "description": "执行耗时(秒)"},
                                "end_time": {"type": "string", "description": "结束时间 (ISO 8601)"},
                            },
                        },
                    },
                    "start_time": {"type": "string", "description": "开始时间 (ISO 8601)"},
                    "tenant_id": {"type": "string", "description": "租户ID"},
                    "send_notify": {"type": "boolean", "description": "是否触发通知动作（依据巡检任务通知配置发送），默认 false"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "添加成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "target_id": {"type": "string"},
                                        "status": {"type": "string"},
                                        "execution_status": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def add_result(request: Request):
    """
    添加巡检结果
    Add inspection result
    """
    request_data = request.json or {}
    tenant_id = _resolve_authenticated_tenant_id(request, request_data)

    result_data_array = request_data.get("result_data")
    start_time = request_data.get("start_time")
    send_notify = request_data.get("send_notify", False)

    if not result_data_array or not start_time:
        raise CustomError(ErrorCode.PARAM_ERROR)

    created_results = []
    created_result_items = []

    if isinstance(result_data_array, str):
        try:
            result_data_array = orjson.loads(result_data_array)
        except orjson.JSONDecodeError:
            try:
                result_data_array = ast.literal_eval(result_data_array)
            except (ValueError, SyntaxError):
                raise CustomError(ErrorCode.PARAM_ERROR)

    if isinstance(result_data_array, dict):
        result_data_array = [result_data_array]

    if not isinstance(result_data_array, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    for item in result_data_array:
        if isinstance(item, str):
            try:
                item = orjson.loads(item)
            except orjson.JSONDecodeError:
                try:
                    item = ast.literal_eval(item)
                except (ValueError, SyntaxError):
                    logger.warning(f"跳过无效的巡检结果项: {item}")
                    continue
        if not isinstance(item, dict):
            logger.warning(f"跳过无效的巡检结果项: {item}")
            continue

        # Extract required fields
        target_id = item.get("target_id")
        result = item.get("result")

        if not target_id or not result:
            logger.warning(f"跳过无效的巡检结果项: 缺少target_id或result")
            continue

        # Extract optional fields
        schedule_id = item.get("schedule_id")
        end_time_str = item.get("end_time")
        execution_time = item.get("execution_time", 0)
        execution_status = "success"
        item["execution_status"] = execution_status

        # Parse end_time
        end_time = None
        if end_time_str:
            try:
                if isinstance(end_time_str, str):
                    end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                logger.warning(f"无效的end_time格式: {end_time_str}")

        if not end_time:
            end_time = datetime.now()

        status = "failed"

        try:
            judgment = await logic.judge_inspection_result(target_id, result, tenant_id)

            # Determine result status based on persisted judgment rule
            status = "success" if judgment.get("passed") else "failed"
            error_message = None if judgment.get("passed") else judgment.get("reason")

            # 兼容下游报告/导出字段，仍写入 ai_judgment
            item["ai_judgment"] = judgment

        except Exception as e:
            logger.warning(f"巡检结果规则判断失败: {str(e)}")
            error_message = f"巡检结果规则判断失败: {str(e)}"

        # Create InspectionResult
        try:
            result_obj = await logic.create_result(
                {
                    "target_id": target_id,
                    "schedule_id": schedule_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration": int(execution_time) if execution_time else 0,
                    "status": status,
                    "execution_status": execution_status,
                    "result_data": item,
                    "error_message": error_message,
                },
                tenant_id,
            )

            created_results.append({"id": result_obj.id, "target_id": result_obj.target_id, "status": result_obj.status, "execution_status": result_obj.execution_status})
            created_result_items.append(
                {
                    "id": result_obj.id,
                    "target_id": result_obj.target_id,
                    "schedule_id": result_obj.schedule_id,
                    "status": result_obj.status,
                    "execution_status": result_obj.execution_status,
                    "output": result,
                    "error_message": result_obj.error_message,
                    "duration": result_obj.duration,
                }
            )
        except Exception as e:
            logger.error(f"创建巡检结果失败: {str(e)}")
            continue

    # 推断 schedule_id（从原始数据取第一条有效的 schedule_id）
    batch_schedule_id = None
    if isinstance(result_data_array, list):
        for item in result_data_array:
            if isinstance(item, dict) and item.get("schedule_id"):
                batch_schedule_id = item["schedule_id"]
                break

    # 触发通知
    if send_notify and batch_schedule_id and created_results:
        # 获取巡检任务信息（用于 Excel 和 Word 生成）
        schedule = None
        try:
            schedule = await logic.get_schedule_by_id(batch_schedule_id, tenant_id)
        except Exception as e:
            logger.warning(f"获取巡检任务信息失败: {e}")

        # 生成并上传 Excel
        excel_file_id = await logic.generate_and_upload_inspection_excel(schedule_id=batch_schedule_id, start_time=start_time, tenant_id=tenant_id, schedule=schedule)

        # 生成并上传 Word 报告
        doc_file_id = await logic.generate_and_upload_inspection_word(schedule_id=batch_schedule_id, created_results=created_results, tenant_id=tenant_id, schedule=schedule)

        # 构建下载地址
        excel_download_url = f"{request.scheme}://{request.host}{excel_file_id}" if excel_file_id else ""
        doc_download_url = f"{request.scheme}://{request.host}{doc_file_id}" if doc_file_id else ""

        # 执行通知动作
        if schedule and schedule.notify_enabled and schedule.notify_config:
            notify_actions = _extract_config_actions(schedule.notify_config)
            builtin_replacements = {"${巡检结果}": excel_download_url, "${巡检报告}": doc_download_url}
            result_context = _build_inspection_result_context(created_result_items)
            custom_report_content = ""
            if _actions_contain_custom_report_placeholder(notify_actions):
                custom_report_content = await _generate_custom_report_content(schedule, request, tenant_id, builtin_replacements, result_context)
            notify_replacements = {**builtin_replacements, CUSTOM_REPORT_PLACEHOLDER: custom_report_content}

            for action in notify_actions:
                data = _build_action_input(action, notify_replacements, result_context)

                params = {
                    "resource": action.get("resource", ""),
                    "node_name": action.get("description", ""),
                    "action": action.get("func", ""),
                    "action_name": action.get("name", ""),
                    "app_name": action.get("tool_name", ""),
                    "name": action.get("description", ""),
                    "app_ver": action.get("version", ""),
                    "main": action.get("tool_main", ""),
                    "action_input": data,
                    "retry_times": _safe_int(action.get("retry_times"), 3),
                    "timeout": _safe_int(action.get("timeout"), 300),
                    "executor": _get_account_id(request.ctx.session.get("account_info")),
                }

                msg = {"run_type": "action", "run_id": gen_run_id(), "run_args": params, "tenant_id": tenant_id}
                await aproduce(TASK_TOPIC, msg)
                logger.info(f"已发送巡检通知动作: {action.get('name', '')}")

    return response_ok(data=created_results)


@inspection_bp.post("/target/export")
@openapi.definition(
    summary="导出巡检项为Excel文件",
    description="导出巡检项及其关联的类别和任务为Excel文件",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["target_ids"],
                "properties": {
                    "target_ids": {"type": "array", "items": {"type": "string"}, "description": "巡检项ID列表"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "导出成功，返回Excel文件",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
@requires_permission("inspection:r")
async def export_targets(request: Request):
    """
    导出巡检项为Excel文件
    Export inspection targets as Excel file
    """
    from sanic.response import raw

    data = request.json or {}
    tenant_id = request.ctx.session.get("tenant_id")

    target_ids = data.get("target_ids")
    if not target_ids or not isinstance(target_ids, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    excel_data = await logic.export_targets_to_excel(target_ids, tenant_id)

    # 生成文件名
    filename = f"inspection_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    # 返回Excel文件
    response = raw(excel_data)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response


@inspection_bp.post("/target/import")
@openapi.definition(
    summary="导入巡检项Excel文件",
    description="导入巡检项及其关联的类别和任务，如果存在同名则跳过或使用已存在的，默认禁用",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string", "format": "binary", "description": "Excel文件(.xlsx)"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "导入成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "schedules_created": {"type": "integer"},
                                    "categories_created": {"type": "integer"},
                                    "targets_created": {"type": "integer"},
                                    "schedule_ids": {"type": "array", "items": {"type": "string"}},
                                    "category_ids": {"type": "array", "items": {"type": "string"}},
                                    "target_ids": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("inspection:c")
async def import_targets(request: Request):
    """
    导入巡检项Excel文件
    Import inspection targets from Excel file
    """
    tenant_id = request.ctx.session.get("tenant_id")

    # 获取上传的文件
    if not request.files or "file" not in request.files:
        raise CustomError(ErrorCode.PARAM_ERROR)

    uploaded_file = request.files.getlist("file")[0]

    # 验证文件类型
    if not uploaded_file.name.endswith((".xlsx", ".xls")):
        raise CustomError(ErrorCode.PARAM_ERROR)

    try:
        # 读取文件内容
        file_content = uploaded_file.body
    except Exception as e:
        logger.error(f"文件读取失败: {str(e)}")
        raise CustomError(ErrorCode.PARAM_ERROR)

    result = await logic.import_targets_from_excel(file_content, tenant_id)
    return response_ok(data=result)


@inspection_bp.get("/target/template")
@openapi.definition(
    summary="下载巡检项导入模板",
    description="下载Excel格式的巡检项导入模板文件",
    response={
        "200": {
            "description": "下载成功，返回Excel模板文件",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
@requires_permission("inspection:r")
async def download_template(request: Request):
    """
    下载巡检项导入模板
    Download inspection import template
    """

    # 模板文件路径
    template_path = os.path.join(os.path.dirname(__file__), "inspection_template_model.xlsx")

    # 检查模板文件是否存在
    if not os.path.exists(template_path):
        raise CustomError(ErrorCode.NOT_FOUND_TO_INSPECTION)

    try:
        # 读取模板文件
        with open(template_path, "rb") as f:
            template_data = f.read()
    except Exception as e:
        logger.error(f"读取模板文件失败: {str(e)}")
        raise CustomError(ErrorCode.READ_FAILED_TO_INSPECTION)

    # 生成文件名
    filename = "inspection_template.xlsx"

    # 返回Excel文件
    response = raw(template_data)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response
