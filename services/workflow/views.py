#!/usr/bin/env python
# coding=utf-8
import asyncio
from collections import defaultdict
from datetime import datetime
import hashlib
import io
import re
import secrets
import uuid
import zipfile

from sanic.log import logger

from aiocache import caches
import orjson
import pytz
import time
from sanic import Blueprint
from sanic.response import raw
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Count, Max
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import (
    check_playbook_permission,
    load_license,
    playbook_permission,
    requires_permission,
)
from libs.utils import gen_run_id, strip_timezone_to_seconds, runlog_list
from models import (
    Account,
    EventRelatedScript,
    Playbook,
    PlaybookPermissions,
    PlaybookVersion,
    PlaybookWebhook,
    Role,
    Runlog,
    Scene,
    Tag,
    Timed,
    Tool,
    ToolAction,
    ToolResources,
)
from services.tools.views import upload_tool
from services.tools.views import tool_action_playbook_delete, tool_action_update
from settings import TASK_TOPIC, TZ

workflow_bp = Blueprint("workflow", url_prefix="/playbook")
_all__ = ["workflow_bp"]


@workflow_bp.post("/new")
@openapi.definition(
    summary="新建剧本",
    description="新建剧本",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "flow_json_list"],
                "properties": {
                    "name": {"type": "string", "description": "剧本名"},
                    "remark": {"type": "string", "description": "备注"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签，列表格式",
                    },
                    "scenes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "场景，列表格式",
                    },
                    "flow_json_list": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "流程图具体内容, 列表格式, 列表的每个元素是一个独立的剧本json",
                    },
                    "flow_timeout": {
                        "type": "integer",
                        "description": "剧本运行超时时间，最小5分钟最大3天，单位秒 默认300",
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
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:c")
async def create_workflow(request):
    data = request.json
    if not data.get("name") or not data.get("flow_json_list") or not data.get("scenes"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    total_up_value = 0
    # 判断剧本数量是否达到了创建上限
    total_count = await Playbook.filter(deleted=False, internal=False).count()
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)
    for key, item in lic["grouplist"].items():
        if key == "剧本编排":
            if int(item["expireTime"]) > int(time.time() * 1000):
                total_up_value = int(item["value"])
    
    if total_up_value != 99999 and total_count >= total_up_value:
        raise CustomError(ErrorCode.PLAYBOOK_CREATE_LIMIT)

    tenant_id = request.ctx.session.get("tenant_id")

    # 判断name是否已经存在，如果存在的话要抛出异常
    if await Playbook.filter(name=data.get("name"), deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.PLAYBOOK_NAME_EXISTS)

    flow_timeout = 300
    if data.get("flow_timeout"):
        if 300 <= int(data.get("flow_timeout")) <= 259200:
            flow_timeout = int(data.get("flow_timeout"))
        else:
            raise CustomError(ErrorCode.FLOW_TIME_OUT_ERROR)

    flow_json_list = data.get("flow_json_list") or []
    creator = request.ctx.session["account_info"]
    admin = await Role.get(id="111111")
    tenant_admin = await Role.get(name="空间管理员", deleted=False, tenant_id=tenant_id)
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    async with in_transaction():
        playbook = await Playbook.create(
            name=data.get("name"),
            remark=data.get("remark", ""),
            creator=creator,
            internal=maintenance_mode if maintenance_mode else False,
            tenant_id=tenant_id,
        )
        await PlaybookPermissions.create(
            pb=playbook,
            users=creator,
            roles=None,
            permissions=["r", "u", "d", "e", "p"],
            tenant_id=tenant_id,
        )
        await PlaybookPermissions.create(pb=playbook, users=None, roles=admin, permissions=["r", "u", "d", "e", "p"], tenant_id=tenant_id)
        await PlaybookPermissions.create(pb=playbook, users=None, roles=tenant_admin, permissions=["r", "u", "d", "e", "p"], tenant_id=tenant_id)
        if data.get("tags"):
            tags = await Tag.filter(name__in=data.get("tags"), tenant_id=tenant_id).all()
            await playbook.tags.add(*tags)
        if data.get("scenes"):
            scenes = await Scene.filter(name__in=data.get("scenes"), tenant_id=tenant_id).all()
            await playbook.scenes.add(*scenes)
            await caches.get("redis_chat").set(f"workflow_scene_{playbook.id}", ",".join([s.id for s in scenes]))

        await playbook.fetch_related("tags", "scenes")
        for flow_json in flow_json_list:
            flow_json_str = orjson.dumps(flow_json)
            sha1_val = hashlib.sha1(flow_json_str).hexdigest()
            pv = await PlaybookVersion.create(
                playbook=playbook,
                version=sha1_val,
                status=0,
                change_desc=data.get("change_desc", ""),
                flow_json=flow_json,
                creator=request.ctx.session["account_info"],
                updator=request.ctx.session["account_info"],
                flow_timeout=flow_timeout,
                tenant_id=tenant_id,
            )

            action_ids = await statistics_data(flow_json, tenant_id)
            await tool_action_update({pv.id: action_ids}, tenant_id)

    return response_ok({"playbook_id": str(playbook.id), "name": playbook.name})


@workflow_bp.post("/list")
@openapi.definition(
    summary="获取剧本列表",
    description="获取剧本列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "分页的页码，默认1"},
                    "size": {"type": "integer", "description": "分页的大小，默认10"},
                    "keyword": {
                        "type": "string",
                        "description": "搜索时使用，支持搜索名称、备注、ID",
                    },
                    "scene": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "搜索时使用，支持搜索场景",
                    },
                    "tag": {
                        "type": "string",
                        "description": "搜索时使用，支持搜索标签",
                    },
                    "publish": {
                        "type": "integer",
                        "description": "搜索时使用，支持搜索发布状态, 0未发布 1已发布",
                    },
                    "ctime": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "搜索时使用，支持搜索创建时间（UTC格式：2025-01-01T00:00:00Z）",
                        "example": ["2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z"],
                    },
                    "playbook_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "剧本id列表，仅返回这些id对应的剧本",
                        "example": ["111111", "2222222"],
                    },
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
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    },
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
async def list_workflow(request):
    json_params = request.json
    page = int(json_params.get("page", 1)) if int(json_params.get("page", 1)) != 0 else 1
    size = max(1, int(json_params.get("size", 15)))
    offset = (page - 1) * size
    limit = size

    tenant_id = request.ctx.session.get("tenant_id")

    user = request.ctx.session.get("account_info")
    user_id = str(user.id)
    user_roles = set(str(role.id) for role in user.roles)
    permissions = await PlaybookPermissions.filter(Q(users__id=user_id) | Q(roles__id__in=user_roles), permissions__contains=["r"], tenant_id=tenant_id).values_list("pb_id", flat=True)
    pb_ids = set(permissions)

    condition = Q(deleted=False, id__in=pb_ids, tenant_id=tenant_id)

    # 分页查询Account，返回总数和数据
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    condition = condition & Q(internal=maintenance_mode if maintenance_mode else False)
    if json_params.get("playbook_ids"):
        condition = condition & Q(id__in=json_params.get("playbook_ids"))
    if json_params.get("keyword"):
        # 模糊搜索name字段和remark字段
        keyword = json_params.get("keyword")
        condition = condition & (Q(name__icontains=keyword) | Q(remark__icontains=keyword) | Q(id=keyword))
    if json_params.get("scene"):
        scenes = await Scene.filter(name__in=json_params.get("scene"), tenant_id=tenant_id).all()
        if scenes:
            condition = condition & Q(scenes__in=scenes)

    if json_params.get("tag"):
        tags = await Tag.filter(name__in=json_params.get("tag"), tenant_id=tenant_id).all()
        if tags:
            condition = condition & Q(tags__in=tags)

    if len(json_params.get("ctime") or []) == 2:
        start_time = datetime.strptime(json_params["ctime"][0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        end_time = datetime.strptime(json_params["ctime"][1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
        condition = condition & Q(ctime__range=(start_time, end_time))

    pv_condition = Q(playbook_versions__deleted=False)
    if json_params.get("publish") in (0, 1):
        pv_condition = pv_condition & Q(playbook_versions__status=json_params.get("publish"))
        condition = condition & pv_condition

    playbooks = (
        await Playbook.filter(condition)
        .annotate(
            versions=Count("playbook_versions", _filter=pv_condition),
            max_flow_timeout=Max("playbook_versions__flow_timeout"),
        )
        .distinct()
        .order_by("-utime")
        .offset(offset)
        .limit(limit)
        .prefetch_related("scenes", "tags")
    )

    ids = await Playbook.filter(condition).values_list("id", flat=True)
    total = len(set(ids))

    # 组装数据
    data = []
    for p in playbooks:
        data.append(
            {
                **p.json,
                "flow_timeout": p.max_flow_timeout or 300,
                "versions": p.versions,
            }
        )

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": data,
        }
    )


@workflow_bp.post("/published-versions")
@openapi.definition(
    summary="获取全部已发布的剧本版本",
    description="获取全部已发布的剧本版本，包括剧本的描述、名称和版本id",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {},
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
                                        "playbook_id": {"type": "string"},
                                        "playbook_name": {"type": "string"},
                                        "playbook_remark": {"type": "string"},
                                        "version_id": {"type": "string"},
                                        "version": {"type": "string"},
                                        "change_desc": {"type": "string"},
                                    },
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
async def list_published_versions(request):
    tenant_id = request.ctx.session.get("tenant_id")
    user = request.ctx.session.get("account_info")
    user_id = str(user.id)
    user_roles = set(str(role.id) for role in user.roles)

    # 获取用户有权限查看的剧本ID列表
    permissions = await PlaybookPermissions.filter(
        Q(users__id=user_id) | Q(roles__id__in=user_roles),
        permissions__contains=["r"],
        tenant_id=tenant_id
    ).values_list("pb_id", flat=True)
    pb_ids = set(permissions)

    # 查询所有已发布的版本
    pv = (
        await PlaybookVersion.filter(
            status=1,  # 已发布
            deleted=False,
            playbook__deleted=False,
            playbook__internal=False,
            playbook__id__in=pb_ids,
            tenant_id=tenant_id
        )
        .only(
            "id",
            "playbook_id",
            "version",
            "change_desc",
        )
        .all()
        .prefetch_related("playbook")
    )

    result = [
        {
            "playbook_id": p.playbook_id,
            "playbook_name": p.playbook.name,
            "playbook_remark": p.playbook.remark,
            "version_id": p.id,
            "version": p.version,
            "change_desc": p.change_desc,
        }
        for p in pv
    ]
    return response_ok(result)


@workflow_bp.post("/versions")
@openapi.definition(
    summary="获取剧本版本列表",
    description="获取剧本版本列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["playbook_id"],
                "properties": {"playbook_id": {"type": "string", "description": "剧本id"}},
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
                                        "playbook_id": {"type": "string"},
                                        "flow_id": {"type": "string"},
                                        "ctime": {"type": "string"},
                                        "utime": {"type": "string"},
                                        "name": {"type": "string"},
                                        "remark": {"type": "string"},
                                        "change_desc": {"type": "string"},
                                        "status": {"type": "integer"},
                                        "version": {"type": "string"},
                                        "creator": {
                                            "type": "object",
                                            "properties": {
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                            },
                                        },
                                        "updator": {
                                            "type": "object",
                                            "properties": {
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                            },
                                        },
                                        "internal": {"type": "boolean"},
                                    },
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
@playbook_permission("r")
async def list_workflow_versions(request):
    tenant_id = request.ctx.session.get("tenant_id")
    pv = (
        await PlaybookVersion.filter(playbook__id=request.json.get("playbook_id"), deleted=False, tenant_id=tenant_id)
        .only(
            "id",
            "ctime",
            "utime",
            "playbook_id",
            "change_desc",
            "status",
            "version",
            "creator_id",
            "updator_id",
        )
        .all()
        .prefetch_related("playbook", "creator", "updator")
    )
    result = [
        {
            "playbook_id": p.playbook_id,
            "flow_id": p.id,
            "ctime": strip_timezone_to_seconds(p.ctime),
            "utime": strip_timezone_to_seconds(p.utime),
            "name": p.playbook.name,
            "remark": p.playbook.remark,
            "change_desc": p.change_desc,
            "status": p.status,
            "version": p.version,
            "creator": {
                "username": p.creator.username,
                "display_name": p.creator.display_name,
            },
            "updator": {
                "username": p.updator.username,
                "display_name": p.updator.display_name,
            },
            "internal": p.playbook.internal,
        }
        for p in pv
    ]
    return response_ok(result)


@workflow_bp.post("/export")
@openapi.definition(
    summary="导出剧本版本",
    description="导出剧本版本及工具包（ZIP格式）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["playbook_ids"],
                "properties": {
                    "playbook_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "剧本id列表",
                    }
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
@requires_permission("workflow:r")
@requires_permission("tool:r")
@playbook_permission("r")
async def export_workflow(request):
    tenant_id = request.ctx.session.get("tenant_id")
    playbook_ids = request.json.get("playbook_ids", [])

    pv = await PlaybookVersion.filter(playbook__id__in=playbook_ids, deleted=False, tenant_id=tenant_id).all().prefetch_related("playbook")

    tool_keys = set()
    resource_ids = set()
    result = []

    for p in pv:
        if p.flow_json and "nodes" in p.flow_json:
            for node in p.flow_json["nodes"]:
                if node.get("type") == "action-node":
                    props = node.get("properties", {})
                    name = props.get("app_name")
                    version = props.get("version")
                    if name and version:
                        tool_keys.add((name, version))

                    resources = props.get("resources", [])
                    if resources:
                        resource_ids.update(resources)

        result.append(
            {
                "playbook_id": p.playbook_id,
                "flow_id": p.id,
                "ctime": strip_timezone_to_seconds(p.ctime),
                "utime": strip_timezone_to_seconds(p.utime),
                "name": p.playbook.name,
                "remark": p.playbook.remark,
                "change_desc": p.change_desc,
                "status": p.status,
                "version": p.version,
                "flow_json": p.flow_json,
            }
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflow.json", orjson.dumps(result))

        if resource_ids:
            resources_objs = await ToolResources.filter(id__in=list(resource_ids), deleted=False).all()
            resources_list = []
            for res in resources_objs:
                resources_data = orjson.loads(res.resources) if res.resources else {}
                for value in resources_data.values():
                    if value.get("data_type") == "password":
                        value["value"] = "******"

                res_dict = {
                    "id": res.id,
                    "ctime": strip_timezone_to_seconds(res.ctime),
                    "utime": strip_timezone_to_seconds(res.utime),
                    "tool_name": res.tool_name,
                    "name": res.name,
                    "resources": resources_data,
                    "engine": res.engine,
                }
                resources_list.append(res_dict)
            zf.writestr("resources.json", orjson.dumps(resources_list))

        for tool_key in tool_keys:
            name, version = tool_key
            content = await utils.get_from_s3("system", f"{name}/{version}.zip", "tools")
            if content:
                filename = f"{name}_{version}.zip"
                zf.writestr(filename, content)

    zip_buffer.seek(0)
    return raw(
        zip_buffer.read(),
        content_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="workflow_export.zip"'},
    )


@workflow_bp.post("/flow-json")
@openapi.definition(
    summary="获取剧本版本的flow_json",
    description="获取剧本版本的flow_json",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本id"},
                    "playbook_id": {"type": "string", "description": "剧本ID"},
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
                            "data": {"type": "object"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
@playbook_permission("r")
async def get_flow_json(request):
    data = None
    tenant_id = request.ctx.session.get("tenant_id")
    pv = await PlaybookVersion.get_or_none(id=request.json.get("flow_id"), tenant_id=tenant_id).prefetch_related("playbook", "creator", "updator")
    if pv:
        data = pv.json
    else:
        pv = await PlaybookVersion.filter(playbook__id=request.json.get("playbook_id"), deleted=False, tenant_id=tenant_id).order_by("-version").first().prefetch_related("playbook", "creator", "updator")
        if pv:
            data = pv.json
    return response_ok(data)


# @workflow_bp.post("/detail")
# @requires_permission("workflow:r")
# async def detail_workflow(request):
#
#     playbook = await PlaybookVersion.get(id=request.json.get("id")).prefetch_related("creator", "updator", "playbook")
#     if not playbook:
#         raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
#     return response_ok(playbook.json)


@workflow_bp.post("/edit")
@openapi.definition(
    summary="修改剧本的基本信息",
    description="修改剧本的基本信息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["playbook_id"],
                "properties": {
                    "playbook_id": {"type": "string", "description": "剧本ID"},
                    "name": {"type": "string", "description": "剧本名"},
                    "remark": {"type": "string", "description": "备注"},
                    "scenes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关联场景名称，列表格式",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签名称，列表格式",
                    },
                    "flow_timeout": {
                        "type": "integer",
                        "description": "剧本运行超时时间，最小5分钟最大3天，单位秒 默认300",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "修改成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "object"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
@playbook_permission("u")
async def edit_workflow(request):
    params = request.json

    tenant_id = request.ctx.session.get("tenant_id")

    playbook = await Playbook.get(id=params.get("playbook_id"), tenant_id=tenant_id).prefetch_related("scenes", "tags")
    if not playbook:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    playbook.name = params.get("name")
    playbook.remark = params.get("remark", "")
    await playbook.scenes.clear()
    if params.get("scenes"):
        scenes = await Scene.filter(name__in=params.get("scenes"), tenant_id=tenant_id)
        await playbook.scenes.add(*scenes)
        await caches.get("redis_chat").set(f"workflow_scene_{playbook.id}", ",".join([s.id for s in scenes]))
    await playbook.tags.clear()
    if params.get("tags"):
        tags = await Tag.filter(name__in=params.get("tags"), tenant_id=tenant_id)
        await playbook.tags.add(*tags)
    if params.get("flow_timeout"):
        if 300 > int(params.get("flow_timeout")) or int(params.get("flow_timeout")) > 259200:
            raise CustomError(ErrorCode.FLOW_TIME_OUT_ERROR)
        await PlaybookVersion.filter(playbook_id=playbook.id, tenant_id=tenant_id).update(flow_timeout=int(params.get("flow_timeout", 300)))
    playbook.updator = request.ctx.session["account_info"]
    await playbook.save()
    return response_ok(playbook.json)


@workflow_bp.post("/edit-flow")
@openapi.definition(
    summary="修改剧本流程图内容",
    description="修改剧本流程图内容,修改已发布的剧本会自动生成一个新的版本",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id", "flow_json"],
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本ID"},
                    "flow_json": {"type": "object", "description": "流程图布局信息"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "修改成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "object"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
@playbook_permission("u")
async def edit_workflow_flow(request):
    if not request.json.get("flow_id") or not request.json.get("flow_json"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    pv = await PlaybookVersion.get(id=request.json.get("flow_id"), tenant_id=tenant_id).prefetch_related("playbook", "creator", "updator")
    old_data = pv.flow_json
    pv.flow_json = request.json.get("flow_json")
    pv.updator = request.ctx.session["account_info"]
    # 已发布的剧本是不允许修改的
    if pv.status == 1:
        flow_json_str = orjson.dumps(pv.flow_json)
        sha1_val = hashlib.sha1(flow_json_str).hexdigest()
        pv = await PlaybookVersion.create(
            playbook=pv.playbook,
            version=sha1_val,
            flow_json=pv.flow_json,
            change_desc=None,
            status=0,
            creator=request.ctx.session["account_info"],
            updator=request.ctx.session["account_info"],
            tenant_id=tenant_id,
        )
    else:
        await pv.save()

    if old_data:
        action_ids = await statistics_data(old_data, tenant_id)
        await tool_action_playbook_delete({pv.id: action_ids}, tenant_id)

    action_ids = await statistics_data(pv.flow_json, tenant_id)
    await tool_action_update({pv.id: action_ids}, tenant_id)

    return response_ok(pv.json)


@workflow_bp.post("/publish")
@openapi.definition(
    summary="发布剧本",
    description="发布剧本",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id", "change_desc"],
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本ID"},
                    "change_desc": {"type": "string", "description": "版本变更说明"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "发布成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
@playbook_permission("u")
async def publish_workflow(request):
    if not request.json.get("flow_id") or not request.json.get("change_desc"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    pv = await PlaybookVersion.get_or_none(id=request.json.get("flow_id"), status=0, tenant_id=tenant_id)
    if not pv:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    pv.status = 1
    pv.updator = request.ctx.session["account_info"]
    pv.change_desc = request.json.get("change_desc")
    await pv.save()
    return response_ok("发布成功")


@workflow_bp.post("/del")
@openapi.definition(
    summary="删除剧本",
    description="删除剧本",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["playbook_id"],
                "properties": {"playbook_id": {"type": "string", "description": "剧本ID"}},
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
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:d")
@playbook_permission("d")
async def delete_workflow(request):
    tenant_id = request.ctx.session.get("tenant_id")

    playbook = await Playbook.get_or_none(id=request.json.get("playbook_id"), deleted=False, tenant_id=tenant_id)
    if not playbook:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
    if playbook.internal:
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)
    pvs = await PlaybookVersion.filter(playbook=playbook, deleted=False, tenant_id=tenant_id).all()
    pv_ids = [pv.id for pv in pvs]
    pv_versions = [pv.version for pv in pvs]
    condition = Q(tenant_id=tenant_id)
    if pv_ids:
        for pv_id in pv_ids:
            condition &= Q(script_id__contains=[pv_id])
        if await EventRelatedScript.filter(condition).exists():
            raise CustomError(ErrorCode.PLAYBOOK_USED_BY_EVENT)
        if await Timed.filter(playbook=playbook, playbook_version__in=pv_versions, deleted=False, tenant_id=tenant_id).exists():
            raise CustomError(ErrorCode.PLAYBOOK_USED_BY_TIMER)

    for pv in pvs:
        if pv.flow_json:
            action_ids = await statistics_data(pv.flow_json, tenant_id)
            await tool_action_playbook_delete({pv.id: action_ids}, tenant_id)
        pv.deleted = True
        await pv.save()

    playbook.deleted = True
    await playbook.save()

    return response_ok("success")


@workflow_bp.post("/del-version")
@openapi.definition(
    summary="删除剧本版本",
    description="删除剧本版本",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id"],
                "properties": {"flow_id": {"type": "string", "description": "剧本版本ID"}},
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
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:d")
@playbook_permission("d")
async def delete_workflow_version(request):
    tenant_id = request.ctx.session.get("tenant_id")
    pv = await PlaybookVersion.filter(id=request.json.get("flow_id"), deleted=False, tenant_id=tenant_id).prefetch_related("playbook").first()
    if not pv:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
    if pv.playbook.internal:
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)

    if await EventRelatedScript.filter(script_id__contains=[pv.id], deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.PLAYBOOK_USED_BY_EVENT)
    if await Timed.filter(playbook_id=pv.playbook_id, playbook_version=pv.version, deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.PLAYBOOK_USED_BY_TIMER)

    if pv.flow_json:
        action_ids = await statistics_data(pv.flow_json, tenant_id)
        await tool_action_playbook_delete({pv.id: action_ids}, tenant_id)
    if await PlaybookVersion.filter(playbook_id=pv.playbook_id, deleted=False, tenant_id=tenant_id).count() == 1:
        pv.playbook.deleted = True
        await pv.playbook.save()

    pv.deleted = True
    await pv.save()
    return response_ok({"flow_id": str(pv.id)})


@workflow_bp.post("/stop")
@openapi.definition(
    summary="停止执行",
    description="停止执行",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["run_id"],
                "properties": {"run_id": {"type": "string", "description": "运行ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "停止成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
async def stop_workflow(request):
    run_id = request.json.get("run_id")
    msg = {"run_type": "kill", "run_id": run_id, "run_args": {"executor": request.ctx.session["account_info"].id}, "tenant_id": request.ctx.session.get("tenant_id")}
    await aproduce(TASK_TOPIC, msg)
    return response_ok({"run_id": run_id})


@workflow_bp.post("/debug")
@openapi.definition(
    summary="执行接口",
    description="执行接口",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "执行类型"},
                    "flow_id": {"type": "string", "description": "剧本id"},
                    "flow_input": {"type": "object", "description": "剧本输入参数"},
                    "app_name": {"type": "string", "description": "应用名称，英文标识"},
                    "name": {"type": "string", "description": "应用中文名称"},
                    "app_ver": {"type": "string", "description": "应用版本号"},
                    "main": {"type": "string", "description": "主程序文件名"},
                    "action_input": {"type": "object", "description": "动作输入参数"},
                    "resource": {"type": "string", "description": "资源名称"},
                    "node_name": {"type": "string", "description": "节点名称"},
                    "action": {"type": "string", "description": "动作名称 英文标识"},
                    "action_name": {
                        "type": "string",
                        "description": "动作名称 中文名称",
                    },
                    "retry_times": {"type": "integer", "description": "重试次数"},
                    "timeout": {"type": "integer", "description": "超时时间,单位秒"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "执行成功",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
@requires_permission("workflow:u")
async def execute_action(request):
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    utime = datetime.now(TZ)
    message = request.json

    tenant_id = request.ctx.session.get("tenant_id")

    if message.get("type") in ("flow", "action", "code"):
        if message.get("type") == "flow":
            pv = await PlaybookVersion.get_or_none(id=message.get("flow_id"), tenant_id=tenant_id)
            playbook_id = pv.playbook_id
            has_access = await check_playbook_permission(
                request=request,
                user=request.ctx.session["account_info"],
                playbook_id=playbook_id,
                required_permissions=["e"],
            )
            if not has_access:
                await resp.send("data: 缺少剧本执行权限，执行失败\n\n")
                await resp.eof()
                return resp
        run_id = gen_run_id()
        message["executor"] = request.ctx.session["account_info"].id
        msg = {"run_type": message["type"], "run_id": run_id, "run_args": message, "tenant_id": tenant_id}
        await aproduce(TASK_TOPIC, msg)
        sent_id = []
        for _ in range(60):
            logs = await Runlog.filter(run_id=run_id, utime__gte=utime).all()
            if logs:
                utime = logs[0].utime
                for log in logs:
                    if log.id not in sent_id:
                        sent_id.append(log.id)
                        await resp.send(f"data: [{orjson.dumps(log.json).decode()}]\n\n")
                if message.get("type") == "action" and len(sent_id) >= len(message.get("resource", "").split()):
                    break
                if message.get("type") == "flow" and any(log.node_type == "end-node" and log.playbook_version_id == message.get("flow_id") for log in logs):
                    break
                if message.get("type") == "code":
                    break
                if any(log.status in [-1, 2] for log in logs):
                    break
            await asyncio.sleep(1)

    if resp:
        await resp.eof()

    return resp


@workflow_bp.post("/input")
@openapi.definition(
    summary="获取指定剧本的输入参数",
    description="获取指定剧本的输入参数",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id"],
                "properties": {"flow_id": {"type": "string", "description": "剧本版本ID"}},
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
                            "data": {"type": "array", "items": {"type": "object"}},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
@playbook_permission("r")
async def input_data(request):
    tenant_id = request.ctx.session.get("tenant_id")
    input_list = await get_playbook_input(request.json.get("flow_id"), tenant_id)

    return response_ok(input_list)


async def get_playbook_input(playbook_id, tenant_id):
    playbook = await PlaybookVersion.get_or_none(id=playbook_id, tenant_id=tenant_id)
    if not playbook:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    input_list = []

    if playbook.flow_json:
        nodes = playbook.flow_json.get("nodes", [])
        if not nodes or "properties" not in nodes[0] or "start_params" not in nodes[0]["properties"]:
            return input_list

        input_id = nodes[0]["id"]
        start_params = nodes[0]["properties"]["start_params"]

        param_map = {param["name"]: {"type": param["type"], "description": param["desc"]} for param in start_params}

        pattern = rf"{re.escape(input_id)}\.output\.([a-zA-Z0-9_\u4e00-\u9fff]+)(?=[\)}}])"

        matches = re.findall(pattern, str(playbook.flow_json))
        unique_matches = list(set(matches))  # 去重
        for param_key in unique_matches:
            if param_key in param_map:
                input_list.append({param_key: param_map[param_key]})

    return input_list


async def statistics_data(flow_json, tenant_id):
    actions_list = []
    for node in flow_json["nodes"]:
        if "app_name" in node["properties"] and "action" in node["properties"] and "version" in node["properties"]:
            tool_name = node["properties"]["app_name"]
            version_number = node["properties"]["version"]
            action_name = node["properties"]["action"]
            actions = await ToolAction.filter(tool_name=tool_name, version_number=version_number, tenant_id=tenant_id).all()
            for action in actions:
                if action_name == action.action["func"]:
                    actions_list.append(action.id)

    return actions_list


@workflow_bp.post("/tag/new")
@openapi.definition(
    summary="新建标签",
    description="新建标签（需要剧本新增权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "标签名称"}},
            }
        }
    },
    response={
        "200": {
            "description": "新建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:c")
async def new_tag(request):
    name = request.json.get("name", "").strip()
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    if await Tag.filter(name=name, type=0, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.TAG_EXIST_ERROR)

    await Tag.create(name=name, type=0, tenant_id=tenant_id)

    return response_ok("标签新建成功")


@workflow_bp.post("/tag/edit")
@openapi.definition(
    summary="编辑标签",
    description="编辑标签（需要剧本更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["old_name", "new_name"],
                "properties": {
                    "old_name": {"type": "string", "description": "原标签名称"},
                    "new_name": {"type": "string", "description": "新标签名称"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "编辑成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
async def edit_tag(request):
    old_name = request.json.get("old_name", "").strip()
    new_name = request.json.get("new_name", "").strip()

    if not old_name or not new_name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tag = await Tag.get_or_none(name=old_name, type=0, tenant_id=tenant_id)
    if not tag:
        raise CustomError(ErrorCode.TAG_NOT_FOUND_ERROR)

    if old_name == new_name:
        return response_ok("标签编辑成功")

    if await Tag.filter(name=new_name, type=0, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.TAG_EXIST_ERROR)

    tag.name = new_name
    await tag.save()

    return response_ok("标签编辑成功")


@workflow_bp.post("/tag/list")
@openapi.definition(
    summary="获取标签列表",
    description="获取标签列表（需要剧本查看权限）",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "array", "items": {"type": "string"}},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
async def tag_list(request):
    tenant_id = request.ctx.session.get("tenant_id")
    tags = await Tag.filter(type=0, tenant_id=tenant_id).all()
    return response_ok([tag.name for tag in tags])


@workflow_bp.post("/tag/remove")
@openapi.definition(
    summary="删除标签",
    description="删除标签（需要剧本删除权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "标签名称"}},
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
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:d")
async def remove_tag(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    tag = await Tag.get(name=name, type=0, tenant_id=tenant_id)
    await tag.delete()
    return response_ok("标签删除成功")


@workflow_bp.post("/permissions/add")
@openapi.definition(
    summary="新增剧本权限",
    description="新增剧本权限（需要剧本更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["playbook_id"],
                "properties": {
                    "playbook_id": {"type": "string", "description": "剧本id"},
                    "users": {
                        "type": "object",
                        "description": '用户权限 {"id":["r","u","d","e","p"]}',
                    },
                    "roles": {
                        "type": "object",
                        "description": '角色权限 {"id":["r","u","d","e","p"]}',
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "权限更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
@playbook_permission("p")
async def add_permission(request):
    playbook_id = request.json.get("playbook_id")
    users = request.json.get("users", [])
    roles = request.json.get("roles", [])

    if not playbook_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    playbook = await Playbook.get_or_none(id=playbook_id, tenant_id=tenant_id)
    if not playbook:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)
    if playbook.internal:
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)

    # 处理用户权限
    for uid, perms in users.items():
        await PlaybookPermissions.update_or_create(
            pb_id=playbook_id,
            users_id=uid,
            tenant_id=tenant_id,
            defaults={"permissions": perms, "roles_id": None},
        )
        await request.app.ctx.cache_client.set((playbook_id, uid), perms, ttl=2592000)

    # 处理角色权限
    for rid, perms in roles.items():
        if rid != "111111":
            await PlaybookPermissions.update_or_create(
                pb_id=playbook_id,
                roles_id=rid,
                tenant_id=tenant_id,
                defaults={"permissions": perms, "users_id": None},
            )
        await request.app.ctx.cache_client.set((playbook_id, rid), perms, ttl=2592000)

    return response_ok("权限更新成功")


@workflow_bp.post("/permissions/current")
@openapi.definition(
    summary="获取当前用户所能查看的剧本的权限",
    description="获取当前用户所能查看的剧本的权限",
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
                                        "id": {"type": "string"},
                                        "permissions": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
async def select_my_permission(request):
    tenant_id = request.ctx.session.get("tenant_id")
    pps = await PlaybookPermissions.filter(
        users__id=request.ctx.session["sub"], tenant_id=tenant_id, deleted=False
    ).all()
    account = await Account.get(id=request.ctx.session["sub"], deleted=False).prefetch_related("roles")
    account_roles = [role.id for role in account.roles]
    account_pps = await PlaybookPermissions.filter(
        roles__id__in=account_roles, tenant_id=tenant_id, deleted=False
    ).all()
    data = [{"id": pp.pb_id, "permissions": pp.permissions} for pp in account_pps] + [
        {"id": pp.pb_id, "permissions": pp.permissions} for pp in pps
    ]
    merged_permissions = {}
    for item in data:
        pb_id = item["id"]
        if pb_id not in merged_permissions:
            merged_permissions[pb_id] = set()
        merged_permissions[pb_id].update(item["permissions"] or [])

    result = [{"id": key, "permissions": sorted(value)} for key, value in merged_permissions.items()]
    return response_ok(result)


@workflow_bp.post("/permissions/select")
@openapi.definition(
    summary="获取剧本权限",
    description="获取剧本权限（需要剧本查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["playbook_id"],
                "properties": {"playbook_id": {"type": "string", "description": "剧本id"}},
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
                            "data": {"type": "array", "items": {"type": "object"}},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
@playbook_permission("r")
async def select_permission_all(request):
    playbook_id = request.json.get("playbook_id")
    if not playbook_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    pps = await PlaybookPermissions.filter(pb_id=playbook_id, tenant_id=tenant_id).all().prefetch_related("users", "roles")

    return response_ok([pp.json for pp in pps])


@workflow_bp.post("/webhook/create")
@openapi.definition(
    summary="生成剧本Webhook URL",
    description="为指定剧本版本生成webhook URL，用于外部系统触发剧本执行",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id"],
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本ID"},
                    "description": {"type": "string", "description": "webhook描述"},
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
                                    "webhook_url": {"type": "string"},
                                    "description": {"type": "string"},
                                    "creator": {"type": "string"},
                                    "ctime": {"type": "string"},
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
@playbook_permission("u")
async def create_webhook(request):
    flow_id = request.json.get("flow_id")
    description = request.json.get("description", "")

    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    pv = await PlaybookVersion.get_or_none(id=flow_id, tenant_id=tenant_id).prefetch_related("playbook")
    if not pv:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    if await PlaybookWebhook.filter(playbook_version=pv, deleted=False, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.WEBHOOK_EXISTS)

    # 生成唯一的webhook token
    webhook_token = secrets.token_urlsafe(10)

    webhook = await PlaybookWebhook.create(playbook_version=pv, webhook_token=webhook_token, enabled=True, creator=request.ctx.session["account_info"], description=description, tenant_id=tenant_id)

    return response_ok(webhook.json)


@workflow_bp.post("/webhook/get")
@openapi.definition(
    summary="获取剧本版本的Webhook",
    description="获取指定剧本版本的webhook",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id"],
                "properties": {"flow_id": {"type": "string", "description": "剧本版本ID"}},
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
                                    "webhook_url": {"type": "string"},
                                    "description": {"type": "string"},
                                    "creator": {"type": "object"},
                                    "playbook_version_id": {"type": "string"},
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
@playbook_permission("r")
async def list_webhook(request):
    flow_id = request.json.get("flow_id")

    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    pv = await PlaybookVersion.get_or_none(id=flow_id, tenant_id=tenant_id)
    if not pv:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    webhook = await PlaybookWebhook.get_or_none(playbook_version_id=flow_id, deleted=False, tenant_id=tenant_id).prefetch_related("creator")

    return response_ok(webhook.json if webhook else {})


@workflow_bp.post("/webhook/delete")
@openapi.definition(
    summary="删除Webhook",
    description="删除指定的webhook",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["webhook_id"],
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本ID"},
                    "webhook_id": {"type": "string", "description": "WebhookID"},
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
                            "data": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:u")
@playbook_permission("u")
async def delete_webhook(request):
    webhook_id = request.json.get("webhook_id")
    flow_id = request.json.get("flow_id")
    if not webhook_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    webhook = await PlaybookWebhook.get_or_none(id=webhook_id, playbook_version_id=flow_id, deleted=False, tenant_id=tenant_id)
    if not webhook:
        raise CustomError(ErrorCode.PARAM_ERROR)

    webhook.deleted = True
    await webhook.save()

    return response_ok("success")


# 无需权限验证的webhook执行接口，通过webhook_token验证
@workflow_bp.get("/webhook/execute", name="execute_webhook_get")
@openapi.definition(
    summary="执行剧本（通过Webhook）",
    description="通过webhook token直接执行剧本，支持GET和POST调用",
    parameter=[
        {
            "name": "token",
            "in": "query",
            "description": "webhook唯一标识令牌",
            "required": True,
            "schema": {"type": "string"},
        },
        {
            "name": "params",
            "in": "query",
            "description": "剧本输入参数，json格式字符串",
            "required": False,
            "schema": {"type": "string"},
        },
    ],
    response={
        "200": {
            "description": "执行成功",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def execute_webhook(request):
    """
    通过webhook token执行剧本
    """
    # 查找webhook
    webhook_token = request.args.get("token")
    if not webhook_token:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    webhook = await PlaybookWebhook.filter(webhook_token=webhook_token, deleted=False, tenant_id=tenant_id).prefetch_related("playbook_version", "playbook_version__playbook", "creator").first()

    if not webhook:
        raise CustomError(ErrorCode.PARAM_ERROR)

    pv = webhook.playbook_version

    # 获取flow_input
    flow_input = request.args.get("params")
    if flow_input:
        try:
            flow_input = orjson.loads(flow_input)
        except orjson.JSONDecodeError:
            raise CustomError(ErrorCode.PARAM_ERROR)

    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    utime = datetime.now(TZ)

    # 构造执行消息
    run_id = gen_run_id()

    msg = {
        "run_type": "flow",
        "run_id": run_id,
        "run_args": {
            "type": "flow",
            "flow_id": pv.id,
            "flow_input": flow_input,
            "executor": webhook.creator_id,
        },
        "tenant_id": tenant_id,
    }
    await aproduce(TASK_TOPIC, msg)

    sent_id = []
    for _ in range(60):
        logs = await Runlog.filter(run_id=run_id, utime__gte=utime, tenant_id=tenant_id).all()
        if logs:
            utime = logs[0].utime
            for log in logs:
                if log.id not in sent_id:
                    sent_id.append(log.id)
                    await resp.send(f"data: [{orjson.dumps(log.json).decode()}]\n\n")
            if any(log.node_type == "end-node" for log in logs):
                break
            if any(log.status in [-1, 2] for log in logs):
                break
        await asyncio.sleep(1)

    await resp.eof()

    return resp


@workflow_bp.post("/dashboard/statistics")
@openapi.definition(
    summary="获取剧本仪表盘统计数据",
    description="获取指定剧本在指定时间范围内的执行统计数据",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id", "start", "end"],
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本ID"},
                    "start": {
                        "type": "string",
                        "description": "查询开始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end": {
                        "type": "string",
                        "description": "查询结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
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
                                    "total_runs": {"type": "integer"},
                                    "success_count": {"type": "integer"},
                                    "fail_count": {"type": "integer"},
                                    "success_rate": {"type": "number"},
                                    "avg_execution_time": {"type": "number"},
                                    "min_execution_time": {"type": "number"},
                                    "max_execution_time": {"type": "number"},
                                    "last_execution_time": {"type": "string"},
                                    "last_status": {"type": "string"},
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
@playbook_permission("r")
async def dashboard_statistics(request):
    data = request.json
    flow_id = data.get("flow_id")
    start = data.get("start")
    end = data.get("end")

    start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)

    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    sql = """
        SELECT
            TO_CHAR(t.run_end, 'YYYY-MM-DD') as date,
            COUNT(t.run_id) as total_runs,
            SUM(CASE WHEN t.is_fail = 0 THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN t.is_fail = 1 THEN 1 ELSE 0 END) as fail_count
        FROM (
            SELECT
                run_id,
                MAX(end_time) as run_end,
                MAX(CASE WHEN status NOT IN (0, 1) THEN 1 ELSE 0 END) as is_fail
            FROM runlog
            WHERE playbook_version_id = $1
              AND start_time BETWEEN $2 AND $3
            GROUP BY run_id
        ) as t
        WHERE t.run_end IS NOT NULL
        GROUP BY date
        ORDER BY date ASC;
    """

    async with in_transaction() as conn:
        results = await conn.execute_query_dict(sql, [flow_id, start_dt, end_dt])

    final_result = []

    for row in results:
        total = row["total_runs"]
        success_count = int(row["success_count"])
        fail = int(row["fail_count"])
        day = row["date"]

        rate_val = round(success_count / total, 2) if total else 0
        success_rate = str(rate_val * 100) + "%"

        day_result = {
            "date": day,
            "total_runs": total,
            "success_count": success_count,
            "fail_count": fail,
            "success_rate": success_rate,
        }
        final_result.append(day_result)

    return response_ok(final_result)


@workflow_bp.post("/import")
@openapi.definition(
    summary="导入剧本",
    description="导入剧本",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "导入ZIP包",
                    }
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
                            "data": {"type": "object"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:c")
@requires_permission("tool:c")
async def import_workflow(request):
    file_obj = request.files.get("file")
    if not file_obj:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    creator = request.ctx.session["account_info"]
    admin = await Role.get(id="111111")
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])

    imported = {"playbooks": 0, "tools": 0, "resources": 0, "skipped_versions": 0, "skipped_resources": 0}

    buf = io.BytesIO(file_obj.body)
    with zipfile.ZipFile(buf, "r", metadata_encoding="utf-8") as zf:
        names = zf.namelist()

        if "resources.json" in names:
            data = orjson.loads(zf.read("resources.json"))
            for res in data:
                name = res.get("name")
                id_ = res.get("id")
                tool_name = res.get("tool_name")
                engine = res.get("engine")
                resources = res.get("resources") or {}
                exist = await ToolResources.get_or_none(
                    name=name,
                    deleted=False,
                    tool_name=tool_name,
                    internal=maintenance_mode if maintenance_mode else False,
                    tenant_id=tenant_id,
                )
                if exist:
                    imported["skipped_resources"] += 1
                    continue
                await ToolResources.create(
                    id=id_,
                    tool_name=tool_name,
                    name=name,
                    engine=engine,
                    resources=orjson.dumps(resources).decode(),
                    internal=maintenance_mode if maintenance_mode else False,
                    creator=creator.username,
                    updater=creator.username,
                    tenant_id=tenant_id,
                )
                imported["resources"] += 1

        lic = load_license()
        if not lic:
            raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)

        # 模拟文件对象类，用于适配 upload_tool 接口
        class MockFile:
            def __init__(self, name, body):
                self.name = name
                self.body = body

        for fn in names:
            if not fn.endswith(".zip"):
                continue
            base = fn[:-4]
            idx = base.rfind("_")
            if idx <= 0:
                continue
            t_name = base[:idx]
            t_ver = base[idx + 1 :]
            if await Tool.filter(name=t_name, deleted=False, tenant_id=tenant_id, version=t_ver).exists():
                imported["skipped_versions"] += 1
                continue
            content = zf.read(fn)

            # 构造 MockFile 对象
            mock_file = MockFile(name=fn, body=content)

            result = await upload_tool(mock_file, lic, creator.username, tenant_id, request)

            if result and result.get("msg") == "success":
                imported["tools"] += 1

        if "workflow.json" in names:
            flows = orjson.loads(zf.read("workflow.json"))
            for item in flows:
                ver = item.get("version")
                if await PlaybookVersion.filter(version=ver, deleted=False, tenant_id=tenant_id).exists():
                    imported["skipped_versions"] += 1
                    continue
                name = item.get("name")
                remark = item.get("remark", "")
                change_desc = item.get("change_desc", "")
                flow_json = item.get("flow_json") or {}
                async with in_transaction():
                    pb = await Playbook.get_or_none(name=name, deleted=False, tenant_id=tenant_id)
                    if not pb:
                        pb = await Playbook.create(
                            id=str(uuid.uuid4()),
                            name=name,
                            remark=remark,
                            creator=creator,
                            internal=maintenance_mode if maintenance_mode else False,
                            tenant_id=tenant_id,
                        )
                        await PlaybookPermissions.create(
                            pb=pb,
                            users=creator,
                            roles=None,
                            permissions=["r", "u", "d", "e", "p"],
                            tenant_id=tenant_id,
                        )
                        await PlaybookPermissions.create(
                            pb=pb,
                            users=None,
                            roles=admin,
                            permissions=["r", "u", "d", "e", "p"],
                            tenant_id=tenant_id,
                        )
                    pv = await PlaybookVersion.create(
                        playbook=pb,
                        version=ver,
                        status=0,
                        change_desc=change_desc,
                        flow_json=flow_json,
                        creator=creator,
                        updator=creator,
                        flow_timeout=300,
                        tenant_id=tenant_id,
                    )
                    action_ids = await statistics_data(flow_json, tenant_id)
                    await tool_action_update({pv.id: action_ids}, tenant_id)
                    imported["playbooks"] += 1
    if imported["skipped_resources"] > 0 or imported["skipped_versions"] > 0:
        raise CustomError(ErrorCode.IMPORT_FLOW_ERROR)
    return response_ok("导入成功")


@workflow_bp.post("/ai-config/create")
@openapi.definition(
    summary="创建AI服务配置",
    description="创建AI服务配置，需要ai_config:c权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "url", "models", "apikeys"],
                "properties": {
                    "name": {"type": "string", "description": "AI服务名称"},
                    "url": {"type": "string", "description": "API地址"},
                    "models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "模型列表",
                    },
                    "apikeys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "API密钥列表",
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
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("ai_config:c")
async def create_ai_config(request):
    from models import Config

    params = request.json
    name = params.get("name")
    url = params.get("url")
    models = params.get("models")
    apikeys = params.get("apikeys")

    if not name or not url or not models or not apikeys:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if not isinstance(models, list) or not isinstance(apikeys, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    # 检查名称是否已存在
    existing = await Config.get_or_none(key=name, module="ai_service", deleted=False, tenant_id=tenant_id)
    if existing:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 构造 value（整个 JSON 会被加密）
    value = orjson.dumps({"url": url, "models": models, "apikeys": apikeys}).decode()

    await Config.create(key=name, value=value, module="ai_service", tenant_id=tenant_id)

    return response_ok("创建成功")


@workflow_bp.post("/ai-config/update")
@openapi.definition(
    summary="更新AI服务配置",
    description="更新AI服务配置，需要ai_config:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "url", "models", "apikeys"],
                "properties": {
                    "name": {"type": "string", "description": "AI服务名称"},
                    "url": {"type": "string", "description": "API地址"},
                    "models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "模型列表",
                    },
                    "apikeys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "API密钥列表（如包含******则不更新）",
                    },
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
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("ai_config:u")
async def update_ai_config(request):
    from models import Config

    params = request.json
    name = params.get("name")
    url = params.get("url")
    models = params.get("models")
    apikeys = params.get("apikeys")

    if not name or not url or not models or not apikeys:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if not isinstance(models, list) or not isinstance(apikeys, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    # 查询配置
    config = await Config.get_or_none(key=name, module="ai_service", deleted=False, tenant_id=tenant_id)
    if not config:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 解析现有 value
    old_value = orjson.loads(config.value)

    # 检查 apikeys 是否包含脱敏标记
    has_masked = any("******" in key for key in apikeys)
    if has_masked:
        # 不更新 apikeys，使用旧的
        apikeys = old_value.get("apikeys", [])

    # 构造新 value
    new_value = orjson.dumps({"url": url, "models": models, "apikeys": apikeys}).decode()
    config.value = new_value
    await config.save()

    return response_ok("更新成功")


@workflow_bp.post("/ai-config/delete")
@openapi.definition(
    summary="删除AI服务配置",
    description="删除AI服务配置（软删除），需要ai_config:d权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "AI服务名称"},
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
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("ai_config:d")
async def delete_ai_config(request):
    from models import Config

    params = request.json
    name = params.get("name")

    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    # 查询配置
    config = await Config.get_or_none(key=name, module="ai_service", deleted=False, tenant_id=tenant_id)
    if not config:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 软删除
    config.deleted = True
    await config.save()

    return response_ok("删除成功")


@workflow_bp.post("/ai-config/list")
@openapi.definition(
    summary="查询AI服务配置列表",
    description="查询AI服务配置列表（一次性返回所有数据），需要ai_config:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "服务名称（模糊查询）"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "查询成功",
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
                                        "url": {"type": "string"},
                                        "models": {"type": "array", "items": {"type": "string"}},
                                        "apikeys": {"type": "array", "items": {"type": "string"}},
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
@requires_permission("ai_config:r")
async def list_ai_config(request):
    from models import Config

    params = request.json or {}
    name = params.get("name")

    tenant_id = request.ctx.session.get("tenant_id")

    # 构建查询条件
    query = Config.filter(module="ai_service", deleted=False, tenant_id=tenant_id)
    if name:
        query = query.filter(key__icontains=name)

    # 查询所有配置
    configs = await query.all()

    # 格式化返回数据
    data = []
    for config in configs:
        value = orjson.loads(config.value)
        url = value.get("url", "")
        models = value.get("models", [])
        apikeys = value.get("apikeys", [])


        data.append(
            {
                "id": config.id,
                "name": config.key,
                "url": url,
                "models": models,
                "apikeys": apikeys,
                "ctime": strip_timezone_to_seconds(config.ctime),
                "utime": strip_timezone_to_seconds(config.utime),
            }
        )

    return response_ok(data)


@workflow_bp.post("/version-runlog")
@openapi.definition(
    summary="获取剧本版本的执行记录",
    description="通过剧本版本ID获取该剧本的所有执行记录",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["flow_id"],
                "properties": {
                    "flow_id": {"type": "string", "description": "剧本版本ID"},
                    "page": {"type": "integer", "description": "页码，默认1"},
                    "size": {"type": "integer", "description": "每页大小，默认20"},
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
                                    "total": {"type": "integer", "description": "总记录数"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "run_logs": {
                                        "type": "array",
                                        "description": "执行记录列表",
                                    },
                                },
                            },
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("workflow:r")
async def get_version_runlog(request):
    flow_id = request.json.get("flow_id")
    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    page = int(request.json.get("page", 1)) if request.json.get("page") else 1
    size = int(request.json.get("size", 20)) if request.json.get("size") else 20
    offset = (page - 1) * size

    tenant_id = request.ctx.session.get("tenant_id")

    # 验证剧本版本是否存在
    pv = await PlaybookVersion.get_or_none(id=flow_id, deleted=False, tenant_id=tenant_id)
    if not pv:
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    # 检查权限
    user = request.ctx.session.get("account_info")
    has_access = await check_playbook_permission(
        request=request,
        user=user,
        playbook_id=pv.playbook_id,
        required_permissions=["r"],
    )
    if not has_access:
        raise CustomError(ErrorCode.PLAYBOOK_UNAUTHORIZED)

    # 查询总数
    total = await Runlog.filter(playbook_version_id=flow_id, deleted=False, tenant_id=tenant_id).count()

    # 查询日志
    logs = await Runlog.filter(playbook_version_id=flow_id, deleted=False, tenant_id=tenant_id).order_by("-ctime").offset(offset).limit(size).all()

    run_logs = await runlog_list(logs)

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "run_logs": run_logs,
        }
    )


