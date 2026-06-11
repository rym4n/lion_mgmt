#!/usr/bin/env python
# coding=utf-8

"""
巡检模块业务逻辑层
Inspection Module Business Logic Layer
"""

import orjson
import ast
import io
import os
import uuid

from typing import Dict, Any, Optional, List, Union
from datetime import datetime
from pathlib import Path
from docxtpl import DocxTemplate
from sanic.log import logger
from libs.nanobot import NanobotClient
from libs.utils import upload_to_s3
from tortoise.expressions import Q
from litellm import acompletion
from models import InspectionSchedule, InspectionCategory, InspectionTarget, InspectionResult, Tool, ToolAction, Timed
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import RY_AI_MODEL, RY_AI_API_BASE, LLM_KEY, NANOBOT_WORKSPACE_BASE

# ==================== InspectionSchedule Operations ====================


async def save_schedule(data: Dict[str, Any], tenant_id: str) -> InspectionSchedule:
    """
    创建或更新巡检调度配置
    Create or update inspection schedule configuration
    """
    schedule_id = data.get("id")

    if schedule_id:
        # 更新现有配置
        schedule = await InspectionSchedule.filter(id=schedule_id, deleted=False, tenant_id=tenant_id).first()

        if not schedule:
            raise CustomError(ErrorCode.NOT_FOUND)

        # 更新字段（排除id）
        update_data = {k: v for k, v in data.items() if k != "id"}
        for key, value in update_data.items():
            if key == "schedule_cron":
                timed_name = f"inspection_{schedule_id}"
                await Timed.filter(name=timed_name, deleted=False, tenant_id=tenant_id).update(tactics=value)
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

    # 分页查询
    items = await query.offset((page - 1) * page_size).limit(page_size).all()

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
    await schedule.save()
    return True


# ==================== InspectionCategory Operations ====================


async def create_category(data: Dict[str, Any], tenant_id: str) -> InspectionCategory:
    """
    创建巡检类别
    Create inspection category
    """
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
    await category.save()
    return True


# ==================== InspectionTarget Operations ====================


async def create_target(data: Dict[str, Any], tenant_id: str) -> InspectionTarget:
    """
    创建巡检目标
    Create inspection target
    """
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
                    "standard": None,
                    "action_name": None,
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
                        "standard": target.standard,
                        "action_name": target.action_name,
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

    # 获取相关的工具信息
    app_names = list(set(c.app_name for c in categories))
    # 按更新时间倒序查询，确保最新的在前面
    tools = await Tool.filter(name__in=app_names, tenant_id=tenant_id, maintain=False, deleted=False).order_by("-utime").all()

    # 去重，只保留最新的
    tool_map = {}
    for t in tools:
        if t.name not in tool_map:
            tool_map[t.name] = t

    results = []
    for target in targets:
        category = category_map.get(target.category_id)
        if not category:
            continue

        tool = tool_map.get(category.app_name)

        # 构造 resources（资源ID列表），保持执行配置接口只返回资源 id，不展开资源内容。
        resources = orjson.dumps(category.resource_ids or []).decode("utf-8")

        item = {
            "main": tool.main if tool and tool.main else "main.py",
            "timeout": "300",
            "version": tool.version if tool else "",
            "resources": resources,
            "tool_name": category.app_name,
            "action_name": target.action_name,
            "action_input": {"params": target.action_input} or {},
            "target_id": target.id,
            "schedule_id": category.schedule_id,
        }
        results.append(item)

    return results


# ==================== InspectionResult Operations ====================


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
        # 模糊匹配 target_content 和 target_standard
        matching_target_ids = await InspectionTarget.filter(Q(content__icontains=keyword) | Q(standard__icontains=keyword), tenant_id=tenant_id, deleted=False).values_list("id", flat=True)
        query = query.filter(target_id__in=matching_target_ids)

    # 计算总数
    total = await query.count()

    # 分页查询，按开始时间倒序
    items = await query.order_by("-start_time").offset((page - 1) * page_size).limit(page_size).all()

    # 获取所有相关的巡检项 ID 并批量查询巡检项详细信息
    target_ids = list(set(result.target_id for result in items if result.target_id))
    targets = await InspectionTarget.filter(id__in=target_ids, tenant_id=tenant_id, deleted=False).all()
    target_map = {target.id: target for target in targets}

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
                "error_message": result.error_message,
                "duration": result.duration,
                "target_content": target_map.get(result.target_id).content if target_map.get(result.target_id) else None,
                "target_standard": target_map.get(result.target_id).standard if target_map.get(result.target_id) else None,
                "reason": result.result_data.get("ai_judgment").get("reason") if result.result_data and result.result_data.get("ai_judgment") else None,
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


async def parse_inspection_content(app_name: str, text: str, tenant_id: str) -> Dict[str, Any]:
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
    # 查询工具信息
    tool = await Tool.filter(name=app_name, deleted=False, tenant_id=tenant_id).first()

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

    # 准备prompt参数
    available_actions_str = orjson.dumps(available_actions, option=orjson.OPT_INDENT_2).decode("utf-8")

    prompt = INSPECTION_PARSE_PROMPT.format(user_input=text, available_actions=available_actions_str)

    # 使用litellm调用AI服务
    model = RY_AI_MODEL
    api_base = RY_AI_API_BASE
    api_key = LLM_KEY

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
            response = await acompletion(**completion_params)

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
                parsed_data["suggestions"].append(f"AI推荐的动作 '{parsed_data.get('action_name')}' 不在可用列表中，已自动选择默认动作")
                parsed_data["action_name"] = available_actions[0]["name"] if available_actions else None
                parsed_data["confidence"] = max(0, float(parsed_data.get("confidence", 0)) - 0.3)

            # 验证confidence范围
            if isinstance(parsed_data.get("confidence"), (int, float)):
                parsed_data["confidence"] = max(0.0, min(1.0, float(parsed_data["confidence"])))
            else:
                parsed_data["confidence"] = 0.5

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
    return {"standard": None, "action_name": available_actions[0]["name"] if available_actions else None, "confidence": 0.0, "suggestions": [last_error or "AI解析失败，请手动配置"]}


# ==================== AI Judgment Operations ====================


def _extract_json_object(text: Any) -> Dict[str, Any]:
    """
    从 Nanobot 返回内容中提取 JSON 对象。
    Nanobot 不像 litellm response_format 那样强制结构化输出，这里兼容纯 JSON、
    markdown fenced code，以及前后带解释文本的情况。
    """
    if isinstance(text, (bytes, bytearray)):
        text = text.decode()
    if not isinstance(text, str):
        raise ValueError("Nanobot response is not text")

    content = text.strip()
    if not content:
        raise ValueError("Nanobot response is empty")

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
        raise ValueError("Nanobot response JSON is not an object")

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
        parsed_data["reason"] = "Nanobot未提供判断理由"
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


async def judge_inspection_result(target_id: str, result_data: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """
    使用AI判断巡检结果是否符合标准
    Judge inspection result using AI

    Args:
        target_id: 巡检目标ID
        result_data: 执行完成的动作结果
        tenant_id: 租户ID

    Returns:
        判断结果，包含 passed, reason, suggestions
    """
    # 查询 InspectionTarget 获取 standard
    target = await InspectionTarget.filter(id=target_id, deleted=False, tenant_id=tenant_id).first()

    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 格式化结果数据以便AI更好理解
    result_str = orjson.dumps(result_data, option=orjson.OPT_INDENT_2).decode("utf-8")

    # 准备 prompt，由 Nanobot 完成判断并返回结构化 JSON
    prompt = f"""
你是一个专业的IT运维巡检专家。请根据以下信息判断巡检结果是否符合标准。

## 巡检标准

{target.standard}

## 巡检结果

{result_str}

## 任务要求

请仔细分析巡检结果，判断是否符合上述标准，并返回JSON格式的判断结果。

判断时需要考虑：
1. 结果数据中的关键指标是否满足标准要求
2. 是否存在错误或异常信息
3. 数值型指标是否在合理范围内
4. 状态型指标是否符合预期

## 输出要求

返回一个JSON对象，包含以下字段：
- passed (boolean): true表示符合标准，false表示不符合标准
- reason (string): 判断理由，简洁说明为什么通过或不通过，引用具体的数据点
- diagnosis (string): 诊断分析，说明导致巡检失败的根本原因和建议的排查方向。如果通过则为空字符串，如果不通过则提供详细的根因分析和排查建议
- suggestions (array): 改进建议列表，如果通过则为空数组，如果不通过则提供具体的改进建议

注意：
1. 直接返回JSON对象，不要使用markdown代码块
2. diagnosis字段应该深入分析问题的根本原因，而不是简单重复reason的内容
3. diagnosis应该包含具体的排查方向，例如：检查哪些配置、查看哪些日志、验证哪些依赖等
4. 本任务只需要基于给定数据做判断，不要调用任何工具、skill 或外部接口
"""

    tenant_workspace = Path(NANOBOT_WORKSPACE_BASE) / tenant_id / "workspace"
    session_key = f"inspection:judgment:{target_id}:{uuid.uuid4().hex}"
    client = NanobotClient(session_id=session_key, workspace=tenant_workspace)

    full_message = f"""<system_instruction>
【身份】你是 AIOE 平台的巡检结果裁决助手。

【执行规则】
1. 只根据用户提供的巡检标准和巡检结果做判断。
2. 不要调用任何工具、skill 或外部接口。
3. 只能输出一个 JSON 对象，禁止输出 markdown、代码块或额外解释。
</system_instruction>

<inspection_judgment_task>
{prompt}
</inspection_judgment_task>"""

    max_retries = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.info(f"准备调用 Nanobot 判断巡检结果，target_id={target_id}, tenant_id={tenant_id}")
            ai_response = await client.ask(message=full_message, streaming=False, session_key=session_key)

            if not ai_response or not ai_response.strip():
                last_error = f"Nanobot返回空内容，已重试 {attempt + 1}/{max_retries}"
                if attempt < max_retries - 1:
                    continue
                break

            return _normalize_ai_judgment(_extract_json_object(ai_response))

        except (orjson.JSONDecodeError, ValueError) as e:
            last_error = f"JSON解析失败: {str(e)}"
            if attempt < max_retries - 1:
                continue  # 重试
        except Exception as e:
            last_error = f"Nanobot调用失败: {str(e)}"
            if attempt < max_retries - 1:
                continue  # 重试

    # 所有重试都失败，返回保守的失败结果
    return {"passed": False, "reason": last_error or "Nanobot判断失败，无法确定巡检结果", "diagnosis": "Nanobot服务异常，无法进行根因分析。建议检查Nanobot配置和运行状态，或人工复核巡检结果。", "suggestions": ["请检查Nanobot配置", "建议人工复核巡检结果"]}


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
    headers = ["任务名称", "Cron表达式", "负责人", "任务描述", "类别名称", "应用名称", "资源ID列表", "巡检内容", "巡检标准", "动作名称"]
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
• Cron表达式：定时执行规则，如 '0 9 * * *' 表示每天9点执行
• 类别名称：巡检类别名称，相同任务下的相同类别会归属到一起
• 应用名称：执行巡检的工具应用名称
• 巡检内容：具体要检查的内容描述
• 动作名称：执行巡检的具体动作名称
【选填字段】
• 负责人：负责该巡检任务的人员
• 任务描述：任务的详细说明
• 资源ID列表：多个资源ID用英文逗号分隔
• 巡检标准：判断巡检是否通过的标准
【填写技巧】
1. 同一个任务的多个巡检项，任务信息（前4列）可以合并或重复填写
2. 同一个类别的多个巡检项，类别信息（第5-7列）可以合并或重复填写
3. 每一行代表一个巡检项，可以复制行来快速添加相似的巡检项
4. 导入时会自动去重，相同名称的任务/类别会合并
5. 所有导入的数据默认为禁用状态，需要在系统中手动启用

下面为示例，导入时请切换为正确内容！"""

    # 添加22行空行（第2-23行）
    for i in range(23):
        ws.append(["", "", "", "", "", "", "", "", "", ""])

    # 合并第2-23行的所有列
    ws.merge_cells("A2:J24")
    ws["A2"] = instructions_text
    ws["A2"].alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws["A2"].border = thin_border

    ws.merge_cells(f"A{ws.max_row}:J{ws.max_row}")

    current_row = ws.max_row + 1

    for schedule_id in sorted(data_structure.keys()):
        schedule = schedule_map[schedule_id]
        schedule_start_row = current_row

        for category_id in sorted(data_structure[schedule_id].keys()):
            category = category_map[category_id]
            category_start_row = current_row
            targets_list = data_structure[schedule_id][category_id]

            resource_ids_str = ",".join(category.resource_ids) if category.resource_ids else ""

            for target in targets_list:
                ws.append([schedule.name, schedule.schedule_cron, schedule.owner or "", schedule.description or "", category.name, category.app_name, resource_ids_str, target.content, target.standard or "", target.action_name])

                # 应用边框和对齐
                for col in range(1, 11):
                    cell = ws.cell(current_row, col)
                    cell.border = thin_border
                    if col <= 7:
                        cell.alignment = center_alignment
                    else:
                        cell.alignment = left_alignment

                current_row += 1

            # 合并类别单元格（列E-G）
            if len(targets_list) > 1:
                category_end_row = current_row - 1
                ws.merge_cells(f"E{category_start_row}:E{category_end_row}")
                ws.merge_cells(f"F{category_start_row}:F{category_end_row}")
                ws.merge_cells(f"G{category_start_row}:G{category_end_row}")

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
    ws.column_dimensions["F"].width = 15  # 应用名称
    ws.column_dimensions["G"].width = 25  # 资源ID列表
    ws.column_dimensions["H"].width = 30  # 巡检内容
    ws.column_dimensions["I"].width = 30  # 巡检标准
    ws.column_dimensions["J"].width = 15  # 动作名称

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
    category_cache = {}  # (schedule_name, category_name) -> category object

    created_schedules = []
    created_categories = []
    created_targets = []

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
            schedule_name = str(row[0]).strip() if row[0] else ""
            schedule_cron = str(row[1]).strip() if row[1] else ""
            owner = str(row[2]).strip() if row[2] else ""
            schedule_desc = str(row[3]).strip() if row[3] else ""
            category_name = str(row[4]).strip() if row[4] else ""
            app_name = str(row[5]).strip() if row[5] else ""
            resource_ids_str = str(row[6]).strip() if row[6] else ""
            target_content = str(row[7]).strip() if row[7] else ""
            target_standard = str(row[8]).strip() if row[8] else ""
            action_name = str(row[9]).strip() if row[9] else ""
        except IndexError:
            # 行数据不完整，跳过
            continue

        # 验证必填字段
        if not schedule_name or not schedule_cron or not category_name or not app_name or not target_content or not action_name:
            continue

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
        category_key = (schedule_name, category_name)
        if category_key not in category_cache:
            # 先从数据库查找
            existing_category = await InspectionCategory.filter(name=category_name, schedule_id=schedule.id, deleted=False, tenant_id=tenant_id).first()

            if existing_category:
                category_cache[category_key] = existing_category
            else:
                # 解析资源ID列表
                resource_ids = [rid.strip() for rid in resource_ids_str.split(",") if rid.strip()] if resource_ids_str else []

                # 创建新类别
                new_category = await InspectionCategory.create(name=category_name, schedule_id=schedule.id, app_name=app_name, resource_ids=resource_ids, enabled=False, tenant_id=tenant_id)
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
        new_target = await InspectionTarget.create(content=target_content, category_id=category.id, standard=target_standard, action_name=action_name, enabled=False, tenant_id=tenant_id)
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
            "targets": [{"content": t.content} for t in targets],
            "results": [],
            "summary_text": "",
            "ai_suggestions": unique_suggestions,
        }

        # 准备巡检结果数据
        for idx, result in enumerate(full_results, 1):
            target = target_map.get(result.target_id)
            status_icon = "✓" if result.status == "success" else "✗"
            status_text = "通过" if result.status == "success" else "未通过"

            result_data = {"index": str(idx), "content": target.content if target else "未知", "status": result.status, "status_icon": status_icon, "status_text": status_text, "reason": "", "diagnosis": "", "error_message": result.error_message or ""}

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
                value = target.content if target else ""
            elif field_name == "target_standard":
                value = target.standard if target else ""
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
