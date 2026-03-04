#!/usr/bin/env python
# coding=utf-8

"""
巡检模块业务逻辑层
Inspection Module Business Logic Layer
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import os
from tortoise.expressions import Q
from tortoise.functions import Count, Avg
from litellm import acompletion
from models import (
    InspectionSchedule,
    InspectionTarget,
    InspectionResult,
    Tool,
    ToolAction
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


async def list_targets(
    page: int,
    page_size: int,
    tenant_id: str,
    category: Optional[str] = None,
    enabled: Optional[bool] = None
) -> Dict[str, Any]:
    """
    获取巡检目标列表（分页）
    Get inspection target list (paginated)
    """
    query = InspectionTarget.filter(deleted=False, tenant_id=tenant_id)

    # 可选过滤条件
    if category:
        query = query.filter(category=category)
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
                "category": target.category,
                "standard": target.standard,
                "action_name": target.action_name,
                "app_name": target.app_name,
                "resource_config": target.resource_config,
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


# ==================== InspectionResult Operations ====================

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
2. **动作名称** (action_name): 从以下可用动作中选择最合适的一个：{available_actions}

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

    # 提取动作名称列表
    available_actions = []
    for action in tool_actions:
        if action.action:
            action_name = action.action.get("name") or action.action.get("func")
            if action_name:
                available_actions.append(action_name)

    if not available_actions:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    # 准备prompt参数
    available_actions_str = ", ".join(available_actions)

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
        parsed_data = json.loads(ai_response)

        # 验证action_name是否在可用列表中
        if parsed_data.get("action_name") not in available_actions:
            if "suggestions" not in parsed_data:
                parsed_data["suggestions"] = []
            parsed_data["suggestions"].append(
                f"AI选择的动作不在可用列表中，已使用默认动作"
            )
            parsed_data["action_name"] = available_actions[0] if available_actions else None
            parsed_data["confidence"] = max(0, parsed_data.get("confidence", 0) - 0.2)

    except json.JSONDecodeError:
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
            "action_name": available_actions[0] if available_actions else None,
            "confidence": 0.0,
            "suggestions": [f"AI服务调用失败: {str(e)}"]
        }

    return parsed_data
