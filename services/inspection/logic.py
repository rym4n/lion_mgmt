#!/usr/bin/env python
# coding=utf-8

"""
巡检模块业务逻辑层
Inspection Module Business Logic Layer
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import orjson
import os
from tortoise.expressions import Q
from tortoise.functions import Count, Avg
from litellm import acompletion
from models import (
    InspectionSchedule,
    InspectionCategory,
    InspectionTarget,
    InspectionResult,
    Tool,
    ToolAction,
    ToolResources
)
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import RY_AI_MODEL, RY_AI_API_BASE, RY_OPENAI_API_KEY

# ==================== InspectionSchedule Operations ====================

async def save_schedule(data: Dict[str, Any], tenant_id: str) -> InspectionSchedule:
    """
    创建或更新巡检调度配置（每个租户只有一个配置）
    Create or update inspection schedule configuration (one per tenant)
    """
    # 查找租户的现有配置
    schedule = await InspectionSchedule.filter(
        deleted=False,
        tenant_id=tenant_id
    ).first()

    if schedule:
        # 更新现有配置
        for key, value in data.items():
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
    获取租户的巡检调度配置
    Get tenant's inspection schedule configuration
    """
    return await InspectionSchedule.filter(
        deleted=False,
        tenant_id=tenant_id
    ).first()


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
    删除巡检类别（软删除）
    Delete inspection category (soft delete)
    """
    category = await get_category(category_id, tenant_id)
    if not category:
        return False

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
    category_id: Optional[str] = None,
    enabled: Optional[bool] = None
) -> Dict[str, Any]:
    """
    获取巡检目标列表（分页）
    Get inspection target list (paginated)
    """
    query = InspectionTarget.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if category_id:
        query = query.filter(category_id=category_id)
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
                "id": target.id,
                "name": target.name,
                "content": target.content,
                "category_id": target.category_id,
                "standard": target.standard,
                "action_name": target.action_name,
                "enabled": target.enabled,
                "description": target.description,
                "ctime": target.ctime.isoformat() if target.ctime else None,
                "utime": target.utime.isoformat() if target.utime else None,
            }
            for target in items
        ]
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


async def get_all_configs(tenant_id: str, schedule_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取全部巡检目标配置
    Get all inspection target configurations
    """
    # 获取相关的巡检类别信息
    categories = await InspectionCategory.filter(
        deleted=False,
        tenant_id=tenant_id,
        enabled=True,
        schedule_id=schedule_id
    ).all()
    
    if not categories:
        return []
        
    category_ids = [c.id for c in categories]
    category_map = {c.id: c for c in categories}

    # 获取巡检目标
    targets = await InspectionTarget.filter(
        deleted=False,
        tenant_id=tenant_id,
        enabled=True,
        category_id__in=category_ids
    ).all()

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
                    for key, value in orjson.loads(res.resources).items():
                        res_data[key] = value.get("value")
                    resources_dict[res.name] = res_data
        
        resources = orjson.dumps(resources_dict).decode('utf-8')

        item = {
            "main": tool.main if tool and tool.main else "main.py",
            "timeout": "300",
            "version": tool.version if tool else "",
            "resources": resources,
            "tool_name": category.app_name,
            "action_name": target.action_name,
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
    status: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    获取巡检结果列表（分页）
    Get inspection result list (paginated)
    """
    query = InspectionResult.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if target_id:
        query = query.filter(target_id=target_id)
    if status:
        query = query.filter(status=status)
    if start_time:
        query = query.filter(start_time__gte=start_time)
    if end_time:
        query = query.filter(end_time__lte=end_time)

    # 计算总数
    total = await query.count()

    # 分页查询，按开始时间倒序
    items = await query.order_by("-start_time").offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "items": [
            {
                "id": result.id,
                "target_id": result.target_id,
                "start_time": result.start_time.isoformat() if result.start_time else None,
                "end_time": result.end_time.isoformat() if result.end_time else None,
                "status": result.status,
                "result_data": result.result_data,
                "error_message": result.error_message,
                "duration": result.duration,
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
    target_id: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    获取巡检统计信息
    Get inspection statistics
    """
    query = InspectionResult.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if target_id:
        query = query.filter(target_id=target_id)
    if start_time:
        query = query.filter(start_time__gte=start_time)
    if end_time:
        query = query.filter(end_time__lte=end_time)

    # 总执行次数
    total_executions = await query.count()

    # 各状态统计
    success_count = await query.filter(status="success").count()
    failed_count = await query.filter(status="failed").count()
    timeout_count = await query.filter(status="timeout").count()

    # 成功率
    success_rate = success_count / total_executions if total_executions > 0 else 0

    # 平均执行时长
    results = await query.filter(duration__isnull=False).all()
    avg_duration = sum(r.duration for r in results) / len(results) if results else 0

    return {
        "total_executions": total_executions,
        "success_count": success_count,
        "failed_count": failed_count,
        "timeout_count": timeout_count,
        "success_rate": round(success_rate, 2),
        "avg_duration": round(avg_duration, 2)
    }


# ==================== AI Parsing Operations ====================

INSPECTION_PARSE_PROMPT = """
你是一个专业的IT运维巡检专家。请分析用户提供的巡检描述，推荐以下信息：

1. **巡检标准** (standard): 明确的成功标准或阈值
2. **动作名称** (action_name): 从以下可用动作中选择最合适的一个

可用动作及其输入定义：
{available_actions}

用户描述：
{user_input}

请以JSON格式返回，包含以上字段。如果某些信息无法确定，使用null。
同时返回confidence字段(0-1)表示推荐置信度，以及suggestions数组提供配置建议。

返回格式示例：
{{
  "standard": "连接成功且响应时间<1秒",
  "action_name": "check_connection",
  "confidence": 0.95,
  "suggestions": ["建议在资源配置中补充密码字段"]
}}
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
    available_actions_str = orjson.dumps(available_actions, ensure_ascii=False, indent=2)

    prompt = INSPECTION_PARSE_PROMPT.format(
        user_input=text,
        available_actions=available_actions_str
    )

    # 使用litellm调用AI服务
    model = RY_AI_MODEL
    api_base = RY_AI_API_BASE
    api_key = RY_OPENAI_API_KEY

    # 构建acompletion参数
    completion_params = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个专业的IT运维巡检专家。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }

    # 如果配置了API base URL，则添加到参数中
    if api_base:
        completion_params["api_base"] = api_base

    # 如果配置了API key，则添加到参数中
    if api_key:
        completion_params["api_key"] = api_key

    try:
        response = await acompletion(**completion_params)

        # 解析AI返回的JSON
        ai_response = response.choices[0].message.content
        parsed_data = orjson.loads(ai_response)

        # 验证action_name是否在可用列表中
        action_names = [a["name"] for a in available_actions]
        if parsed_data.get("action_name") not in action_names:
            if "suggestions" not in parsed_data:
                parsed_data["suggestions"] = []
            parsed_data["suggestions"].append(
                f"AI选择的动作不在可用列表中，已使用默认动作"
            )
            parsed_data["action_name"] = available_actions[0]["name"] if available_actions else None
            parsed_data["confidence"] = max(0, parsed_data.get("confidence", 0) - 0.2)

    except orjson.JSONDecodeError:
        # AI返回格式错误，返回默认结构
        parsed_data = {
            "standard": None,
            "action_name": available_actions[0] if available_actions else None,
            "confidence": 0.0,
            "suggestions": ["AI解析失败，请手动配置"]
        }
    except Exception as e:
        # 其他错误
        parsed_data = {
            "standard": None,
            "action_name": available_actions[0]["name"] if available_actions else None,
            "confidence": 0.0,
            "suggestions": [f"AI服务调用失败: {str(e)}"]
        }

    return parsed_data


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

    # 准备 prompt
    prompt = f"""
你是一个专业的IT运维巡检专家。请根据以下信息判断巡检结果是否符合标准：

巡检标准：{target.standard}

巡检结果：{orjson.dumps(result_data, ensure_ascii=False, indent=2)}

请判断巡检结果是否符合标准，并返回JSON格式：
{{
  "passed": true/false,
  "reason": "判断理由，说明为什么通过或不通过",
  "suggestions": ["改进建议1", "改进建议2"]
}}

注意：
- passed 为 true 表示符合标准，false 表示不符合标准
- reason 要简洁明了，说明判断依据
- suggestions 只在不通过时提供改进建议，通过时可以为空数组
"""

    # 使用litellm调用AI服务
    model = RY_AI_MODEL
    api_base = RY_AI_API_BASE
    api_key = RY_OPENAI_API_KEY

    # 构建acompletion参数
    completion_params = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个专业的IT运维巡检专家。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }

    if api_base:
        completion_params["api_base"] = api_base
    if api_key:
        completion_params["api_key"] = api_key

    try:
        response = await acompletion(**completion_params)
        ai_response = response.choices[0].message.content
        parsed_data = orjson.loads(ai_response)

        # 确保返回的数据包含必需字段
        if "passed" not in parsed_data:
            parsed_data["passed"] = False
        if "reason" not in parsed_data:
            parsed_data["reason"] = "AI判断失败"
        if "suggestions" not in parsed_data:
            parsed_data["suggestions"] = []

    except orjson.JSONDecodeError:
        parsed_data = {
            "passed": False,
            "reason": "AI返回格式错误",
            "suggestions": ["请检查AI服务配置"]
        }
    except Exception as e:
        parsed_data = {
            "passed": False,
            "reason": f"AI服务调用失败: {str(e)}",
            "suggestions": ["请检查AI服务配置"]
        }

    return parsed_data
