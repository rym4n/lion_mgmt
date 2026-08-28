#!/usr/bin/env python
# coding=utf-8

"""
巡检模块业务逻辑层
Inspection Module Business Logic Layer
"""

import ast
import io
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import orjson
from croniter import croniter
from docxtpl import DocxTemplate
from sanic.log import logger
from tortoise.expressions import Q

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.utils import normalize_required_text, upload_to_s3
from models import (
    InspectionCategory,
    InspectionResult,
    InspectionSchedule,
    InspectionTarget,
    Playbook,
    PlaybookVersion,
    Timed,
    Tool,
    ToolAction,
)
from services.digital_employee.lion_agent_adapter import (
    execute_prompt,
    identity_only_memory,
    workspace_id_for,
)
from services.digital_employee.lion_agent_client import LionAgentExecutionError

INSPECTION_CATEGORY_TYPE_ACTION = "action"
INSPECTION_CATEGORY_TYPE_PLAYBOOK = "playbook"
INSPECTION_CATEGORY_TYPES = {INSPECTION_CATEGORY_TYPE_ACTION, INSPECTION_CATEGORY_TYPE_PLAYBOOK}
INSPECTION_RULE_INPUT_MAX_TOKENS = int(
    os.environ.get("INSPECTION_RULE_INPUT_MAX_TOKENS", "24000")
)
_INSPECTION_SAMPLE_PROFILES = (
    (8, 40, 3, 500),
    (7, 24, 3, 320),
    (6, 16, 2, 240),
    (5, 10, 2, 160),
    (4, 8, 1, 100),
)


async def _acompletion(**kwargs):
    from litellm import acompletion

    return await acompletion(**kwargs)


def is_valid_schedule_cron(value: Any) -> bool:
    """校验巡检执行周期：六段 Cron 且秒、分字段必须固定为 0。"""
    fields = str(value or "").strip().split()
    if len(fields) != 6 or fields[0] != "0" or fields[1] != "0":
        return False
    return croniter.is_valid(" ".join(fields), second_at_beginning=True)


def normalize_category_type(value: Any = None) -> str:
    """规范化巡检类别类型，兼容字符串和定时任务里的 0/1 类型。"""
    if value is None or value == "":
        return INSPECTION_CATEGORY_TYPE_ACTION

    if isinstance(value, bool):
        return INSPECTION_CATEGORY_TYPE_ACTION if value else INSPECTION_CATEGORY_TYPE_PLAYBOOK

    if isinstance(value, int):
        return INSPECTION_CATEGORY_TYPE_PLAYBOOK if value == 0 else INSPECTION_CATEGORY_TYPE_ACTION

    value_str = str(value).strip().lower()
    if value_str in ("playbook", "flow", "剧本", "0"):
        return INSPECTION_CATEGORY_TYPE_PLAYBOOK
    if value_str in ("action", "tool", "动作", "1"):
        return INSPECTION_CATEGORY_TYPE_ACTION

    raise CustomError(ErrorCode.PARAM_ERROR)


def _normalize_resource_ids(resource_ids: Any, category_type: str) -> List[str]:
    """剧本类别不需要资源；动作类别继续返回资源 ID 列表。"""
    if category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK:
        return []

    if resource_ids in (None, ""):
        return []

    if isinstance(resource_ids, str):
        try:
            parsed = orjson.loads(resource_ids)
        except orjson.JSONDecodeError:
            parsed = [rid.strip() for rid in resource_ids.split(",") if rid.strip()]
        resource_ids = parsed

    if not isinstance(resource_ids, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    return [str(rid).strip() for rid in resource_ids if str(rid).strip()]


def _normalize_version(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _normalize_category_data(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    normalized = data.copy()
    if "type" in normalized:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if not partial or "name" in normalized:
        raw_name = normalized.get("name")
        try:
            canonical_name = normalize_required_text(raw_name)
        except CustomError as exc:
            raise CustomError(ErrorCode.INSPECTION_CATEGORY_NAME_INVALID) from exc
        if canonical_name != raw_name:
            raise CustomError(ErrorCode.INSPECTION_CATEGORY_NAME_INVALID)
        normalized["name"] = canonical_name
    type_provided = "category_type" in normalized
    raw_category_type = normalized.get("category_type")
    category_type = normalize_category_type(raw_category_type) if (type_provided or not partial) else None

    if category_type:
        normalized["category_type"] = category_type

    if not partial or "resource_ids" in normalized or category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK:
        normalized["resource_ids"] = _normalize_resource_ids(normalized.get("resource_ids"), category_type or INSPECTION_CATEGORY_TYPE_ACTION)

    if category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK and not normalized.get("app_name"):
        normalized["app_name"] = ""
    return normalized


def _normalize_target_data(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = data.copy()
    if "version" in normalized:
        normalized["version"] = _normalize_version(normalized.get("version"))
    if "judgment_rule" in normalized:
        judgment_rule = normalized.get("judgment_rule")
        if judgment_rule in (None, "", {}):
            normalized["judgment_rule"] = None
        elif isinstance(judgment_rule, str):
            try:
                normalized["judgment_rule"] = orjson.loads(judgment_rule)
            except orjson.JSONDecodeError:
                raise CustomError(ErrorCode.PARAM_ERROR)
        elif not isinstance(judgment_rule, dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
    elif any(key in normalized for key in ("standard", "action_name", "version", "action_input")):
        # 判断规则由巡检标准和结果结构生成，相关配置变更后需要下次结果重新生成。
        normalized["judgment_rule"] = None
    return normalized


def _get_category_type(category: InspectionCategory) -> str:
    return normalize_category_type(getattr(category, "category_type", None))


def _build_action_config(category: InspectionCategory, target: InspectionTarget, tool: Optional[Tool]) -> Dict[str, Any]:
    """构造动作巡检执行配置，保留旧字段并补充标准动作执行字段。"""
    resolved_version = tool.version if tool and tool.version else (target.version or "")
    resources = orjson.dumps(category.resource_ids or []).decode("utf-8")
    action_input = {"params": target.action_input} or {}

    return {
        "type": INSPECTION_CATEGORY_TYPE_ACTION,
        "category_type": INSPECTION_CATEGORY_TYPE_ACTION,
        "main": tool.main if tool and tool.main else "main.py",
        "timeout": "300",
        "version": resolved_version,
        "app_ver": resolved_version,
        "resources": resources,
        "tool_name": category.app_name,
        "app_name": category.app_name,
        "name": category.app_name,
        "action_name": target.action_name,
        "action": target.action_name,
        "node_name": target.content,
        "action_input": action_input,
        "target_id": target.id,
        "schedule_id": category.schedule_id,
    }


async def _resolve_playbook(category: InspectionCategory, tenant_id: str) -> Optional[Playbook]:
    """通过巡检类别 app_name 兼容剧本 ID 或剧本名称。"""
    playbook_key = (category.app_name or "").strip()
    if not playbook_key:
        return None

    return await _resolve_playbook_by_key(playbook_key, tenant_id)


async def _resolve_playbook_by_key(playbook_key: str, tenant_id: str) -> Optional[Playbook]:
    """通过剧本 ID 或剧本名称解析剧本，避免长名称触发 ID 长度校验。"""
    playbook_key = (playbook_key or "").strip()
    if not playbook_key:
        return None

    playbook_filter = Q(name=playbook_key)
    if len(playbook_key) <= 36:
        playbook_filter = Q(id=playbook_key) | playbook_filter
    return await Playbook.filter(playbook_filter, deleted=False, tenant_id=tenant_id).first()


async def _resolve_playbook_version(playbook: Playbook, version: Optional[str], tenant_id: str) -> Optional[PlaybookVersion]:
    """通过版本 ID/版本号解析剧本版本；未指定版本时使用最新已发布版本。"""
    if version:
        return (
            await PlaybookVersion.filter(
                Q(version=version),
                playbook_id=playbook.id,
                deleted=False,
                tenant_id=tenant_id,
            )
            .order_by("-utime")
            .first()
        )

    return (
        await PlaybookVersion.filter(
            playbook_id=playbook.id,
            status=1,
            deleted=False,
            tenant_id=tenant_id,
        )
        .order_by("-utime")
        .first()
    )


def _build_playbook_config(category: InspectionCategory, target: InspectionTarget, playbook: Playbook, playbook_version: PlaybookVersion, tenant_id: str) -> Dict[str, Any]:
    """构造剧本巡检执行配置。"""
    flow_input = target.action_input.copy() if isinstance(target.action_input, dict) else {}
    flow_input.setdefault("schedule_id", category.schedule_id)
    flow_input.setdefault("target_id", target.id)
    flow_input.setdefault("tenant_id", tenant_id)

    return {
        "type": "flow",
        "category_type": INSPECTION_CATEGORY_TYPE_PLAYBOOK,
        "flow_id": playbook_version.id,
        "flow_input": flow_input,
        "version": playbook_version.version,
        "resources": "[]",
        "tool_name": category.app_name,
        "app_name": category.app_name,
        "playbook_id": playbook.id,
        "playbook_name": playbook.name,
        "playbook_version_id": playbook_version.id,
        "action_name": target.action_name,
        "action_input": target.action_input or {},
        "target_id": target.id,
        "schedule_id": category.schedule_id,
        "timeout": str(getattr(playbook_version, "flow_timeout", 300) or 300),
    }


# ==================== InspectionSchedule Operations ====================


async def save_schedule(data: Dict[str, Any], tenant_id: str) -> InspectionSchedule:
    """
    创建或更新巡检调度配置
    Create or update inspection schedule configuration
    """
    if not is_valid_schedule_cron(data.get("schedule_cron")):
        raise CustomError(ErrorCode.PARAM_ERROR)

    schedule_id = data.get("id")

    if schedule_id:
        # 更新现有配置
        schedule = await InspectionSchedule.filter(id=schedule_id, deleted=False, tenant_id=tenant_id).first()

        if not schedule:
            raise CustomError(ErrorCode.NOT_FOUND)

        # 更新字段（排除id）
        update_data = {k: v for k, v in data.items() if k != "id"}
        for key, value in update_data.items():
            setattr(schedule, key, value)
        await schedule.save()
    else:
        # 创建新配置
        schedule = await InspectionSchedule.create(**data, tenant_id=tenant_id)

    return schedule


async def notify_config(schedule_id: str, notify_enabled: bool, notify_config: Dict[str, Any], tenant_id: str) -> InspectionSchedule:
    """
    巡检通知配置
    inspection notification configuration
    """
    schedule = await InspectionSchedule.filter(id=schedule_id, deleted=False, tenant_id=tenant_id).first()

    if not schedule:
        raise CustomError(ErrorCode.SCHEDULE_NOT_FOUND)

    schedule.notify_enabled = notify_enabled
    schedule.notify_config = notify_config
    await schedule.save()

    return schedule


async def custom_report_config(schedule_id: str, custom_report_config: Dict[str, Any], tenant_id: str) -> InspectionSchedule:
    """
    巡检自定义报告配置
    inspection custom report configuration
    """
    schedule = await InspectionSchedule.filter(id=schedule_id, deleted=False, tenant_id=tenant_id).first()

    if not schedule:
        raise CustomError(ErrorCode.SCHEDULE_NOT_FOUND)

    schedule.custom_report_config = custom_report_config
    await schedule.save()

    return schedule


async def get_schedule(tenant_id: str) -> Optional[InspectionSchedule]:
    """
    获取租户的巡检调度配置（返回第一个，用于向后兼容）
    Get tenant's inspection schedule configuration (returns first one, for backward compatibility)
    """
    return await InspectionSchedule.filter(deleted=False, tenant_id=tenant_id).first()


async def get_schedule_by_id(schedule_id: str, tenant_id: str) -> Optional[InspectionSchedule]:
    """
    根据ID获取巡检调度配置
    Get inspection schedule configuration by ID
    """
    return await InspectionSchedule.filter(id=schedule_id, deleted=False, tenant_id=tenant_id).first()


async def list_schedules(page: int, page_size: int, tenant_id: str, enabled: Optional[bool] = None, keyword: Optional[str] = None) -> Dict[str, Any]:
    """
    获取巡检调度配置列表（分页）
    Get inspection schedule list (paginated)
    """
    query = InspectionSchedule.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if enabled is not None:
        query = query.filter(enabled=enabled)

    # 可选搜索关键词
    if keyword:
        query = query.filter(Q(name__icontains=keyword) | Q(description__icontains=keyword))

    # 计算总数
    total = await query.count()

    # 启用任务优先，其次按最新变更/创建时间倒序，避免新建任务落到最后一页。
    items = await query.order_by("-enabled", "-utime", "-ctime").offset((page - 1) * page_size).limit(page_size).all()

    # 获取所有 schedule_id 对应的巡检结果日期
    schedule_ids = [schedule.id for schedule in items]

    # 查询这些 schedule 的所有结果，只需要 schedule_id 和 start_time
    results = await InspectionResult.filter(deleted=False, tenant_id=tenant_id, schedule_id__in=schedule_ids).only("schedule_id", "start_time").all()

    # 构建 schedule_id -> 日期列表的映射
    schedule_dates_map = {}
    for result in results:
        if result.start_time:
            date_str = result.start_time.date().isoformat()
            if result.schedule_id not in schedule_dates_map:
                schedule_dates_map[result.schedule_id] = set()
            schedule_dates_map[result.schedule_id].add(date_str)

    # 转换为排序后的列表
    for schedule_id in schedule_dates_map:
        schedule_dates_map[schedule_id] = sorted(list(schedule_dates_map[schedule_id]), reverse=True)

    return {
        "total": total,
        "items": [
            {
                "id": schedule.id,
                "name": schedule.name,
                "schedule_cron": schedule.schedule_cron,
                "owner": schedule.owner,
                "enabled": schedule.enabled,
                "description": schedule.description,
                "result_dates": schedule_dates_map.get(schedule.id, []),
                "ctime": schedule.ctime.isoformat() if schedule.ctime else None,
                "utime": schedule.utime.isoformat() if schedule.utime else None,
                "notify_enabled": schedule.notify_enabled,
                "notify_config": schedule.notify_config,
                "custom_report_config": schedule.custom_report_config,
            }
            for schedule in items
        ],
    }


async def delete_schedule(schedule_id: str, tenant_id: str) -> bool:
    """
    删除巡检调度配置（软删除，级联删除关联的类别和巡检项，并清理定时任务）
    Delete inspection schedule configuration (soft delete, cascade delete related categories and targets, cleanup scheduled tasks)
    """
    schedule = await get_schedule_by_id(schedule_id, tenant_id)
    if not schedule:
        return False

    # 查找所有关联的类别
    categories = await InspectionCategory.filter(schedule_id=schedule_id, deleted=False, tenant_id=tenant_id).all()

    # 收集所有类别ID
    category_ids = [cat.id for cat in categories]

    # 软删除所有关联的巡检项
    if category_ids:
        await InspectionTarget.filter(category_id__in=category_ids, deleted=False, tenant_id=tenant_id).update(deleted=True)

    # 软删除所有关联的类别
    await InspectionCategory.filter(schedule_id=schedule_id, deleted=False, tenant_id=tenant_id).update(deleted=True)

    # 软删除关联的定时任务
    timed_name = f"inspection_{schedule_id}"
    await Timed.filter(name=timed_name, deleted=False, tenant_id=tenant_id).update(deleted=True)

    # 软删除调度配置
    schedule.deleted = True
    await schedule.save(update_fields=["deleted", "utime"])
    return True


# ==================== InspectionCategory Operations ====================


async def create_category(data: Dict[str, Any], tenant_id: str) -> InspectionCategory:
    """
    创建巡检类别
    Create inspection category
    """
    data = _normalize_category_data(data)
    category = await InspectionCategory.create(**data, tenant_id=tenant_id)
    return category


async def batch_create_categories(data_list: List[Dict[str, Any]], tenant_id: str) -> List[InspectionCategory]:
    """
    批量创建巡检类别
    Batch create inspection categories
    """
    categories = []
    for data in data_list:
        category = await create_category(data, tenant_id)
        categories.append(category)
    return categories


async def list_categories(page: int, page_size: int, tenant_id: str, schedule_id: Optional[str] = None, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """
    获取巡检类别列表（分页）
    Get inspection category list (paginated)
    """
    query = InspectionCategory.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if schedule_id:
        query = query.filter(schedule_id=schedule_id)
    if enabled is not None:
        query = query.filter(enabled=enabled)

    # 计算总数
    total = await query.count()

    # 分页查询
    items = await query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "items": [
            {
                "id": category.id,
                "name": category.name,
                "schedule_id": category.schedule_id,
                "app_name": category.app_name,
                "category_type": normalize_category_type(getattr(category, "category_type", None)),
                "resource_ids": category.resource_ids,
                "enabled": category.enabled,
                "ctime": category.ctime.isoformat() if category.ctime else None,
                "utime": category.utime.isoformat() if category.utime else None,
            }
            for category in items
        ],
    }


async def get_category(category_id: str, tenant_id: str) -> Optional[InspectionCategory]:
    """
    获取巡检类别详情
    Get inspection category details
    """
    return await InspectionCategory.filter(id=category_id, deleted=False, tenant_id=tenant_id).first()


async def update_category(category_id: str, data: Dict[str, Any], tenant_id: str) -> Optional[InspectionCategory]:
    """
    更新巡检类别
    Update inspection category
    """
    category = await get_category(category_id, tenant_id)
    if not category:
        return None

    if "category_type" not in data and "resource_ids" in data:
        data["category_type"] = _get_category_type(category)
    data = _normalize_category_data(data, partial=True)

    # 更新字段
    for key, value in data.items():
        if hasattr(category, key):
            setattr(category, key, value)

    await category.save()
    return category


async def delete_category(category_id: str, tenant_id: str) -> bool:
    """
    删除巡检类别（软删除，级联删除关联的巡检项）
    Delete inspection category (soft delete, cascade delete related targets)
    """
    category = await get_category(category_id, tenant_id)
    if not category:
        return False

    # 软删除所有关联的巡检项
    await InspectionTarget.filter(category_id=category_id, deleted=False, tenant_id=tenant_id).update(deleted=True)

    # 软删除类别
    category.deleted = True
    await category.save(update_fields=["deleted", "utime"])
    return True


# ==================== InspectionTarget Operations ====================


async def create_target(data: Dict[str, Any], tenant_id: str) -> InspectionTarget:
    """
    创建巡检目标
    Create inspection target
    """
    data = _normalize_target_data(data)
    target = await InspectionTarget.create(**data, tenant_id=tenant_id)
    return target


async def batch_create_targets(data_list: List[Dict[str, Any]], tenant_id: str) -> List[InspectionTarget]:
    """
    批量创建巡检目标
    Batch create inspection targets
    """
    # 使用循环创建以获取完整的对象信息（包括ID）
    targets = []
    for data in data_list:
        target = await create_target(data, tenant_id)
        targets.append(target)
    return targets


async def list_targets(page: int, page_size: int, tenant_id: str, schedule_id: Optional[str] = None, enabled: Optional[bool] = None, keyword: Optional[str] = None) -> Dict[str, Any]:
    """
    获取巡检目标列表（分页）
    Get inspection target list (paginated)
    """
    from collections import defaultdict

    # 1. 获取所有相关的巡检类别
    categories_query = InspectionCategory.filter(deleted=False, tenant_id=tenant_id)
    if schedule_id:
        categories_query = categories_query.filter(schedule_id=schedule_id)

    categories = await categories_query.all()
    category_ids = [cat.id for cat in categories]

    # 2. 获取所有相关的巡检目标
    targets_query = InspectionTarget.filter(deleted=False, tenant_id=tenant_id)
    if category_ids:
        targets_query = targets_query.filter(category_id__in=category_ids)
    elif schedule_id:
        return {"total": 0, "items": []}

    if enabled is not None:
        targets_query = targets_query.filter(enabled=enabled)

    if keyword is not None:
        targets_query = targets_query.filter(Q(content__icontains=keyword) | Q(standard__icontains=keyword))

    targets = await targets_query.all()

    # 3. 组织数据：按类别分组
    category_targets = defaultdict(list)
    for t in targets:
        category_targets[t.category_id].append(t)

    all_items = []
    # 遍历所有类别，确保每个类别至少出现一次
    for cat in categories:
        # 如果提供了 enabled 过滤，且类别状态不匹配，跳过
        if enabled is not None and cat.enabled != enabled:
            continue

        targets_in_cat = category_targets.get(cat.id, [])
        if not targets_in_cat and keyword is None:
            # 无 target 时返回 category 信息，target 相关字段设为 None
            all_items.append(
                {
                    "id": None,
                    "content": None,
                    "category_id": cat.id,
                    "category_name": cat.name,
                    "category_enabled": cat.enabled,
                    "app_name": cat.app_name,
                    "category_type": normalize_category_type(getattr(cat, "category_type", None)),
                    "standard": None,
                    "judgment_rule": None,
                    "action_name": None,
                    "version": None,
                    "action_input": None,
                    "enabled": None,
                    "ctime": cat.ctime.isoformat() if hasattr(cat, "ctime") and cat.ctime else None,
                    "utime": cat.utime.isoformat() if hasattr(cat, "utime") and cat.utime else None,
                }
            )
        else:
            for target in targets_in_cat:
                all_items.append(
                    {
                        "id": target.id,
                        "content": target.content,
                        "category_id": target.category_id,
                        "category_name": cat.name,
                        "category_enabled": cat.enabled,
                        "app_name": cat.app_name,
                        "category_type": normalize_category_type(getattr(cat, "category_type", None)),
                        "standard": target.standard,
                        "judgment_rule": target.judgment_rule,
                        "action_name": target.action_name,
                        "version": target.version,
                        "action_input": target.action_input or {},
                        "enabled": target.enabled,
                        "ctime": target.ctime.isoformat() if target.ctime else None,
                        "utime": target.utime.isoformat() if target.utime else None,
                    }
                )

    # 4. 手动分页
    total = len(all_items)
    start = (page - 1) * page_size
    end = start + page_size
    items = all_items[start:end]

    return {"total": total, "items": items}


async def get_target(target_id: str, tenant_id: str) -> Optional[InspectionTarget]:
    """
    获取巡检目标详情
    Get inspection target details
    """
    return await InspectionTarget.filter(id=target_id, deleted=False, tenant_id=tenant_id).first()


async def update_target(target_id: str, data: Dict[str, Any], tenant_id: str) -> Optional[InspectionTarget]:
    """
    更新巡检目标
    Update inspection target
    """
    target = await get_target(target_id, tenant_id)
    if not target:
        return None

    data = _normalize_target_data(data)

    # 更新字段
    for key, value in data.items():
        if hasattr(target, key):
            setattr(target, key, value)

    await target.save()
    return target


async def delete_target(target_id: str, tenant_id: str) -> bool:
    """
    删除巡检目标（软删除）
    Delete inspection target (soft delete)
    """
    target = await get_target(target_id, tenant_id)
    if not target:
        return False

    target.deleted = True
    await target.save()
    return True


async def get_all_configs(tenant_id: str, schedule_ids: Optional[Union[str, List[str]]] = None, target_ids: Optional[Union[str, List[str]]] = None) -> List[Dict[str, Any]]:
    """
    获取全部巡检目标配置
    Get all inspection target configurations
    """
    # 转换为列表处理
    if isinstance(schedule_ids, str):
        schedule_ids = [s.strip() for s in schedule_ids.split(",") if s.strip()]
    if isinstance(target_ids, str):
        target_ids = ast.literal_eval(target_ids)

    # 获取相关的巡检类别信息
    categories_query = InspectionCategory.filter(deleted=False, tenant_id=tenant_id, enabled=True)

    if schedule_ids:
        categories_query = categories_query.filter(schedule_id__in=schedule_ids)

    categories = await categories_query.all()

    if not categories:
        return []

    category_ids = [c.id for c in categories]
    category_map = {c.id: c for c in categories}
    # 获取巡检目标
    query = InspectionTarget.filter(deleted=False, tenant_id=tenant_id, enabled=True, category_id__in=category_ids)

    if target_ids:
        query = query.filter(id__in=target_ids)

    targets = await query.all()

    if not targets:
        return []

    # 获取相关的工具信息。空版本沿用最新版本；指定版本时优先命中对应版本。
    action_app_names = list(
        {
            c.app_name
            for c in categories
            if _get_category_type(c) == INSPECTION_CATEGORY_TYPE_ACTION and c.app_name
        }
    )
    tools = []
    if action_app_names:
        tools = await Tool.filter(
            name__in=action_app_names,
            tenant_id=tenant_id,
            maintain=False,
            deleted=False,
        ).order_by("-utime").all()

    latest_tool_map = {}
    version_tool_map = {}
    for tool in tools:
        if tool.name not in latest_tool_map:
            latest_tool_map[tool.name] = tool
        if tool.version and (tool.name, tool.version) not in version_tool_map:
            version_tool_map[(tool.name, tool.version)] = tool

    results = []
    for target in targets:
        category = category_map.get(target.category_id)
        if not category:
            continue

        category_type = _get_category_type(category)

        if category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK:
            playbook = await _resolve_playbook(category, tenant_id)
            if not playbook:
                raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

            playbook_version = await _resolve_playbook_version(playbook, target.version, tenant_id)
            if not playbook_version:
                raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

            results.append(_build_playbook_config(category, target, playbook, playbook_version, tenant_id))
            continue

        tool = version_tool_map.get((category.app_name, target.version)) if target.version else latest_tool_map.get(category.app_name)
        results.append(_build_action_config(category, target, tool))

    return results


# ==================== InspectionResult Operations ====================


def result_target_snapshot(result, target, field: str, default=None):
    snapshot = getattr(result, f"target_{field}", None)
    if snapshot is not None:
        return snapshot
    return getattr(target, field, default) if target else default


async def create_result(data: Dict[str, Any], tenant_id: str) -> InspectionResult:
    """
    创建巡检结果
    Create inspection result
    """
    target_id = data.get("target_id")
    if not target_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    target = await InspectionTarget.filter(id=target_id, deleted=False, tenant_id=tenant_id).first()

    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 获取 schedule_id: target -> category -> schedule
    category = await InspectionCategory.filter(id=target.category_id, deleted=False, tenant_id=tenant_id).first()

    if not category:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 添加 schedule_id 到数据中
    data["schedule_id"] = category.schedule_id
    data["target_content"] = target.content
    data["target_standard"] = target.standard

    result = await InspectionResult.create(**data, tenant_id=tenant_id)
    return result


async def list_results(
    page: int,
    page_size: int,
    tenant_id: str,
    target_id: Optional[str] = None,
    schedule_id: Optional[str] = None,
    status: Optional[str] = None,
    execution_status: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    keyword: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取巡检结果列表（分页）
    Get inspection result list (paginated)
    """
    query = InspectionResult.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if target_id:
        query = query.filter(target_id=target_id)
    if schedule_id:
        query = query.filter(schedule_id=schedule_id)
    if status:
        query = query.filter(status=status)
    if execution_status:
        query = query.filter(execution_status=execution_status)
    if start_time:
        query = query.filter(start_time__gte=start_time)
    if end_time:
        query = query.filter(end_time__lte=end_time)
    if keyword:
        matching_target_ids = await InspectionTarget.filter(Q(content__icontains=keyword) | Q(standard__icontains=keyword), tenant_id=tenant_id, deleted=False).values_list("id", flat=True)
        query = query.filter(
            Q(target_content__icontains=keyword)
            | Q(target_standard__icontains=keyword)
            | Q(target_content__isnull=True, target_id__in=matching_target_ids)
            | Q(target_standard__isnull=True, target_id__in=matching_target_ids)
        )

    # 计算总数
    total = await query.count()

    # 分页查询，按开始时间倒序
    items = await query.order_by("-start_time").offset((page - 1) * page_size).limit(page_size).all()

    # 获取所有相关的巡检项 ID 并批量查询巡检项详细信息
    target_ids = list(set(result.target_id for result in items if result.target_id))
    targets = await InspectionTarget.filter(id__in=target_ids, tenant_id=tenant_id, deleted=False).all()
    target_map = {target.id: target for target in targets}

    def get_result_reason(result_data):
        if not isinstance(result_data, dict):
            return None
        ai_judgment = result_data.get("ai_judgment")
        if not isinstance(ai_judgment, dict):
            nested_result = result_data.get("result")
            if isinstance(nested_result, dict):
                ai_judgment = nested_result.get("ai_judgment")
        return ai_judgment.get("reason") if isinstance(ai_judgment, dict) else None

    return {
        "total": total,
        "items": [
            {
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
                "target_content": result_target_snapshot(
                    result, target_map.get(result.target_id), "content"
                ),
                "target_standard": result_target_snapshot(
                    result, target_map.get(result.target_id), "standard"
                ),
                "reason": get_result_reason(result.result_data),
            }
            for result in items
        ],
    }


async def get_result(result_id: str, tenant_id: str) -> Optional[InspectionResult]:
    """
    获取巡检结果详情
    Get inspection result details
    """
    return await InspectionResult.filter(id=result_id, deleted=False, tenant_id=tenant_id).first()


async def get_stats(tenant_id: str, schedule_id: str, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    获取巡检统计信息
    Get inspection statistics
    """
    query = InspectionResult.filter(deleted=False, tenant_id=tenant_id, schedule_id=schedule_id)

    # 可选过滤条件
    if start_time:
        query = query.filter(start_time__gte=start_time)
    if end_time:
        query = query.filter(end_time__lte=end_time)

    # 总执行次数
    total_executions = await query.count()

    # 各状态统计
    success_count = await query.filter(status="success").count()
    failed_count = await query.filter(status="failed").count()

    # 成功率
    success_rate = success_count / total_executions if total_executions > 0 else 0

    return {"success_rate": round(success_rate, 2), "success_count": success_count, "failed_count": failed_count, "total_count": success_count + failed_count}


# ==================== AI Parsing Operations ====================

INSPECTION_PARSE_PROMPT = """
你是一个专业的IT运维巡检专家。请分析用户提供的巡检描述，推荐最合适的巡检配置。

## 任务要求

1. **巡检标准** (standard): 提取或推断明确的成功标准或阈值
   - 应该是可量化、可验证的标准
   - 例如："CPU使用率<80%"、"响应时间<1秒"、"服务状态为running"

2. **动作名称** (action_name): 从可用动作列表中选择最合适的一个
   - 必须从下面的可用动作中选择
   - 选择最能满足巡检需求的动作

## 可用动作列表

{available_actions}

## 用户巡检描述

{user_input}

## 输出要求

返回一个JSON格式的字符串，包含以下字段：
- standard (string): 巡检标准，如果无法确定则为null
- action_name (string): 选择的动作名称，必须从可用动作列表中选择
- confidence (number): 推荐置信度，范围0-1，表示对推荐结果的信心程度
- suggestions (array): 配置建议列表，提供额外的配置提示或注意事项

注意：
1. 直接返回JSON格式的字符串，不要使用markdown代码块
2. action_name必须是可用动作列表中的一个
3. 如果用户描述不清晰，降低confidence值并在suggestions中说明
4. suggestions应该提供实用的配置建议，而不是通用建议
"""


async def parse_inspection_content(app_name: str, text: str, tenant_id: str, category_type: str = INSPECTION_CATEGORY_TYPE_ACTION, version: Optional[str] = None) -> Dict[str, Any]:
    """
    使用AI解析巡检内容
    Parse inspection content using AI

    Args:
        app_name: 应用名称（对应Tool.name）
        text: 用户输入的巡检描述
        tenant_id: 租户ID

    Returns:
        解析后的结构化数据
    """
    category_type = normalize_category_type(category_type)
    version = _normalize_version(version)

    extra_fields = {}

    if category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK:
        playbook = await _resolve_playbook_by_key(app_name, tenant_id)
        if not playbook:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

        playbook_version = await _resolve_playbook_version(playbook, version, tenant_id)
        if not playbook_version:
            raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

        available_actions = [{"name": playbook.name}]
        extra_fields = {
            "category_type": INSPECTION_CATEGORY_TYPE_PLAYBOOK,
            "playbook_id": playbook.id,
            "playbook_name": playbook.name,
            "flow_id": playbook_version.id,
            "version": playbook_version.version,
        }
    else:
        # 查询工具信息。未指定版本时沿用最新版本。
        tool_query = Tool.filter(name=app_name, deleted=False, tenant_id=tenant_id)
        if version:
            tool_query = tool_query.filter(version=version)
        tool = await tool_query.order_by("-utime").first()

        if not tool:
            raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

        # 查询该工具的所有动作
        tool_actions = await ToolAction.filter(tool_name=tool.name, version_number=tool.version, deleted=False, tenant_id=tenant_id).all()

        if not tool_actions:
            raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

        # 提取动作名称和输入定义
        available_actions = []
        action_map = {}  # 用于后续查找
        for action in tool_actions:
            if action.action:
                action_name = action.action.get("func")
                if action_name:
                    available_actions.append({"name": action_name})
                    action_map[action_name] = action

        if not available_actions:
            raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    # 准备prompt参数。剧本类别也走 AI 解析，保留 standard/confidence/suggestions。
    available_actions_str = orjson.dumps(available_actions, option=orjson.OPT_INDENT_2).decode("utf-8")

    prompt = INSPECTION_PARSE_PROMPT.format(user_input=text, available_actions=available_actions_str)

    # 使用litellm调用AI服务
    from services.llm_config.resolver import resolve_llm

    llm = await resolve_llm(tenant_id)
    model = llm.model_name
    api_base = llm.base_url
    api_key = llm.api_key

    # 如果使用自定义API base且模型名称不包含provider前缀，自动添加
    if api_base and "/" not in model:
        model = f"openai/{model}"

    # 构建acompletion参数 - 针对GPT-5.4优化
    completion_params = {
        "model": model,
        "messages": [{"role": "system", "content": "你是一个专业的IT运维巡检专家，擅长分析巡检需求并提供精确的配置建议。"}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": 1000,  # 限制输出长度
    }

    # GPT-5系列模型只支持temperature=1，其他模型可以使用自定义temperature
    if "gpt-5" not in model.lower():
        completion_params["temperature"] = 0.2

    # 如果配置了API base URL，则添加到参数中
    if api_base:
        completion_params["api_base"] = api_base

    # 如果配置了API key，则添加到参数中
    if api_key:
        completion_params["api_key"] = api_key

    # 重试逻辑
    max_retries = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            response = await _acompletion(**completion_params)

            # 解析AI返回的JSON
            ai_response = response.choices[0].message.content
            parsed_data = orjson.loads(ai_response)

            # 验证必需字段
            required_fields = ["standard", "action_name", "confidence", "suggestions"]
            for field in required_fields:
                if field not in parsed_data:
                    if field == "suggestions":
                        parsed_data[field] = []
                    elif field == "confidence":
                        parsed_data[field] = 0.5
                    else:
                        parsed_data[field] = None

            # 验证action_name是否在可用列表中
            action_names = [a["name"] for a in available_actions]
            if parsed_data.get("action_name") not in action_names:
                if not isinstance(parsed_data.get("suggestions"), list):
                    parsed_data["suggestions"] = []
                if category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK:
                    parsed_data["suggestions"].append(f"当前类别为剧本类型，已使用剧本 '{available_actions[0]['name']}'")
                else:
                    parsed_data["suggestions"].append(f"AI推荐的动作 '{parsed_data.get('action_name')}' 不在可用列表中，已自动选择默认动作")
                    parsed_data["confidence"] = max(0, float(parsed_data.get("confidence", 0)) - 0.3)
                parsed_data["action_name"] = available_actions[0]["name"] if available_actions else None

            # 验证confidence范围
            if isinstance(parsed_data.get("confidence"), (int, float)):
                parsed_data["confidence"] = max(0.0, min(1.0, float(parsed_data["confidence"])))
            else:
                parsed_data["confidence"] = 0.5

            parsed_data.update(extra_fields)

            # 成功解析，返回结果
            return parsed_data

        except orjson.JSONDecodeError as e:
            last_error = f"JSON解析失败: {str(e)}"
            if attempt < max_retries - 1:
                continue  # 重试
        except Exception as e:
            last_error = f"AI服务调用失败: {str(e)}"
            if attempt < max_retries - 1:
                continue  # 重试

    # 所有重试都失败，返回默认结构
    fallback_data = {"standard": None, "action_name": available_actions[0]["name"] if available_actions else None, "confidence": 0.0, "suggestions": [last_error or "AI解析失败，请手动配置"]}
    fallback_data.update(extra_fields)
    return fallback_data


# ==================== Judgment Rule Operations ====================


def _extract_json_object(text: Any) -> Dict[str, Any]:
    """
    从 AI 返回内容中提取 JSON 对象，兼容纯 JSON、markdown fenced code、
    以及前后带解释文本的情况。
    """
    if isinstance(text, (bytes, bytearray)):
        text = text.decode()
    if not isinstance(text, str):
        raise ValueError("AI response is not text")

    content = text.strip()
    if not content:
        raise ValueError("AI response is empty")

    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        parsed = orjson.loads(content)
    except orjson.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = orjson.loads(content[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("AI response JSON is not an object")

    return parsed


def _normalize_ai_judgment(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    规范化 AI 巡检裁决结果，保持 /inspection/result/add 下游字段契约不变。
    """
    passed = parsed_data.get("passed", False)
    if isinstance(passed, str):
        parsed_data["passed"] = passed.strip().lower() in ("true", "1", "yes", "y", "通过", "符合", "pass", "passed")
    else:
        parsed_data["passed"] = bool(passed)

    if "reason" not in parsed_data or not parsed_data["reason"]:
        parsed_data["reason"] = "AI未提供判断理由"
    elif not isinstance(parsed_data["reason"], str):
        parsed_data["reason"] = str(parsed_data["reason"])

    if "diagnosis" not in parsed_data:
        parsed_data["diagnosis"] = ""
    elif not isinstance(parsed_data["diagnosis"], str):
        parsed_data["diagnosis"] = str(parsed_data["diagnosis"]) if parsed_data["diagnosis"] else ""

    if "suggestions" not in parsed_data:
        parsed_data["suggestions"] = []
    elif isinstance(parsed_data["suggestions"], str):
        parsed_data["suggestions"] = [parsed_data["suggestions"]]
    elif not isinstance(parsed_data["suggestions"], list):
        parsed_data["suggestions"] = []

    return parsed_data


_RULE_VALUE_MISSING = object()
JUDGMENT_RULE_VERSION = 1
JUDGMENT_RULE_OPERATOR_ALIASES = {
    "eq": "==",
    "equals": "==",
    "equal": "==",
    "is": "==",
    "=": "==",
    "ne": "!=",
    "not_equals": "!=",
    "not_equal": "!=",
    "<>": "!=",
    "gt": ">",
    "greater_than": ">",
    "gte": ">=",
    "ge": ">=",
    "greater_than_or_equal": ">=",
    "lt": "<",
    "less_than": "<",
    "lte": "<=",
    "le": "<=",
    "less_than_or_equal": "<=",
    "include": "contains",
    "includes": "contains",
    "not_include": "not_contains",
    "not_includes": "not_contains",
    "exist": "exists",
    "not_exist": "not_exists",
    "is_true": "truthy",
    "true": "truthy",
    "is_false": "falsy",
    "false": "falsy",
    "matches": "regex",
    "not_matches": "not_regex",
}


def _json_text(value: Any) -> str:
    try:
        return orjson.dumps(value, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS).decode("utf-8")
    except Exception:
        return str(value)


def _estimate_input_tokens(value: str) -> int:
    return (len(value.encode("utf-8")) + 3) // 4


def _structured_result_sample(
    value: Any,
    *,
    max_depth: int,
    max_dict_items: int,
    max_list_items: int,
    max_string_chars: int,
    depth: int = 0,
) -> Any:
    value = _maybe_parse_nested_json(value)
    if depth >= max_depth:
        if isinstance(value, dict):
            return {}
        if isinstance(value, (list, tuple)):
            return []

    if isinstance(value, dict):
        return {
            key: _structured_result_sample(
                item,
                max_depth=max_depth,
                max_dict_items=max_dict_items,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:max_dict_items]
        }
    if isinstance(value, (list, tuple)):
        if len(value) <= max_list_items:
            selected = value
        elif max_list_items == 1:
            selected = value[:1]
        else:
            indexes = {0, len(value) // 2, len(value) - 1}
            selected = [value[index] for index in sorted(indexes)[:max_list_items]]
        return [
            _structured_result_sample(
                item,
                max_depth=max_depth,
                max_dict_items=max_dict_items,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
                depth=depth + 1,
            )
            for item in selected
        ]
    if isinstance(value, str) and len(value) > max_string_chars:
        return value[:max_string_chars]
    return value


def _fit_result_sample(result_data: Any, max_bytes: int) -> tuple[str, bool, int]:
    original_text = _json_text(result_data)
    original_chars = len(original_text)
    if len(original_text.encode("utf-8")) <= max_bytes:
        return original_text, False, original_chars

    for max_depth, max_dict_items, max_list_items, max_string_chars in _INSPECTION_SAMPLE_PROFILES:
        sampled = _structured_result_sample(
            result_data,
            max_depth=max_depth,
            max_dict_items=max_dict_items,
            max_list_items=max_list_items,
            max_string_chars=max_string_chars,
        )
        sample_text = _json_text(sampled)
        if len(sample_text.encode("utf-8")) <= max_bytes:
            return sample_text, True, original_chars
    raise ValueError("巡检结果的字段结构超过模型输入预算，未调用大模型")


def _build_judgment_rule_message(
    target: InspectionTarget,
    result_data: Any,
) -> tuple[str, bool, int]:
    sample_placeholder = "__INSPECTION_RESULT_SAMPLE__"
    message_template = f"""<system_instruction>
【身份】你是 AIOE 平台的巡检判断规则生成助手。

【执行规则】
1. 只根据用户提供的巡检标准生成结构化规则。
2. 本次巡检结果样例只用于定位字段路径，不允许为了适配当前结果而放宽标准。
3. 不要调用任何工具、skill 或外部接口。
4. 只能输出一个 JSON 对象，禁止输出 markdown、代码块或额外解释。
</system_instruction>

<inspection_judgment_rule_task>
你是一个专业的IT运维巡检规则生成助手。请根据巡检标准生成可复用的本地判断规则。

## 巡检项

{target.content}

## 巡检标准

{target.standard}

## 本次巡检结果结构化样例

{sample_placeholder}

## 任务要求

1. 只生成用于后续本地判断的规则，不要直接判断本次结果是否通过。
2. 判断条件必须来源于巡检标准；本次巡检结果样例只用于识别字段路径和数据类型。
3. 字段路径必须能从巡检结果样例中读取，支持点路径和数组下标，例如 data.cpu_usage、items[0].status。
4. 如果标准包含多个条件，用 conditions 表达，并设置 logic 为 all 或 any。
5. 不要调用任何工具、skill 或外部接口。

## 可用 operator

==、!=、>、>=、<、<=、between、contains、not_contains、in、not_in、exists、not_exists、regex、not_regex、truthy、falsy。

## 输出要求

只能返回一个 JSON 对象，不要使用 markdown 代码块。格式如下：
{{
  "logic": "all",
  "description": "规则说明",
  "conditions": [
    {{
      "path": "巡检结果中的字段路径",
      "operator": "<=",
      "value": 80,
      "value_type": "number",
      "description": "CPU 使用率需要小于等于 80%"
    }}
  ],
  "success_reason": "巡检结果符合判断规则",
  "failure_reason": "巡检结果不符合判断规则",
  "failure_diagnosis": "失败时的排查方向",
  "failure_suggestions": ["失败时的处理建议"]
}}
</inspection_judgment_rule_task>"""

    budget_bytes = INSPECTION_RULE_INPUT_MAX_TOKENS * 4
    fixed_bytes = len(message_template.replace(sample_placeholder, "").encode("utf-8"))
    sample_budget = budget_bytes - fixed_bytes
    if sample_budget < 1024:
        raise ValueError("巡检项和巡检标准超过模型输入预算，未调用大模型")

    sample_notice = (
        "说明：原始结果共 {original_chars} 字符，"
        "以下仅保留字段结构和代表性数组项。\n"
    )
    sample_text, sampled, original_chars = _fit_result_sample(
        result_data,
        sample_budget - len(sample_notice.format(original_chars=0).encode("utf-8")),
    )
    if sampled:
        sample_text = sample_notice.format(original_chars=original_chars) + sample_text
    message = message_template.replace(sample_placeholder, sample_text)
    if _estimate_input_tokens(message) > INSPECTION_RULE_INPUT_MAX_TOKENS:
        raise ValueError("巡检规则生成输入超过预算，未调用大模型")
    return message, sampled, original_chars


def _value_text(value: Any, max_length: int = 300) -> str:
    if value is _RULE_VALUE_MISSING:
        return "未找到"
    if isinstance(value, (dict, list)):
        text = _json_text(value)
    else:
        text = "" if value is None else str(value)
    return text if len(text) <= max_length else f"{text[:max_length]}..."


def _normalize_rule_operator(operator: Any) -> str:
    operator = str(operator or "==").strip().lower()
    return JUDGMENT_RULE_OPERATOR_ALIASES.get(operator, operator)


def _get_condition_expected_value(condition: Dict[str, Any]) -> Any:
    for key in ("value", "expected", "expected_value", "threshold"):
        if key in condition:
            return condition[key]
    return None


def _normalize_rule_suggestions(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return []


def _normalize_judgment_rule(rule_data: Any) -> Dict[str, Any]:
    """规范化可复用的巡检判断规则，供本地规则引擎执行。"""
    if isinstance(rule_data, str):
        try:
            rule_data = orjson.loads(rule_data)
        except orjson.JSONDecodeError:
            raise ValueError("判断规则不是有效 JSON")

    if not isinstance(rule_data, dict):
        raise ValueError("判断规则必须是 JSON 对象")

    if isinstance(rule_data.get("judgment_rule"), dict):
        rule_data = rule_data["judgment_rule"]

    conditions = rule_data.get("conditions") or rule_data.get("rules") or rule_data.get("checks")
    if not conditions and any(key in rule_data for key in ("path", "field", "field_path", "key")):
        conditions = [rule_data]
    if isinstance(conditions, dict):
        conditions = [conditions]
    if not isinstance(conditions, list):
        raise ValueError("判断规则缺少 conditions")

    normalized_conditions = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue

        path = condition.get("path")
        if path in (None, ""):
            path = condition.get("field_path")
        if path in (None, ""):
            path = condition.get("field")
        if path in (None, ""):
            path = condition.get("key")

        operator = _normalize_rule_operator(condition.get("operator") or condition.get("op"))
        expected_value = _get_condition_expected_value(condition)
        description = condition.get("description") or condition.get("name") or condition.get("label") or ""

        normalized_condition = {
            "path": path if path is not None else "",
            "operator": operator,
            "value": expected_value,
            "value_type": str(condition.get("value_type") or condition.get("type") or "").strip().lower(),
            "description": str(description),
            "case_sensitive": bool(condition.get("case_sensitive", False)),
        }
        if condition.get("value_path") not in (None, ""):
            normalized_condition["value_path"] = condition.get("value_path")
        normalized_conditions.append(normalized_condition)

    if not normalized_conditions:
        raise ValueError("判断规则没有可执行条件")

    logic = str(rule_data.get("logic") or rule_data.get("condition_logic") or "all").strip().lower()
    if logic in ("or", "any_of", "任一"):
        logic = "any"
    elif logic not in ("all", "any"):
        logic = "all"

    return {
        "version": rule_data.get("version") or JUDGMENT_RULE_VERSION,
        "logic": logic,
        "conditions": normalized_conditions,
        "description": str(rule_data.get("description") or ""),
        "success_reason": str(rule_data.get("success_reason") or ""),
        "failure_reason": str(rule_data.get("failure_reason") or ""),
        "failure_diagnosis": str(rule_data.get("failure_diagnosis") or rule_data.get("diagnosis") or ""),
        "failure_suggestions": _normalize_rule_suggestions(rule_data.get("failure_suggestions") or rule_data.get("suggestions")),
        "source_standard": rule_data.get("source_standard"),
        "generated_by": rule_data.get("generated_by"),
    }


def _parse_rule_path(path: Any) -> List[str]:
    if path in (None, ""):
        return []

    raw_parts = []
    if isinstance(path, (list, tuple)):
        for part in path:
            if part in (None, ""):
                continue
            raw_parts.extend(str(part).split("."))
    else:
        path_str = str(path).strip()
        for prefix in ("$.", "result_data.", "result."):
            if path_str.startswith(prefix):
                path_str = path_str[len(prefix) :]
        raw_parts = path_str.split(".")

    parsed_parts = []
    part_pattern = r"([^\[\]]+)|\[(?:'([^']+)'|\"([^\"]+)\"|(\d+))\]"
    for raw_part in raw_parts:
        part = raw_part.strip()
        if not part:
            continue
        matches = list(re.finditer(part_pattern, part))
        if matches and "".join(match.group(0) for match in matches) == part:
            for match in matches:
                parsed_parts.append(next(group for group in match.groups() if group is not None).strip())
        else:
            parsed_parts.append(part)

    return parsed_parts


def _maybe_parse_nested_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return value

    try:
        return orjson.loads(stripped)
    except orjson.JSONDecodeError:
        try:
            return ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            return value


def _dict_get_case_insensitive(data: Dict[str, Any], key: str) -> Any:
    if key in data:
        return data[key]

    key_lower = str(key).lower()
    for item_key, item_value in data.items():
        if str(item_key).lower() == key_lower:
            return item_value

    return _RULE_VALUE_MISSING


def _find_value_by_key(data: Any, key: str) -> Any:
    data = _maybe_parse_nested_json(data)
    if isinstance(data, dict):
        value = _dict_get_case_insensitive(data, key)
        if value is not _RULE_VALUE_MISSING:
            return value
        for item_value in data.values():
            nested_value = _find_value_by_key(item_value, key)
            if nested_value is not _RULE_VALUE_MISSING:
                return nested_value
    elif isinstance(data, list):
        for item in data:
            nested_value = _find_value_by_key(item, key)
            if nested_value is not _RULE_VALUE_MISSING:
                return nested_value
    return _RULE_VALUE_MISSING


def _get_rule_path_value(data: Any, path: Any) -> Any:
    parts = _parse_rule_path(path)
    if not parts:
        return data

    current = data
    for part in parts:
        current = _maybe_parse_nested_json(current)
        if isinstance(current, dict):
            current = _dict_get_case_insensitive(current, part)
            if current is _RULE_VALUE_MISSING:
                return _find_value_by_key(data, parts[-1])
            continue

        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return _find_value_by_key(data, parts[-1])
            continue

        return _find_value_by_key(data, parts[-1])

    return current


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group(0))
    return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("false", "0", "no", "n", "否", "不", "失败", "failed", "fail"):
            return False
        if value in ("true", "1", "yes", "y", "是", "成功", "success", "pass", "passed"):
            return True
    return bool(value)


def _values_equal(actual: Any, expected: Any, case_sensitive: bool = False) -> bool:
    actual_number = _to_number(actual)
    expected_number = _to_number(expected)
    if actual_number is not None and expected_number is not None:
        return actual_number == expected_number

    if isinstance(actual, str) or isinstance(expected, str):
        actual_text = "" if actual is None else str(actual)
        expected_text = "" if expected is None else str(expected)
        if not case_sensitive:
            actual_text = actual_text.lower()
            expected_text = expected_text.lower()
        return actual_text == expected_text

    return actual == expected


def _contains_value(actual: Any, expected: Any, case_sensitive: bool = False) -> bool:
    if isinstance(actual, dict):
        return any(_values_equal(key, expected, case_sensitive) for key in actual.keys()) or any(_values_equal(value, expected, case_sensitive) for value in actual.values())
    if isinstance(actual, list):
        return any(_values_equal(item, expected, case_sensitive) for item in actual)

    actual_text = "" if actual is None else str(actual)
    expected_text = "" if expected is None else str(expected)
    if not case_sensitive:
        actual_text = actual_text.lower()
        expected_text = expected_text.lower()
    return expected_text in actual_text


def _evaluate_rule_condition(condition: Dict[str, Any], result_data: Any) -> tuple[bool, str]:
    path = condition.get("path")
    operator = _normalize_rule_operator(condition.get("operator"))
    value_type = condition.get("value_type")
    case_sensitive = bool(condition.get("case_sensitive", False))
    actual_value = _get_rule_path_value(result_data, path)
    exists = actual_value is not _RULE_VALUE_MISSING and actual_value is not None
    expected_value = condition.get("value")

    if condition.get("value_path") not in (None, ""):
        expected_value = _get_rule_path_value(result_data, condition.get("value_path"))
        if expected_value is _RULE_VALUE_MISSING:
            return False, f"{condition.get('description') or path}: 未找到对比字段 {condition.get('value_path')}"

    description = condition.get("description") or f"{path or '结果'} {operator} {_value_text(expected_value)}"

    if operator == "exists":
        return exists, f"{description}: 字段{'存在' if exists else '不存在'}"
    if operator == "not_exists":
        return not exists, f"{description}: 字段{'不存在' if not exists else '存在'}"
    if operator == "not_empty":
        passed = exists and actual_value not in ("", [], {}, ())
        return passed, f"{description}: 实际值 {_value_text(actual_value)}"
    if operator == "is_empty":
        passed = (not exists) or actual_value in ("", [], {}, ())
        return passed, f"{description}: 实际值 {_value_text(actual_value)}"

    if not exists:
        return False, f"{description}: 结果中未找到字段 {path}"

    if operator in (">", ">=", "<", "<="):
        actual_number = _to_number(actual_value)
        expected_number = _to_number(expected_value)
        if actual_number is None or expected_number is None:
            return False, f"{description}: 数值转换失败，实际值 {_value_text(actual_value)}，期望值 {_value_text(expected_value)}"
        if operator == ">":
            passed = actual_number > expected_number
        elif operator == ">=":
            passed = actual_number >= expected_number
        elif operator == "<":
            passed = actual_number < expected_number
        elif operator == "<=":
            passed = actual_number <= expected_number
        else:
            passed = actual_number == expected_number
        return passed, f"{description}: 实际值 {_value_text(actual_value)}，期望 {operator} {_value_text(expected_value)}"

    if operator in ("between", "range"):
        if not isinstance(expected_value, (list, tuple)) or len(expected_value) != 2:
            return False, f"{description}: between 规则需要两个边界值"
        actual_number = _to_number(actual_value)
        lower_number = _to_number(expected_value[0])
        upper_number = _to_number(expected_value[1])
        if actual_number is None or lower_number is None or upper_number is None:
            return False, f"{description}: 数值转换失败，实际值 {_value_text(actual_value)}，期望范围 {_value_text(expected_value)}"
        passed = lower_number <= actual_number <= upper_number
        return passed, f"{description}: 实际值 {_value_text(actual_value)}，期望范围 [{_value_text(expected_value[0])}, {_value_text(expected_value[1])}]"

    if operator in ("==", "!="):
        passed = _values_equal(actual_value, expected_value, case_sensitive)
        if operator == "!=":
            passed = not passed
        return passed, f"{description}: 实际值 {_value_text(actual_value)}，期望 {operator} {_value_text(expected_value)}"

    if operator in ("contains", "not_contains"):
        passed = _contains_value(actual_value, expected_value, case_sensitive)
        if operator == "not_contains":
            passed = not passed
        return passed, f"{description}: 实际值 {_value_text(actual_value)}，期望 {operator} {_value_text(expected_value)}"

    if operator in ("in", "not_in"):
        expected_values = expected_value if isinstance(expected_value, list) else [expected_value]
        passed = any(_values_equal(actual_value, item, case_sensitive) for item in expected_values)
        if operator == "not_in":
            passed = not passed
        return passed, f"{description}: 实际值 {_value_text(actual_value)}，期望 {operator} {_value_text(expected_values)}"

    if operator in ("regex", "not_regex"):
        flags = 0 if case_sensitive else re.IGNORECASE
        passed = re.search(str(expected_value), str(actual_value), flags=flags) is not None
        if operator == "not_regex":
            passed = not passed
        return passed, f"{description}: 实际值 {_value_text(actual_value)}，正则 {_value_text(expected_value)}"

    if operator in ("truthy", "falsy"):
        passed = _to_bool(actual_value)
        if operator == "falsy":
            passed = not passed
        return passed, f"{description}: 实际值 {_value_text(actual_value)}"

    passed = _values_equal(actual_value, expected_value, case_sensitive)
    return passed, f"{description}: 实际值 {_value_text(actual_value)}，期望 == {_value_text(expected_value)}"


def _judge_result_by_rule(rule: Dict[str, Any], result_data: Any, rule_generated: bool = False) -> Dict[str, Any]:
    normalized_rule = _normalize_judgment_rule(rule)
    evaluations = [_evaluate_rule_condition(condition, result_data) for condition in normalized_rule["conditions"]]
    logic = normalized_rule.get("logic") or "all"
    passed = any(item[0] for item in evaluations) if logic == "any" else all(item[0] for item in evaluations)
    matched_details = [detail for is_passed, detail in evaluations if is_passed]
    failed_details = [detail for is_passed, detail in evaluations if not is_passed]

    if passed:
        detail = "; ".join(matched_details)[:1000]
        reason = normalized_rule.get("success_reason") or "巡检结果符合判断规则"
        if detail:
            reason = f"{reason}: {detail}"
        diagnosis = ""
        suggestions = []
    else:
        details = failed_details if logic == "all" else [detail for _, detail in evaluations]
        detail = "; ".join(details)[:1000]
        reason = normalized_rule.get("failure_reason") or "巡检结果不符合判断规则"
        if detail:
            reason = f"{reason}: {detail}"
        diagnosis = normalized_rule.get("failure_diagnosis") or "请根据未满足的判断规则检查对应巡检指标和业务配置。"
        suggestions = normalized_rule.get("failure_suggestions") or []

    return _normalize_ai_judgment(
        {
            "passed": passed,
            "reason": reason,
            "diagnosis": diagnosis,
            "suggestions": suggestions,
            "judgment_source": "rule",
            "rule_generated": rule_generated,
            "rule_version": normalized_rule.get("version"),
            "rule_description": normalized_rule.get("description") or "",
        }
    )


async def _generate_inspection_judgment_rule(target: InspectionTarget, result_data: Any, tenant_id: str) -> Dict[str, Any]:
    """首次巡检结果到达时由 lion_agent 生成可复用判断规则。"""
    from services.llm_config.resolver import resolve_llm

    session_key = f"inspection:judgment_rule:{target.id}:{uuid.uuid4().hex}"
    full_message, sampled, original_chars = _build_judgment_rule_message(target, result_data)
    if sampled:
        logger.info(
            "巡检规则生成输入已结构化采样: target_id=%s, original_chars=%s, estimated_tokens=%s",
            target.id,
            original_chars,
            _estimate_input_tokens(full_message),
        )

    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.info(f"准备调用 lion_agent 生成巡检判断规则，target_id={target.id}, tenant_id={tenant_id}")
            ai_response = await execute_prompt(
                workspace_id=workspace_id_for(tenant_id, "inspection"),
                session_id=session_key,
                request_id=f"inspection:judgment_rule:{target.id}",
                message=full_message,
                llm=await resolve_llm(tenant_id),
                memory=identity_only_memory(tenant_id),
            )

            if not ai_response or not ai_response.strip():
                last_error = f"lion_agent返回空规则内容，已重试 {attempt + 1}/{max_retries}"
                if attempt < max_retries - 1:
                    continue
                break

            judgment_rule = _normalize_judgment_rule(_extract_json_object(ai_response))
            judgment_rule["source_standard"] = target.standard
            judgment_rule["generated_by"] = "lion_agent"
            return judgment_rule
        except (orjson.JSONDecodeError, ValueError) as e:
            last_error = f"判断规则解析失败: {str(e)}"
            if attempt < max_retries - 1:
                continue
        except LionAgentExecutionError as e:
            last_error = f"lion_agent生成判断规则失败[{e.code}]: {str(e)}"
            if e.retryable and attempt < max_retries - 1:
                continue
            break
        except Exception as e:
            last_error = f"lion_agent生成判断规则失败: {str(e)}"
            if attempt < max_retries - 1:
                continue

    raise ValueError(last_error or "lion_agent生成判断规则失败")


async def judge_inspection_result(target_id: str, result_data: Any, tenant_id: str) -> Dict[str, Any]:
    """
    使用巡检项上的判断规则判断巡检结果。
    首次没有规则时由 lion_agent 根据巡检标准生成规则并持久化，后续直接本地判断。
    """
    target = await InspectionTarget.filter(id=target_id, deleted=False, tenant_id=tenant_id).first()

    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    rule_generated = False
    judgment_rule = target.judgment_rule

    if not judgment_rule:
        try:
            judgment_rule = await _generate_inspection_judgment_rule(target, result_data, tenant_id)
            target.judgment_rule = judgment_rule
            await target.save()
            rule_generated = True
        except Exception as e:
            logger.warning(f"生成巡检判断规则失败: target_id={target_id}, error={e}")
            return _normalize_ai_judgment(
                {
                    "passed": False,
                    "reason": f"生成巡检判断规则失败: {str(e)}",
                    "diagnosis": "lion_agent未能生成可执行判断规则，无法进行本地规则判断。建议检查巡检标准是否明确，或人工配置判断规则。",
                    "suggestions": ["请检查巡检标准是否可量化", "请人工复核巡检结果或补充判断规则"],
                    "judgment_source": "rule_generation_failed",
                    "rule_generated": False,
                }
            )

    try:
        return _judge_result_by_rule(judgment_rule, result_data, rule_generated=rule_generated)
    except Exception as e:
        logger.warning(f"巡检判断规则执行失败: target_id={target_id}, error={e}")
        return _normalize_ai_judgment(
            {
                "passed": False,
                "reason": f"巡检判断规则执行失败: {str(e)}",
                "diagnosis": "已存在的巡检判断规则无法执行。建议清空判断规则后重新生成，或人工修正规则字段路径和操作符。",
                "suggestions": ["请检查判断规则字段路径是否匹配当前结果结构", "如巡检标准或动作输出已变化，请清空判断规则后重新生成"],
                "judgment_source": "rule_execution_failed",
                "rule_generated": rule_generated,
            }
        )


# ==================== Import/Export Operations ====================


async def export_targets_to_excel(target_ids: List[str], tenant_id: str) -> bytes:
    """
    导出巡检项及其关联的类别和任务为Excel文件（单表格式，带单元格合并和说明）
    Export inspection targets with their categories and schedules to Excel (single sheet with merged cells and instructions)

    Args:
        target_ids: 巡检项ID列表
        tenant_id: 租户ID

    Returns:
        Excel文件的字节数据
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO
    from collections import defaultdict

    # 查询巡检项
    targets = await InspectionTarget.filter(id__in=target_ids, deleted=False, tenant_id=tenant_id).all()

    if not targets:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 获取所有关联的类别ID
    category_ids = list(set(t.category_id for t in targets))
    categories = await InspectionCategory.filter(id__in=category_ids, deleted=False, tenant_id=tenant_id).all()

    # 获取所有关联的任务ID
    schedule_ids = list(set(c.schedule_id for c in categories))
    schedules = await InspectionSchedule.filter(id__in=schedule_ids, deleted=False, tenant_id=tenant_id).all()

    # 创建映射表
    schedule_map = {s.id: s for s in schedules}
    category_map = {c.id: c for c in categories}

    # 按任务和类别组织数据
    data_structure = defaultdict(lambda: defaultdict(list))
    for target in targets:
        category = category_map.get(target.category_id)
        if not category:
            continue
        schedule = schedule_map.get(category.schedule_id)
        if not schedule:
            continue

        data_structure[schedule.id][category.id].append(target)

    # 创建Excel工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "巡检配置"

    # 定义样式
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    center_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # 边框样式
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

    # 设置表头
    headers = ["任务名称", "Cron表达式", "负责人", "任务描述", "类别名称", "类别类型", "应用名称", "资源ID列表", "巡检内容", "巡检标准", "动作/剧本名称", "版本"]
    ws.append(headers)

    # 应用表头样式
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # 添加填写说明（第2-23行，整体合并）
    instructions_text = """填写说明：

【必填字段】
• 任务名称：巡检任务的名称，相同任务名称的行会归属到同一个任务下
• Cron表达式：六段执行周期，秒和分固定为 0，如 '0 0 9 * * *' 表示每天9点执行
• 类别名称：巡检类别名称，相同任务下的相同类别会归属到一起
• 类别类型：action 表示执行动作，playbook 表示执行剧本；为空默认 action
• 应用名称：action 类型填写工具应用名称，playbook 类型填写剧本名称或剧本ID
• 巡检内容：具体要检查的内容描述
• 动作/剧本名称：action 类型填写动作名称，playbook 类型可填写剧本显示名称
【选填字段】
• 负责人：负责该巡检任务的人员
• 任务描述：任务的详细说明
• 资源ID列表：多个资源ID用英文逗号分隔，playbook 类型无需填写
• 巡检标准：判断巡检是否通过的标准
• 版本：工具版本或剧本版本/剧本版本ID，空则执行最新版本
【填写技巧】
1. 同一个任务的多个巡检项，任务信息（前4列）可以合并或重复填写
2. 同一个类别的多个巡检项，类别信息（第5-8列）可以合并或重复填写
3. 每一行代表一个巡检项，可以复制行来快速添加相似的巡检项
4. 导入时会自动去重，相同名称的任务/类别会合并
5. 所有导入的数据默认为禁用状态，需要在系统中手动启用

下面为示例，导入时请切换为正确内容！"""

    # 添加22行空行（第2-23行）
    for i in range(23):
        ws.append(["", "", "", "", "", "", "", "", "", "", "", ""])

    # 合并第2-23行的所有列
    ws.merge_cells("A2:L24")
    ws["A2"] = instructions_text
    ws["A2"].alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws["A2"].border = thin_border

    ws.merge_cells(f"A{ws.max_row}:L{ws.max_row}")

    current_row = ws.max_row + 1

    for schedule_id in sorted(data_structure.keys()):
        schedule = schedule_map[schedule_id]
        schedule_start_row = current_row

        for category_id in sorted(data_structure[schedule_id].keys()):
            category = category_map[category_id]
            category_start_row = current_row
            targets_list = data_structure[schedule_id][category_id]

            category_type = _get_category_type(category)
            resource_ids_str = ",".join(category.resource_ids) if category.resource_ids else ""

            for target in targets_list:
                ws.append([schedule.name, schedule.schedule_cron, schedule.owner or "", schedule.description or "", category.name, category_type, category.app_name, resource_ids_str, target.content, target.standard or "", target.action_name, target.version or ""])

                # 应用边框和对齐
                for col in range(1, 13):
                    cell = ws.cell(current_row, col)
                    cell.border = thin_border
                    if col <= 8:
                        cell.alignment = center_alignment
                    else:
                        cell.alignment = left_alignment

                current_row += 1

            # 合并类别单元格（列E-H）
            if len(targets_list) > 1:
                category_end_row = current_row - 1
                ws.merge_cells(f"E{category_start_row}:E{category_end_row}")
                ws.merge_cells(f"F{category_start_row}:F{category_end_row}")
                ws.merge_cells(f"G{category_start_row}:G{category_end_row}")
                ws.merge_cells(f"H{category_start_row}:H{category_end_row}")

        # 合并任务单元格（列A-D）
        schedule_end_row = current_row - 1
        if schedule_end_row > schedule_start_row:
            ws.merge_cells(f"A{schedule_start_row}:A{schedule_end_row}")
            ws.merge_cells(f"B{schedule_start_row}:B{schedule_end_row}")
            ws.merge_cells(f"C{schedule_start_row}:C{schedule_end_row}")
            ws.merge_cells(f"D{schedule_start_row}:D{schedule_end_row}")

    # 设置列宽
    ws.column_dimensions["A"].width = 20  # 任务名称
    ws.column_dimensions["B"].width = 15  # Cron表达式
    ws.column_dimensions["C"].width = 12  # 负责人
    ws.column_dimensions["D"].width = 25  # 任务描述
    ws.column_dimensions["E"].width = 20  # 类别名称
    ws.column_dimensions["F"].width = 12  # 类别类型
    ws.column_dimensions["G"].width = 15  # 应用名称/剧本
    ws.column_dimensions["H"].width = 25  # 资源ID列表
    ws.column_dimensions["I"].width = 30  # 巡检内容
    ws.column_dimensions["J"].width = 30  # 巡检标准
    ws.column_dimensions["K"].width = 18  # 动作/剧本名称
    ws.column_dimensions["L"].width = 18  # 版本

    # 设置行高
    ws.row_dimensions[1].height = 30  # 表头行高

    # 保存到字节流
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output.getvalue()


async def import_targets_from_excel(file_content: bytes, tenant_id: str) -> Dict[str, Any]:
    """
    从Excel文件导入巡检项及其关联的类别和任务（单表格式，支持说明行）
    Import inspection targets with their categories and schedules from Excel (single sheet, skip instruction rows)

    Args:
        file_content: Excel文件的字节数据
        tenant_id: 租户ID

    Returns:
        导入结果统计
    """
    from openpyxl import load_workbook
    from io import BytesIO
    from datetime import datetime

    # 生成时间后缀
    date_suffix = datetime.now().strftime("%Y%m%d%H%M%S")

    # 加载Excel文件
    try:
        wb = load_workbook(BytesIO(file_content))
        ws = wb.active  # 使用第一个工作表
    except Exception as e:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 缓存映射表
    schedule_cache = {}  # name -> schedule object
    category_cache = {}  # (schedule_name, category_name, category_type) -> category object

    created_schedules = []
    created_categories = []
    created_targets = []

    header_values = [str(cell.value).strip() if cell.value else "" for cell in ws[1]]
    header_index = {name: idx for idx, name in enumerate(header_values) if name}
    has_new_format = "类别类型" in header_index or "版本" in header_index

    def _row_text(row, idx: int) -> str:
        if idx < 0 or idx >= len(row) or row[idx] is None:
            return ""
        return str(row[idx]).strip()

    def _value_by_header(row, header: str, fallback_idx: int) -> str:
        return _row_text(row, header_index.get(header, fallback_idx))

    def _first_value_by_header(row, headers: List[str], fallback_idx: int) -> str:
        for header in headers:
            value = _value_by_header(row, header, -1)
            if value:
                return value
        return _row_text(row, fallback_idx)

    # 跳过表头和说明行，从第2行开始读取
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        # 检查是否为空行
        if not any(row):
            continue

        # 跳过说明行（第一列包含"填写说明"、"必填字段"、"选填字段"、"填写技巧"、"导入规则"等关键词）
        first_cell = str(row[0]).strip() if row[0] else ""
        if any(keyword in first_cell for keyword in ["填写说明", "【", "•", "1.", "2.", "3.", "4.", "5."]):
            continue

        # 解析列数据
        try:
            schedule_name = _value_by_header(row, "任务名称", 0)
            schedule_cron = _value_by_header(row, "Cron表达式", 1)
            owner = _value_by_header(row, "负责人", 2)
            schedule_desc = _value_by_header(row, "任务描述", 3)
            category_name = _value_by_header(row, "类别名称", 4)
            if has_new_format:
                category_type = normalize_category_type(_value_by_header(row, "类别类型", 5))
                app_name = _value_by_header(row, "应用名称", 6)
                resource_ids_str = _value_by_header(row, "资源ID列表", 7)
                target_content = _value_by_header(row, "巡检内容", 8)
                target_standard = _value_by_header(row, "巡检标准", 9)
                action_name = _first_value_by_header(row, ["动作/剧本名称", "动作名称"], 10)
                version = _value_by_header(row, "版本", 11)
            else:
                category_type = INSPECTION_CATEGORY_TYPE_ACTION
                app_name = _row_text(row, 5)
                resource_ids_str = _row_text(row, 6)
                target_content = _row_text(row, 7)
                target_standard = _row_text(row, 8)
                action_name = _row_text(row, 9)
                version = ""
        except IndexError:
            # 行数据不完整，跳过
            continue

        # 验证必填字段
        if (
            not schedule_name
            or not schedule_cron
            or not is_valid_schedule_cron(schedule_cron)
            or not category_name
            or not app_name
            or not target_content
        ):
            continue
        if category_type == INSPECTION_CATEGORY_TYPE_ACTION and not action_name:
            continue
        if category_type == INSPECTION_CATEGORY_TYPE_PLAYBOOK and not action_name:
            action_name = app_name

        # ===== 1. 处理巡检任务 =====
        if schedule_name not in schedule_cache:
            # 先从数据库查找
            existing_schedule = await InspectionSchedule.filter(name=schedule_name, deleted=False, tenant_id=tenant_id).first()

            if existing_schedule:
                schedule_cache[schedule_name] = existing_schedule
            else:
                # 创建新任务
                new_schedule = await InspectionSchedule.create(name=schedule_name, schedule_cron=schedule_cron, owner=owner, description=schedule_desc, enabled=False, tenant_id=tenant_id)
                schedule_cache[schedule_name] = new_schedule
                created_schedules.append(new_schedule)

        schedule = schedule_cache[schedule_name]

        # ===== 2. 处理巡检类别 =====
        category_key = (schedule_name, category_name, category_type)
        if category_key not in category_cache:
            # 先从数据库查找
            existing_category = await InspectionCategory.filter(name=category_name, schedule_id=schedule.id, category_type=category_type, deleted=False, tenant_id=tenant_id).first()

            if existing_category:
                category_cache[category_key] = existing_category
            else:
                # 解析资源ID列表
                resource_ids = _normalize_resource_ids(resource_ids_str, category_type)

                # 创建新类别
                new_category = await create_category(
                    {
                        "name": category_name,
                        "schedule_id": schedule.id,
                        "app_name": app_name,
                        "category_type": category_type,
                        "resource_ids": resource_ids,
                        "enabled": False,
                    },
                    tenant_id,
                )
                category_cache[category_key] = new_category
                created_categories.append(new_category)

        category = category_cache[category_key]

        # ===== 3. 处理巡检项 =====
        # 检查是否存在相同内容的巡检项
        existing_target = await InspectionTarget.filter(content=target_content, category_id=category.id, deleted=False, tenant_id=tenant_id).first()

        if existing_target:
            # 跳过已存在的巡检项
            continue

        # 创建新巡检项
        new_target = await create_target(
            {
                "content": target_content,
                "category_id": category.id,
                "standard": target_standard,
                "action_name": action_name,
                "version": version,
                "enabled": False,
            },
            tenant_id,
        )
        created_targets.append(new_target)

    return {
        "schedules_created": len(created_schedules),
        "categories_created": len(created_categories),
        "targets_created": len(created_targets),
        "schedule_ids": [s.id for s in created_schedules],
        "category_ids": [c.id for c in created_categories],
        "target_ids": [t.id for t in created_targets],
    }


async def generate_and_upload_inspection_excel(schedule_id: str, start_time: Union[str, datetime], tenant_id: str, schedule: Optional[InspectionSchedule] = None) -> Optional[str]:
    """
    生成并上传巡检结果 Excel
    Generate and upload inspection results Excel file

    Args:
        schedule_id: 巡检任务ID
        start_time: 开始时间
        tenant_id: 租户ID
        schedule: 巡检任务对象（可选）

    Returns:
        上传成功的文件ID，失败返回None
    """
    try:
        # 解析 start_time
        if isinstance(start_time, str):
            start_time_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        else:
            start_time_dt = start_time

        end_time_dt = datetime.now()

        # 定义导出列
        columns = ["巡检内容", "巡检标准", "巡检状态", "耗时", "错误信息", "开始时间", "结束时间", "AI裁决结果", "执行结果"]

        excel_data = await export_results_to_excel(schedule_id=schedule_id, start_time=start_time_dt, end_time=end_time_dt, columns=columns, tenant_id=tenant_id)

        # 生成文件名并上传
        excel_file_id = str(uuid.uuid4()) + ".xlsx"
        excel_url = await upload_to_s3(excel_data, "user", excel_file_id, "inspection_reports")

        logger.info(f"巡检结果 Excel 已上传: {excel_url}")
        return excel_url
    except Exception as e:
        logger.error(f"生成或上传巡检结果 Excel 失败: {e}")
        return None


async def generate_and_upload_inspection_word(schedule_id: str, created_results: List[Dict[str, Any]], tenant_id: str, schedule: Optional[InspectionSchedule] = None) -> Optional[str]:
    """
    生成并上传巡检报告 Word 文档（使用模板）
    Generate and upload inspection report Word document (using template)

    Args:
        schedule_id: 巡检任务ID
        created_results: 创建的巡检结果列表
        tenant_id: 租户ID
        schedule: 巡检任务对象（可选）

    Returns:
        上传成功的文件ID，失败返回None
    """
    try:
        # 获取完整的巡检结果数据
        result_ids = [r["id"] for r in created_results]
        full_results = await InspectionResult.filter(id__in=result_ids, tenant_id=tenant_id, deleted=False).all()

        # 获取所有目标信息
        target_ids = [r.target_id for r in full_results]
        targets = await InspectionTarget.filter(id__in=target_ids, tenant_id=tenant_id, deleted=False).all()
        target_map = {t.id: t for t in targets}

        # 获取类别信息
        category_ids = list(set(t.category_id for t in targets))
        categories = await InspectionCategory.filter(id__in=category_ids, tenant_id=tenant_id, deleted=False).all()

        # 统计数据
        success_count = sum(1 for r in full_results if r.status == "success")
        failed_count = len(full_results) - success_count
        success_rate = (success_count / len(full_results) * 100) if full_results else 0

        # 收集AI建议
        all_suggestions = []
        for result in full_results:
            if result.result_data and isinstance(result.result_data, dict):
                ai_judgment = result.result_data.get("ai_judgment", {})
                if isinstance(ai_judgment, dict):
                    suggestions = ai_judgment.get("suggestions", [])
                    if suggestions:
                        all_suggestions.extend(suggestions)

        # 去重AI建议
        unique_suggestions = list(set(all_suggestions))

        # 准备模板数据
        context = {
            "schedule_name": schedule.name if schedule else schedule_id,
            "check_date": datetime.now().strftime("%Y年%m月%d日"),
            "execution_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": str(len(full_results)),
            "success_count": str(success_count),
            "failed_count": str(failed_count),
            "success_rate": f"{success_rate:.1f}",
            "health_status": "良好" if success_rate >= 90 else "警告" if success_rate >= 70 else "异常",
            "categories": [{"name": c.name, "app_name": c.app_name} for c in categories],
            "targets": [
                {
                    "content": result_target_snapshot(
                        result,
                        target_map.get(result.target_id),
                        "content",
                        "未知",
                    )
                }
                for result in full_results
            ],
            "results": [],
            "summary_text": "",
            "ai_suggestions": unique_suggestions,
        }

        # 准备巡检结果数据
        for idx, result in enumerate(full_results, 1):
            target = target_map.get(result.target_id)
            status_icon = "✓" if result.status == "success" else "✗"
            status_text = "通过" if result.status == "success" else "未通过"

            result_data = {"index": str(idx), "content": result_target_snapshot(result, target, "content", "未知"), "status": result.status, "status_icon": status_icon, "status_text": status_text, "reason": "", "diagnosis": "", "error_message": result.error_message or ""}

            # 提取AI判断信息
            if result.result_data and isinstance(result.result_data, dict):
                ai_judgment = result.result_data.get("ai_judgment", {})
                if isinstance(ai_judgment, dict):
                    result_data["reason"] = ai_judgment.get("reason", "")
                    result_data["diagnosis"] = ai_judgment.get("diagnosis", "")

            context["results"].append(result_data)

        # 准备总结文本
        if success_rate >= 90:
            context["summary_text"] = "系统整体运行状态良好，各项指标均符合预期标准，未发现重大风险。建议继续保持当前运维策略，定期进行巡检监控。"
        elif success_rate >= 70:
            context["summary_text"] = "系统运行基本正常，但存在部分风险项需要关注。建议优先处理失败的巡检项，加强对相关指标的监控频率，并制定应急预案以应对潜在故障。"
        else:
            context["summary_text"] = "系统存在较多异常情况，运行状态不佳。建议立即组织技术团队进行问题排查，紧急处理所有失败的巡检项，必要时启动应急响应流程。"

        # 加载模板
        template_path = os.path.join(os.path.dirname(__file__), "inspection_report_template.docx")

        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板文件不存在: {template_path}。请先运行 generate_template.py 生成模板文件。")

        doc = DocxTemplate(template_path)

        # 渲染模板
        doc.render(context)

        # 保存文档
        doc_buffer = io.BytesIO()
        doc.save(doc_buffer)
        doc_data = doc_buffer.getvalue()

        # 上传
        doc_file_id = str(uuid.uuid4()) + ".docx"
        doc_url = await upload_to_s3(doc_data, "user", doc_file_id, "inspection_reports")

        logger.info(f"巡检报告 Word 文档已上传: {doc_url}")
        return doc_url
    except Exception as e:
        logger.error(f"生成或上传巡检报告失败: {e}")
        return None


async def export_results_to_excel(schedule_id: str, start_time: Optional[datetime], end_time: Optional[datetime], columns: List[str], tenant_id: str) -> bytes:
    """
    导出巡检结果为Excel文件
    Export inspection results to Excel file

    Args:
        schedule_id: 巡检任务ID
        start_time: 开始时间
        end_time: 结束时间
        columns: 需要导出的列名列表
        tenant_id: 租户ID

    Returns:
        Excel文件的字节数据
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO

    # 定义列名映射
    column_mapping = {
        "巡检内容": "target_content",
        "巡检标准": "target_standard",
        "巡检状态": "status",
        "执行结果状态": "status",
        "执行状态": "execution_status",
        "耗时": "duration",
        "错误信息": "error_message",
        "开始时间": "start_time",
        "结束时间": "end_time",
        "AI裁决结果": "ai_judgment",
        "执行结果": "result_data",
    }

    # 验证列名
    invalid_columns = [col for col in columns if col not in column_mapping]
    if invalid_columns:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 构建查询
    query = InspectionResult.filter(deleted=False, tenant_id=tenant_id, schedule_id=schedule_id)

    if start_time:
        query = query.filter(start_time__gte=start_time)
    if end_time:
        query = query.filter(end_time__lte=end_time)

    results = await query.order_by("-start_time").all()

    if not results:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 获取所有关联的巡检项
    target_ids = list(set(r.target_id for r in results))
    targets = await InspectionTarget.filter(id__in=target_ids, deleted=False, tenant_id=tenant_id).all()
    target_map = {t.id: t for t in targets}

    # 创建Excel工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "巡检结果"

    # 定义样式
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    center_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # 边框样式
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))

    # 设置表头
    ws.append(columns)

    # 应用表头样式
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # 填充数据
    for result in results:
        target = target_map.get(result.target_id)
        row_data = []

        for col_name in columns:
            field_name = column_mapping[col_name]

            if field_name == "target_content":
                value = result_target_snapshot(result, target, "content", "")
            elif field_name == "target_standard":
                value = result_target_snapshot(result, target, "standard", "")
            elif field_name == "status":
                status_map = {"success": "通过", "failed": "未通过", "running": "运行中"}
                value = status_map.get(result.status, result.status)
            elif field_name == "execution_status":
                execution_status_map = {"success": "成功", "failed": "失败", "timeout": "超时", "running": "运行中"}
                value = execution_status_map.get(result.execution_status, result.execution_status)
            elif field_name == "duration":
                value = f"{result.duration}秒" if result.duration is not None else "0秒"
            elif field_name == "error_message":
                value = result.error_message or ""
            elif field_name == "start_time":
                value = result.start_time.strftime("%Y-%m-%d %H:%M:%S") if result.start_time else ""
            elif field_name == "end_time":
                value = result.end_time.strftime("%Y-%m-%d %H:%M:%S") if result.end_time else ""
            elif field_name == "ai_judgment":
                # 从 result_data 中提取 AI 裁决结果
                if result.result_data and isinstance(result.result_data, dict):
                    ai_judgment = result.result_data.get("ai_judgment", {})
                    if isinstance(ai_judgment, dict):
                        passed = ai_judgment.get("passed", False)
                        reason = ai_judgment.get("reason", "")
                        value = f"{'通过' if passed else '不通过'}: {reason}" if reason else ("通过" if passed else "不通过")
                    else:
                        value = ""
                else:
                    value = ""
            elif field_name == "result_data":
                # 将执行结果转换为字符串
                if result.result_data:
                    try:
                        import json

                        value = json.dumps(result.result_data, ensure_ascii=False, indent=2)
                    except:
                        value = str(result.result_data)
                else:
                    value = ""
            else:
                value = ""

            row_data.append(value)

        ws.append(row_data)

    # 应用数据行样式
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.alignment = left_alignment
            cell.border = thin_border

    # 自动调整列宽
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column_letter].width = adjusted_width

    # 保存到字节流
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output.getvalue()
