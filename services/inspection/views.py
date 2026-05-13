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

from datetime import datetime
from sanic import Blueprint, Request
from sanic.log import logger
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.response import response_ok
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import Timed, Playbook, PlaybookVersion, InspectionTarget, InspectionCategory, InspectionResult
from settings import TASK_TOPIC
from . import logic
from sanic.response import raw

inspection_bp = Blueprint("inspection", url_prefix="/inspection")
__all__ = ["inspection_bp"]


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

    return response_ok(data={
        "id": schedule.id,
        "name": schedule.name,
        "schedule_cron": schedule.schedule_cron,
        "owner": schedule.owner,
        "enabled": schedule.enabled,
        "description": schedule.description,
        "ctime": schedule.ctime.isoformat() if schedule.ctime else None,
        "utime": schedule.utime.isoformat() if schedule.utime else None,
    })


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

    return response_ok(data={
        "id": schedule.id,
        "name": schedule.name,
        "schedule_cron": schedule.schedule_cron,
        "owner": schedule.owner,
        "enabled": schedule.enabled,
        "description": schedule.description,
        "ctime": schedule.ctime.isoformat() if schedule.ctime else None,
        "utime": schedule.utime.isoformat() if schedule.utime else None,
    })


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

    result = await logic.list_schedules(
        page=page,
        page_size=page_size,
        tenant_id=tenant_id,
        enabled=enabled,
        keyword=keyword
    )

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
                    "required": ["name", "schedule_id", "app_name", "resource_ids"],
                    "properties": {
                        "name": {"type": "string", "description": "类别名称"},
                        "schedule_id": {"type": "string", "description": "关联巡检任务ID"},
                        "app_name": {"type": "string", "description": "应用名称"},
                        "resource_ids": {"type": "array", "items": {"type": "string"}, "description": "资源ID列表"},
                        "enabled": {"type": "boolean", "description": "是否启用"},
                    },
                }
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

    # 验证必需字段
    required_fields = ["name", "schedule_id", "app_name", "resource_ids"]
    processed_list = []

    for item in data_list:
        if not isinstance(item, dict):
            continue

        # 验证必需字段
        for field in required_fields:
            if field not in item:
                raise CustomError(ErrorCode.PARAM_ERROR)

        processed_list.append(item)

    if not processed_list and data_list:
        if not data_list:
            return response_ok(data=[])
        raise CustomError(ErrorCode.PARAM_ERROR)

    categories = await logic.batch_create_categories(processed_list, tenant_id)

    result_data = [
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
        for category in categories
    ]

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

    result = await logic.list_categories(
        page=page,
        page_size=page_size,
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        enabled=enabled
    )

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
                                    "resource_config": {"type": "object"},
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

    return response_ok(data={
        "id": category.id,
        "name": category.name,
        "schedule_id": category.schedule_id,
        "app_name": category.app_name,
        "resource_ids": category.resource_ids,
        "enabled": category.enabled,
        "ctime": category.ctime.isoformat() if category.ctime else None,
        "utime": category.utime.isoformat() if category.utime else None,
    })


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
                    "app_name": {"type": "string", "description": "应用名称"},
                    "resource_ids": {"type": "array", "items": {"type": "string"}, "description": "资源ID列表"},
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
                                    "resource_config": {"type": "object"},
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
    category = await logic.update_category(category_id, data, tenant_id)
    if not category:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(data={
        "id": category.id,
        "name": category.name,
        "schedule_id": category.schedule_id,
        "app_name": category.app_name,
        "resource_ids": category.resource_ids,
        "enabled": category.enabled,
        "ctime": category.ctime.isoformat() if category.ctime else None,
        "utime": category.utime.isoformat() if category.utime else None,
    })


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
                    "required": ["content", "category_id", "standard", "action_name"],
                    "properties": {
                        "content": {"type": "string", "description": "巡检内容"},
                        "category_id": {"type": "string", "description": "关联巡检类别ID"},
                        "standard": {"type": "string", "description": "巡检标准"},
                        "action_name": {"type": "string", "description": "动作名称"},
                        "enabled": {"type": "boolean", "description": "是否启用"},
                    },
                }
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
                                        "action_name": {"type": "string"},
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

    # 验证必需字段并处理JSON字符串
    required_fields = ["content", "category_id", "standard", "action_name"]
    processed_list = []

    for item in data_list:
        if not isinstance(item, dict):
            continue

        # 验证必需字段
        for field in required_fields:
            if field not in item:
                raise CustomError(ErrorCode.PARAM_ERROR)

        # 处理可能为字符串的JSON字段
        processed_item = item.copy()
        processed_list.append(processed_item)

    if not processed_list and data_list:
         if not data_list:
             return response_ok(data=[])
         raise CustomError(ErrorCode.PARAM_ERROR)

    targets = await logic.batch_create_targets(processed_list, tenant_id)

    result_data = [
        {
            "id": target.id,
            "content": target.content,
            "category_id": target.category_id,
            "standard": target.standard,
            "action_name": target.action_name,
            "enabled": target.enabled,
            "ctime": target.ctime.isoformat() if target.ctime else None,
            "utime": target.utime.isoformat() if target.utime else None,
        }
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
                                                "standard": {"type": "string"},
                                                "action_name": {"type": "string"},
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

    result = await logic.list_targets(
        page=page,
        page_size=page_size,
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        enabled=enabled,
        keyword=keyword
    )

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
                                    "standard": {"type": "string"},
                                    "action_name": {"type": "string"},
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

    categories = await InspectionCategory.filter(
        id__in=[target.category_id],
        deleted=False,
        tenant_id=tenant_id
    ).all()
    category_map = {cat.id: cat.app_name for cat in categories}

    return response_ok(data={
        "id": target.id,
        "content": target.content,
        "category_id": target.category_id,
        "app_name": category_map.get(target.category_id),
        "standard": target.standard,
        "action_name": target.action_name,
        "enabled": target.enabled,
        "ctime": target.ctime.isoformat() if target.ctime else None,
        "utime": target.utime.isoformat() if target.utime else None,
    })


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
                    "action_name": {"type": "string", "description": "动作名称"},
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
                                    "action_name": {"type": "string"},
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
    target = await logic.update_target(target_id, data, tenant_id)
    if not target:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(data={
        "id": target.id,
        "content": target.content,
        "category_id": target.category_id,
        "standard": target.standard,
        "action_name": target.action_name,
        "enabled": target.enabled,
        "ctime": target.ctime.isoformat() if target.ctime else None,
        "utime": target.utime.isoformat() if target.utime else None,
    })


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
                                        "action_input": {"type": "string"},
                                    }
                                }
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
    target_ids = data.get("target_id") if "target_id" in data else None
    tenant_id = request.ctx.session.get("tenant_id")
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

    result = await logic.parse_inspection_content(category.app_name, text, tenant_id)
    result["app_name"] = category.app_name
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
            await timed.save()

            # 发送消息到 Kafka
            msg = {
                "run_type": "flow",
                "run_args": timed.playbook_input,
                "timer": "cron",
                "timer_rule": {
                    "end_date": timed.end_time,
                    "crontab": schedule.schedule_cron
                },
                "timer_id": timed.id,
                "tenant_id": tenant_id
            }
            await aproduce(TASK_TOPIC, msg)
    else:
        # 创建新的定时任务
        # 查找名为"自动化巡检"的剧本
        playbook = await Playbook.filter(
            name="自动化巡检",
            deleted=False,
            tenant_id=tenant_id,
            internal=True
        ).first()

        if not playbook:
            return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

        # 获取该剧本的最新发布版本
        playbook_version = await PlaybookVersion.filter(
            playbook_id=playbook.id,
            tenant_id=tenant_id,
            status=1  # 假设 status=1 表示已发布
        ).order_by("-version").first()

        if not playbook_version:
            return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

        playbook_version_id = playbook_version.id

        # 构建剧本输入参数（传入 schedule_id，剧本会查询该 schedule 下所有启用的巡检项）
        playbook_input = {
            "flow_id": playbook_version_id,
            "flow_input": {
                "schedule_id": schedule_id,
                "target_id": None
            },
            "executor": request.ctx.session.get("account_info").id
        }

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
            tenant_id=tenant_id,
            internal=True
        )

        # 发送消息到 Kafka
        msg = {
            "run_type": "flow",
            "run_args": playbook_input,
            "timer": "cron",
            "timer_rule": {
                "end_date": timed.end_time,
                "crontab": schedule.schedule_cron
            },
            "timer_id": timed.id,
            "tenant_id": tenant_id
        }
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
        msg = {
            "run_type": "flow",
            "run_args": timed.playbook_input,
            "timer": "remove",
            "timer_rule": {},
            "timer_id": timed.id,
            "tenant_id": tenant_id
        }
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
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"}
                }
            },
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

    playbook = await Playbook.filter(
        name="自动化巡检",
        deleted=False,
        internal=True
    ).first()

    if not playbook:
        return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

    # 获取该剧本的最新发布版本
    playbook_version = await PlaybookVersion.filter(
        playbook_id=playbook.id,
        status=1  # 假设 status=1 表示已发布
    ).order_by("-version").first()

    if not playbook_version:
        return response_ok("当前不存在自动化巡检剧本，请联系管理员进行处理")

    playbook_version_id = playbook_version.id

    # 构建剧本输入参数（传入 schedule_id，剧本会查询该 schedule 下所有启用的巡检项）
    playbook_input = {
        "flow_id": playbook_version_id,
        "flow_input": {
            "schedule_id": schedule_id,
            "target_id": target_ids
        },
        "executor": request.ctx.session.get("account_info").id
    }

    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    # 发送消息到 Kafka
    msg = {
        "run_type": "flow",
        "run_id": gen_run_id(),
        "run_args": playbook_input,
        "tenant_id": tenant_id
    }
    await aproduce(TASK_TOPIC, msg)
    await resp.send(f"data: {orjson.dumps({'message': '手动执行已启动'}).decode()}\n\n")

    sent_id = []
    for _ in range(60):
        results = await InspectionResult.filter(
            deleted=False,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            target_id__in=target_ids,
            ctime__gte=start_time,
        ).order_by("ctime").all()

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
                    "status": {"type": "string", "description": "状态"},
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
        start_time=start_time,
        end_time=end_time,
        keyword=keyword
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

    return response_ok(data={
        "id": result.id,
        "target_id": result.target_id,
        "schedule_id": result.schedule_id,
        "start_time": result.start_time.isoformat() if result.start_time else None,
        "end_time": result.end_time.isoformat() if result.end_time else None,
        "status": result.status,
        "result_data": result.result_data,
        "error_message": result.error_message,
        "duration": result.duration,
        "target_content": target.content if target else None,
        "target_standard": target.standard if target else None,
    })


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

    stats = await logic.get_stats(
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        start_time=start_time,
        end_time=end_time
    )

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
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要导出的列名列表，可选值：巡检内容、巡检标准、巡检状态、耗时、错误信息、开始时间、结束时间、AI裁决结果、执行结果"
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "导出成功，返回Excel文件",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {
                        "type": "string",
                        "format": "binary"
                    }
                }
            },
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

    excel_data = await logic.export_results_to_excel(
        schedule_id=schedule_id,
        start_time=start_time,
        end_time=end_time,
        columns=columns,
        tenant_id=tenant_id
    )

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
                            "data": {
                                "type": "array",
                                "items": {"type": "object"}
                            },
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
                result = [{
                    "affected_rows": row_count,
                    "message": "SQL executed successfully"
                }]
        except Exception as e:
            response_ok(f"SQL执行失败 {e}")

    return response_ok(data=result)


@inspection_bp.post("/result/add")
@openapi.definition(
    summary="添加巡检结果",
    description="批量添加巡检结果记录，自动通过AI判断执行状态",
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
                            }
                        }
                    },
                    "start_time": {"type": "string", "description": "开始时间 (ISO 8601)"},
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
                                    }
                                }
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
    tenant_id = request.ctx.session.get("tenant_id")

    result_data_array = request_data.get("result_data")
    start_time = request_data.get("start_time")

    if not result_data_array or not start_time:
        raise CustomError(ErrorCode.PARAM_ERROR)

    created_results = []

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

        # Use AI to judge the result
        status = "failed"

        try:
            judgment = await logic.judge_inspection_result(
                target_id,
                result,
                tenant_id
            )

            # Determine status based on AI judgment
            status = "success" if judgment.get("passed") else "failed"
            error_message = None if judgment.get("passed") else judgment.get("reason")

            # Add AI judgment to the item
            item["ai_judgment"] = judgment

        except Exception as e:
            logger.warning(f"AI判断巡检结果失败: {str(e)}")
            # Fallback: use status from item if available
            error_message = f"AI判断失败: {str(e)}"

        # Create InspectionResult
        try:
            result_obj = await logic.create_result({
                "target_id": target_id,
                "schedule_id": schedule_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration": int(execution_time) if execution_time else 0,
                "status": status,
                "result_data": item,
                "error_message": error_message
            }, tenant_id)

            created_results.append({
                "id": result_obj.id,
                "target_id": result_obj.target_id,
                "status": result_obj.status
            })
        except Exception as e:
            logger.error(f"创建巡检结果失败: {str(e)}")
            continue

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
                    "target_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "巡检项ID列表"
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "导出成功，返回Excel文件",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {
                        "type": "string",
                        "format": "binary"
                    }
                }
            },
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
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "Excel文件(.xlsx)"
                    },
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
                                    "schedule_ids": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "category_ids": {
                                        "type": "array",
                                        "items": {"type": "string"}
                                    },
                                    "target_ids": {
                                        "type": "array",
                                        "items": {"type": "string"}
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
    if not uploaded_file.name.endswith(('.xlsx', '.xls')):
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
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {
                        "type": "string",
                        "format": "binary"
                    }
                }
            },
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
    template_path = os.path.join(
        os.path.dirname(__file__),
        "inspection_template_model.xlsx"
    )

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

