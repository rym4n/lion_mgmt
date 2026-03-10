#!/usr/bin/env python
# coding=utf-8

"""
巡检模块API路由层
Inspection Module API Routes Layer
"""

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
from models import Timed, Playbook, PlaybookVersion
from settings import TASK_TOPIC
from . import logic

inspection_bp = Blueprint("inspection", url_prefix="/inspection")
__all__ = ["inspection_bp"]


# ==================== Schedule Management ====================

@inspection_bp.post("/schedule/save")
@openapi.definition(
    summary="创建或更新巡检任务",
    description="创建或更新巡检任务",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["schedule_cron"],
                "properties": {
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
    if not data.get("schedule_cron"):
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
    description="获取巡检任务配置",
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
    tenant_id = request.ctx.session.get("tenant_id")

    schedule = await logic.get_schedule(tenant_id)
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
                    "required": ["name", "content", "category_id", "standard", "action_name"],
                    "properties": {
                        "name": {"type": "string", "description": "巡检项名称"},
                        "content": {"type": "string", "description": "巡检内容"},
                        "category_id": {"type": "string", "description": "关联巡检类别ID"},
                        "standard": {"type": "string", "description": "巡检标准"},
                        "action_name": {"type": "string", "description": "动作名称"},
                        "enabled": {"type": "boolean", "description": "是否启用"},
                        "description": {"type": "string", "description": "巡检项描述"},
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
                                        "content": {"type": "string"},
                                        "category_id": {"type": "string"},
                                        "standard": {"type": "string"},
                                        "action_name": {"type": "string"},
                                        "enabled": {"type": "boolean"},
                                        "description": {"type": "string"},
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
    required_fields = ["name", "content", "category_id", "standard", "action_name"]
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
                    "category_id": {"type": "string", "description": "巡检类别ID"},
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
                                                "content": {"type": "string"},
                                                "category_id": {"type": "string"},
                                                "standard": {"type": "string"},
                                                "action_name": {"type": "string"},
                                                "enabled": {"type": "boolean"},
                                                "description": {"type": "string"},
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
    category_id = data.get("category_id")
    enabled = data.get("enabled")

    result = await logic.list_targets(
        page=page,
        page_size=page_size,
        tenant_id=tenant_id,
        category_id=category_id,
        enabled=enabled
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
                                    "name": {"type": "string"},
                                    "content": {"type": "string"},
                                    "category_id": {"type": "string"},
                                    "standard": {"type": "string"},
                                    "action_name": {"type": "string"},
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

    return response_ok(data={
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
                    "name": {"type": "string", "description": "巡检项名称"},
                    "content": {"type": "string", "description": "巡检内容"},
                    "category_id": {"type": "string", "description": "关联巡检类别ID"},
                    "standard": {"type": "string", "description": "巡检标准"},
                    "action_name": {"type": "string", "description": "动作名称"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "description": {"type": "string", "description": "巡检项描述"},
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
                                    "content": {"type": "string"},
                                    "category_id": {"type": "string"},
                                    "standard": {"type": "string"},
                                    "action_name": {"type": "string"},
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
        "name": target.name,
        "content": target.content,
        "category_id": target.category_id,
        "standard": target.standard,
        "action_name": target.action_name,
        "enabled": target.enabled,
        "description": target.description,
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
    tenant_id = request.ctx.session.get("tenant_id")
    result = await logic.get_all_configs(tenant_id)
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
    return response_ok(data=result)


@inspection_bp.post("/start")
@openapi.definition(
    summary="启动巡检",
    description="启动全局巡检定时任务",
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
    Start global inspection
    """
    tenant_id = request.ctx.session.get("tenant_id")

    # 查询巡检调度配置
    schedule = await logic.get_schedule(tenant_id)
    if not schedule or not schedule.enabled:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 查询是否已经存在全局定时任务
    timed_name = f"inspection_{tenant_id}"
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
        # 创建新的全局定时任务
        # 获取巡检剧本 ID（需要配置环境变量 RY_INSPECTION_PLAYBOOK_ID）
        playbook_id = os.getenv("RY_INSPECTION_PLAYBOOK_ID")
        if not playbook_id:
            raise CustomError(ErrorCode.PARAM_ERROR)

        playbook = await Playbook.get_or_none(id=playbook_id, deleted=False, tenant_id=tenant_id)
        if not playbook:
            raise CustomError(ErrorCode.NOT_FOUND)

        # 获取剧本版本 ID（需要配置环境变量 RY_INSPECTION_PLAYBOOK_VERSION_ID）
        playbook_version_id = os.getenv("RY_INSPECTION_PLAYBOOK_VERSION_ID")
        if not playbook_version_id:
            raise CustomError(ErrorCode.PARAM_ERROR)

        playbook_version = await PlaybookVersion.get_or_none(id=playbook_version_id, tenant_id=tenant_id)
        if not playbook_version:
            raise CustomError(ErrorCode.NOT_FOUND)

        # 构建剧本输入参数（不包含 target_id，剧本会自动查询所有启用的巡检项）
        playbook_input = {
            "flow_id": playbook_version_id,
            "flow_input": {},
            "executor": request.ctx.session.get("account_info").id
        }

        # 创建全局定时任务
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
            tenant_id=tenant_id
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

    return response_ok(data={"message": "启动成功"})


@inspection_bp.post("/pause")
@openapi.definition(
    summary="暂停巡检",
    description="暂停全局巡检定时任务",
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
    Pause global inspection
    """
    tenant_id = request.ctx.session.get("tenant_id")

    # 查询全局定时任务
    timed_name = f"inspection_{tenant_id}"
    timed = await Timed.filter(name=timed_name, deleted=False, tenant_id=tenant_id).first()

    if not timed:
        raise CustomError(ErrorCode.NOT_FOUND)

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

    return response_ok(data={"message": "暂停成功"})


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
                    "status": {"type": "string", "description": "状态"},
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
                                    "total": {"type": "integer"},
                                    "items": {
                                        "type": "array",
                                        "items": {
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
    status = data.get("status")

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
        status=status,
        start_time=start_time,
        end_time=end_time
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

    return response_ok(data={
        "id": result.id,
        "target_id": result.target_id,
        "start_time": result.start_time.isoformat() if result.start_time else None,
        "end_time": result.end_time.isoformat() if result.end_time else None,
        "status": result.status,
        "result_data": result.result_data,
        "error_message": result.error_message,
        "duration": result.duration,
    })


@inspection_bp.post("/result/stats")
@openapi.definition(
    summary="获取巡检统计信息",
    description="获取巡检统计信息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "目标ID"},
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
                                    "total_executions": {"type": "integer"},
                                    "success_count": {"type": "integer"},
                                    "failed_count": {"type": "integer"},
                                    "timeout_count": {"type": "integer"},
                                    "success_rate": {"type": "number"},
                                    "avg_duration": {"type": "number"},
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

    target_id = data.get("target_id")

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
        target_id=target_id,
        start_time=start_time,
        end_time=end_time
    )

    return response_ok(data=stats)


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
    description="添加新的巡检结果记录",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["target_id", "status"],
                "properties": {
                    "target_id": {"type": "string", "description": "巡检目标ID"},
                    "status": {"type": "string", "description": "执行状态: success/failed/timeout"},
                    "result_data": {"type": "object", "description": "巡检结果数据"},
                    "error_message": {"type": "string", "description": "错误信息"},
                    "start_time": {"type": "string", "description": "开始时间 (ISO 8601)"},
                    "end_time": {"type": "string", "description": "结束时间 (ISO 8601)"},
                    "duration": {"type": "integer", "description": "执行耗时(秒)"},
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
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "target_id": {"type": "string"},
                                    "status": {"type": "string"},
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
    tenant_id = request.ctx.session.get("tenant_id")

    # 提取有效字段
    valid_fields = ["target_id", "status", "result_data", "error_message", "start_time", "end_time", "duration"]
    data = {k: v for k, v in request_data.items() if k in valid_fields}

    if not data.get("target_id") or not data.get("status"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 处理时间字段
    if data.get("start_time"):
        try:
            if isinstance(data["start_time"], str):
                data["start_time"] = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    if data.get("end_time"):
        try:
            if isinstance(data["end_time"], str):
                data["end_time"] = datetime.fromisoformat(data["end_time"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    # 如果提供了 result_data，使用 AI 判断巡检结果
    if data.get("result_data"):
        try:
            judgment = await logic.judge_inspection_result(
                data["target_id"],
                data["result_data"],
                tenant_id
            )
            # 将判断结果添加到 result_data 中
            if not isinstance(data["result_data"], dict):
                data["result_data"] = {}
            data["result_data"]["ai_judgment"] = judgment
        except Exception as e:
            logger.warning(f"AI判断巡检结果失败: {str(e)}")

    result = await logic.create_result(data, tenant_id)

    return response_ok(data={
        "id": result.id,
        "target_id": result.target_id,
        "status": result.status
    })
