#!/usr/bin/env python
# coding=utf-8

"""
巡检模块业务逻辑层
Inspection Module Business Logic Layer
"""

from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import orjson
import ast
from tortoise.expressions import Q
from litellm import acompletion
from models import (
    InspectionSchedule,
    InspectionCategory,
    InspectionTarget,
    InspectionResult,
    Tool,
    ToolAction,
    ToolResources,
    Timed
)
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import RY_AI_MODEL, RY_AI_API_BASE, LLM_KEY

# ==================== InspectionSchedule Operations ====================

async def save_schedule(data: Dict[str, Any], tenant_id: str) -> InspectionSchedule:
    """
    创建或更新巡检调度配置
    Create or update inspection schedule configuration
    """
    schedule_id = data.get("id")

    if schedule_id:
        # 更新现有配置
        schedule = await InspectionSchedule.filter(
            id=schedule_id,
            deleted=False,
            tenant_id=tenant_id
        ).first()

        if not schedule:
            raise CustomError(ErrorCode.NOT_FOUND)

        # 更新字段（排除id）
        update_data = {k: v for k, v in data.items() if k != "id"}
        for key, value in update_data.items():
            setattr(schedule, key, value)
        await schedule.save()
    else:
        # 创建新配置
        schedule = await InspectionSchedule.create(
            **data,
            tenant_id=tenant_id
        )

    return schedule


async def get_schedule(tenant_id: str) -> Optional[InspectionSchedule]:
    """
    获取租户的巡检调度配置（返回第一个，用于向后兼容）
    Get tenant's inspection schedule configuration (returns first one, for backward compatibility)
    """
    return await InspectionSchedule.filter(
        deleted=False,
        tenant_id=tenant_id
    ).first()


async def get_schedule_by_id(schedule_id: str, tenant_id: str) -> Optional[InspectionSchedule]:
    """
    根据ID获取巡检调度配置
    Get inspection schedule configuration by ID
    """
    return await InspectionSchedule.filter(
        id=schedule_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()


async def list_schedules(
    page: int,
    page_size: int,
    tenant_id: str,
    enabled: Optional[bool] = None,
    keyword: Optional[str] = None
) -> Dict[str, Any]:
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
        query = query.filter(
            Q(name__icontains=keyword) |
            Q(description__icontains=keyword)
        )

    # 计算总数
    total = await query.count()

    # 分页查询
    items = await query.offset((page - 1) * page_size).limit(page_size).all()

    # 获取所有 schedule_id 对应的巡检结果日期
    schedule_ids = [schedule.id for schedule in items]

    # 查询这些 schedule 的所有结果，只需要 schedule_id 和 start_time
    results = await InspectionResult.filter(
        deleted=False,
        tenant_id=tenant_id,
        schedule_id__in=schedule_ids
    ).only("schedule_id", "start_time").all()

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
            }
            for schedule in items
        ]
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
    categories = await InspectionCategory.filter(
        schedule_id=schedule_id,
        deleted=False,
        tenant_id=tenant_id
    ).all()

    # 收集所有类别ID
    category_ids = [cat.id for cat in categories]

    # 软删除所有关联的巡检项
    if category_ids:
        await InspectionTarget.filter(
            category_id__in=category_ids,
            deleted=False,
            tenant_id=tenant_id
        ).update(deleted=True)

    # 软删除所有关联的类别
    await InspectionCategory.filter(
        schedule_id=schedule_id,
        deleted=False,
        tenant_id=tenant_id
    ).update(deleted=True)

    # 软删除关联的定时任务
    timed_name = f"inspection_{schedule_id}"
    await Timed.filter(
        name=timed_name,
        deleted=False,
        tenant_id=tenant_id
    ).update(deleted=True)

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
    category = await InspectionCategory.create(
        **data,
        tenant_id=tenant_id
    )
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


async def list_categories(
    page: int,
    page_size: int,
    tenant_id: str,
    schedule_id: Optional[str] = None,
    enabled: Optional[bool] = None
) -> Dict[str, Any]:
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
        ]
    }


async def get_category(category_id: str, tenant_id: str) -> Optional[InspectionCategory]:
    """
    获取巡检类别详情
    Get inspection category details
    """
    return await InspectionCategory.filter(
        id=category_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()


async def update_category(
    category_id: str,
    data: Dict[str, Any],
    tenant_id: str
) -> Optional[InspectionCategory]:
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
    await InspectionTarget.filter(
        category_id=category_id,
        deleted=False,
        tenant_id=tenant_id
    ).update(deleted=True)

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
    target = await InspectionTarget.create(
        **data,
        tenant_id=tenant_id
    )
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


async def list_targets(
    page: int,
    page_size: int,
    tenant_id: str,
    schedule_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    keyword: Optional[str] = None
) -> Dict[str, Any]:
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
            all_items.append({
                "id": None,
                "content": None,
                "category_id": cat.id,
                "category_name": cat.name,
                "category_enabled": cat.enabled,
                "app_name": cat.app_name,
                "standard": None,
                "action_name": None,
                "enabled": None,
                "ctime": cat.ctime.isoformat() if hasattr(cat, 'ctime') and cat.ctime else None,
                "utime": cat.utime.isoformat() if hasattr(cat, 'utime') and cat.utime else None,
            })
        else:
            for target in targets_in_cat:
                all_items.append({
                    "id": target.id,
                    "content": target.content,
                    "category_id": target.category_id,
                    "category_name": cat.name,
                    "category_enabled": cat.enabled,
                    "app_name": cat.app_name,
                    "standard": target.standard,
                    "action_name": target.action_name,
                    "enabled": target.enabled,
                    "ctime": target.ctime.isoformat() if target.ctime else None,
                    "utime": target.utime.isoformat() if target.utime else None,
                })

    # 4. 手动分页
    total = len(all_items)
    start = (page - 1) * page_size
    end = start + page_size
    items = all_items[start:end]

    return {
        "total": total,
        "items": items
    }


async def get_target(target_id: str, tenant_id: str) -> Optional[InspectionTarget]:
    """
    获取巡检目标详情
    Get inspection target details
    """
    return await InspectionTarget.filter(
        id=target_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()


async def update_target(
    target_id: str,
    data: Dict[str, Any],
    tenant_id: str
) -> Optional[InspectionTarget]:
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
    categories_query = InspectionCategory.filter(
        deleted=False,
        tenant_id=tenant_id,
        enabled=True
    )

    if schedule_ids:
        categories_query = categories_query.filter(schedule_id__in=schedule_ids)

    categories = await categories_query.all()

    if not categories:
        return []

    category_ids = [c.id for c in categories]
    category_map = {c.id: c for c in categories}
    # 获取巡检目标
    query = InspectionTarget.filter(
        deleted=False,
        tenant_id=tenant_id,
        enabled=True,
        category_id__in=category_ids
    )

    if target_ids:
        query = query.filter(id__in=target_ids)

    targets = await query.all()

    if not targets:
        return []

    # 获取相关的工具信息
    app_names = list(set(c.app_name for c in categories))
    # 按更新时间倒序查询，确保最新的在前面
    tools = await Tool.filter(
        name__in=app_names,
        tenant_id=tenant_id,
        maintain=False,
        deleted=False
    ).order_by("-utime").all()

    # 去重，只保留最新的
    tool_map = {}
    for t in tools:
        if t.name not in tool_map:
            tool_map[t.name] = t

    # 获取相关的工具资源信息
    all_resource_ids = set()
    for c in categories:
        if c.resource_ids:
            all_resource_ids.update(c.resource_ids)

    tool_resources = await ToolResources.filter(
        id__in=list(all_resource_ids),
        deleted=False,
        tenant_id=tenant_id
    ).all()
    resource_map = {r.id: r for r in tool_resources}

    results = []
    for target in targets:
        category = category_map.get(target.category_id)
        # category is guaranteed to be in map because we fetched targets based on categories
        # but keep safety check just in case
        if not category:
            continue

        tool = tool_map.get(category.app_name)

        # 构造 resources（资源配置）
        resources_dict = {}
        if category.resource_ids:
            for r_id in category.resource_ids:
                if r_id in resource_map:
                    res = resource_map[r_id]
                    res_data = {}
                    resources = res.resources_json
                    if isinstance(resources, dict):
                        for key, value in resources.items():
                            res_data[key] = value.get("value") if isinstance(value, dict) else value
                    resources_dict[res.name] = res_data
        
        resources = orjson.dumps(resources_dict).decode('utf-8')

        item = {
            "main": tool.main if tool and tool.main else "main.py",
            "timeout": "300",
            "version": tool.version if tool else "",
            "resources": resources,
            "tool_name": category.app_name,
            "action_name": target.action_name,
            "target_id": target.id,
            "schedule_id": category.schedule_id
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

    target = await InspectionTarget.filter(
        id=target_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()

    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 获取 schedule_id: target -> category -> schedule
    category = await InspectionCategory.filter(
        id=target.category_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()

    if not category:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 添加 schedule_id 到数据中
    data["schedule_id"] = category.schedule_id

    result = await InspectionResult.create(
        **data,
        tenant_id=tenant_id
    )
    return result


async def list_results(
    page: int,
    page_size: int,
    tenant_id: str,
    target_id: Optional[str] = None,
    schedule_id: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    keyword: Optional[str] = None
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
    if start_time:
        query = query.filter(start_time__gte=start_time)
    if end_time:
        query = query.filter(end_time__lte=end_time)
    if keyword:
        # 模糊匹配 target_content 和 target_standard
        matching_target_ids = await InspectionTarget.filter(
            Q(content__icontains=keyword) | Q(standard__icontains=keyword),
            tenant_id=tenant_id,
            deleted=False
        ).values_list("id", flat=True)
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
                "error_message": result.error_message,
                "duration": result.duration,
                "target_content": target_map.get(result.target_id).content if target_map.get(result.target_id) else None,
                "target_standard": target_map.get(result.target_id).standard if target_map.get(result.target_id) else None,
            }
            for result in items
        ]
    }


async def get_result(result_id: str, tenant_id: str) -> Optional[InspectionResult]:
    """
    获取巡检结果详情
    Get inspection result details
    """
    return await InspectionResult.filter(
        id=result_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()


async def get_stats(
    tenant_id: str,
    schedule_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None
) -> Dict[str, Any]:
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

    return {
        "success_rate": round(success_rate, 2),
        "success_count": success_count,
        "failed_count": failed_count,
        "total_count": success_count + failed_count
    }


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
    tool = await Tool.filter(
        name=app_name,
        deleted=False,
        tenant_id=tenant_id
    ).first()

    if not tool:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    # 查询该工具的所有动作
    tool_actions = await ToolAction.filter(
        tool_name=tool.name,
        version_number=tool.version,
        deleted=False,
        tenant_id=tenant_id
    ).all()

    if not tool_actions:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    # 提取动作名称和输入定义
    available_actions = []
    action_map = {}  # 用于后续查找
    for action in tool_actions:
        if action.action:
            action_name = action.action.get("func")
            if action_name:
                available_actions.append({
                    "name": action_name
                })
                action_map[action_name] = action

    if not available_actions:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    # 准备prompt参数
    available_actions_str = orjson.dumps(available_actions, option=orjson.OPT_INDENT_2).decode('utf-8')

    prompt = INSPECTION_PARSE_PROMPT.format(
        user_input=text,
        available_actions=available_actions_str
    )

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
        "messages": [
            {"role": "system", "content": "你是一个专业的IT运维巡检专家，擅长分析巡检需求并提供精确的配置建议。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 1000  # 限制输出长度
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
                parsed_data["suggestions"].append(
                    f"AI推荐的动作 '{parsed_data.get('action_name')}' 不在可用列表中，已自动选择默认动作"
                )
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
    return {
        "standard": None,
        "action_name": available_actions[0]["name"] if available_actions else None,
        "confidence": 0.0,
        "suggestions": [last_error or "AI解析失败，请手动配置"]
    }


# ==================== AI Judgment Operations ====================

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
    target = await InspectionTarget.filter(
        id=target_id,
        deleted=False,
        tenant_id=tenant_id
    ).first()

    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    # 格式化结果数据以便AI更好理解
    result_str = orjson.dumps(result_data, option=orjson.OPT_INDENT_2).decode('utf-8')

    # 准备 prompt - 针对GPT-5.4优化
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
- suggestions (array): 改进建议列表，如果通过则为空数组，如果不通过则提供具体的改进建议

注意：直接返回JSON对象，不要使用markdown代码块。
"""

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
        "messages": [
            {"role": "system", "content": "你是一个专业的IT运维巡检专家，擅长分析巡检结果并做出准确的判断。你的判断应该基于数据和标准，而不是主观臆断。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 800
    }

    # GPT-5系列模型只支持temperature=1，其他模型可以使用自定义temperature
    if "gpt-5" not in model.lower():
        completion_params["temperature"] = 0.1

    if api_base:
        completion_params["api_base"] = api_base
    if api_key:
        completion_params["api_key"] = api_key

    # 重试逻辑
    max_retries = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            response = await acompletion(**completion_params)
            ai_response = response.choices[0].message.content
            parsed_data = orjson.loads(ai_response)

            # 验证和规范化必需字段
            if "passed" not in parsed_data:
                parsed_data["passed"] = False
            else:
                # 确保passed是布尔值
                parsed_data["passed"] = bool(parsed_data["passed"])

            if "reason" not in parsed_data or not parsed_data["reason"]:
                parsed_data["reason"] = "AI未提供判断理由"

            if "suggestions" not in parsed_data:
                parsed_data["suggestions"] = []
            elif not isinstance(parsed_data["suggestions"], list):
                parsed_data["suggestions"] = []

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

    # 所有重试都失败，返回保守的失败结果
    return {
        "passed": False,
        "reason": last_error or "AI判断失败，无法确定巡检结果",
        "suggestions": ["请检查AI服务配置", "建议人工复核巡检结果"]
    }


# ==================== Import/Export Operations ====================

async def export_targets(target_ids: List[str], tenant_id: str) -> Dict[str, Any]:
    """
    导出巡检项及其关联的类别和任务
    Export inspection targets with their categories and schedules

    Args:
        target_ids: 巡检项ID列表
        tenant_id: 租户ID

    Returns:
        包含巡检任务、类别和项的完整数据
    """
    # 查询巡检项
    targets = await InspectionTarget.filter(
        id__in=target_ids,
        deleted=False,
        tenant_id=tenant_id
    ).all()

    if not targets:
        return {"schedules": [], "categories": [], "targets": []}

    # 获取所有关联的类别ID
    category_ids = list(set(t.category_id for t in targets))
    categories = await InspectionCategory.filter(
        id__in=category_ids,
        deleted=False,
        tenant_id=tenant_id
    ).all()

    # 获取所有关联的任务ID
    schedule_ids = list(set(c.schedule_id for c in categories))
    schedules = await InspectionSchedule.filter(
        id__in=schedule_ids,
        deleted=False,
        tenant_id=tenant_id
    ).all()

    # 构建导出数据
    export_data = {
        "schedules": [
            {
                "id": s.id,
                "name": s.name,
                "schedule_cron": s.schedule_cron,
                "owner": s.owner,
                "description": s.description,
            }
            for s in schedules
        ],
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "schedule_id": c.schedule_id,
                "app_name": c.app_name,
                "resource_ids": c.resource_ids,
            }
            for c in categories
        ],
        "targets": [
            {
                "id": t.id,
                "content": t.content,
                "category_id": t.category_id,
                "standard": t.standard,
                "action_name": t.action_name,
            }
            for t in targets
        ]
    }

    return export_data


async def import_targets(import_data: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """
    导入巡检项及其关联的类别和任务
    Import inspection targets with their categories and schedules

    Args:
        import_data: 导入的数据（包含schedules, categories, targets）
        tenant_id: 租户ID

    Returns:
        导入结果统计
    """
    from datetime import datetime

    # 生成时间后缀
    date_suffix = datetime.now().strftime("%Y%m%d%H%M%S")

    # ID映射表（旧ID -> 新ID）
    schedule_id_map = {}
    category_id_map = {}

    schedules_data = import_data.get("schedules", [])
    categories_data = import_data.get("categories", [])
    targets_data = import_data.get("targets", [])

    created_schedules = []
    created_categories = []
    created_targets = []

    # 1. 导入巡检任务
    for schedule_data in schedules_data:
        old_schedule_id = schedule_data.get("id")
        schedule_name = schedule_data.get("name")

        # 检查是否存在同名任务
        existing_schedule = await InspectionSchedule.filter(
            name=schedule_name,
            deleted=False,
            tenant_id=tenant_id
        ).first()

        if existing_schedule:
            # 添加日期后缀
            schedule_name = f"{schedule_name}_{date_suffix}"

        # 创建新任务（默认禁用）
        new_schedule = await InspectionSchedule.create(
            name=schedule_name,
            schedule_cron=schedule_data.get("schedule_cron"),
            owner=schedule_data.get("owner"),
            description=schedule_data.get("description"),
            enabled=False,  # 默认禁用
            tenant_id=tenant_id
        )

        schedule_id_map[old_schedule_id] = new_schedule.id
        created_schedules.append(new_schedule)

    # 2. 导入巡检类别
    for category_data in categories_data:
        old_category_id = category_data.get("id")
        old_schedule_id = category_data.get("schedule_id")
        category_name = category_data.get("name")

        # 映射到新的schedule_id
        new_schedule_id = schedule_id_map.get(old_schedule_id)
        if not new_schedule_id:
            continue

        # 检查是否存在同名类别（在同一任务下）
        existing_category = await InspectionCategory.filter(
            name=category_name,
            schedule_id=new_schedule_id,
            deleted=False,
            tenant_id=tenant_id
        ).first()

        if existing_category:
            # 添加日期后缀
            category_name = f"{category_name}_{date_suffix}"

        # 创建新类别（默认禁用）
        new_category = await InspectionCategory.create(
            name=category_name,
            schedule_id=new_schedule_id,
            app_name=category_data.get("app_name"),
            resource_ids=category_data.get("resource_ids", []),
            enabled=False,  # 默认禁用
            tenant_id=tenant_id
        )

        category_id_map[old_category_id] = new_category.id
        created_categories.append(new_category)

    # 3. 导入巡检项
    for target_data in targets_data:
        old_category_id = target_data.get("category_id")
        target_content = target_data.get("content")

        # 映射到新的category_id
        new_category_id = category_id_map.get(old_category_id)
        if not new_category_id:
            continue

        # 检查是否存在同内容的巡检项（在同一类别下）
        existing_target = await InspectionTarget.filter(
            content=target_content,
            category_id=new_category_id,
            deleted=False,
            tenant_id=tenant_id
        ).first()

        if existing_target:
            # 添加日期后缀
            target_content = f"{target_content}_{date_suffix}"

        # 创建新巡检项（默认禁用）
        new_target = await InspectionTarget.create(
            content=target_content,
            category_id=new_category_id,
            standard=target_data.get("standard"),
            action_name=target_data.get("action_name"),
            enabled=False,  # 默认禁用
            tenant_id=tenant_id
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
