from aiocache import caches
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Scene, Playbook

scene_bp = Blueprint("scene", url_prefix="/scene")
_all__ = ["scene_bp"]


@scene_bp.post("/add")
@openapi.definition(
    summary="添加场景",
    description="添加一个新的场景，需要场景创建权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "remark", "status"],
                "properties": {
                    "name": {"type": "string", "description": "场景名称"},
                    "remark": {"type": "string", "description": "场景描述"},
                    "status": {
                        "type": "integer",
                        "description": "场景状态 0-禁用 1-启用",
                    },
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
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "remark": {"type": "string"},
                                    "status": {"type": "integer"},
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
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("scene:c")
async def add_scene(request):
    name = request.json.get("name")
    remark = request.json.get("remark")
    status = int(request.json.get("status"))
    if not all([name, remark]):
        raise CustomError(ErrorCode.ParameterError)

    if status is None and status not in [0, 1]:
        raise CustomError(ErrorCode.ParameterError)

    if await Scene.filter(name=request.json.get("name")).exists():
        raise CustomError(ErrorCode.SceneExistsError)
    if status == 1:
        await Scene.filter(status=1).update(status=0)
    scene = await Scene.create(
        name=name,
        remark=remark,
        status=status,
        creator=request.ctx.session["account_info"],
        updator=request.ctx.session["account_info"],
    )

    return response_ok(scene.json)


@scene_bp.post("/update")
@openapi.definition(
    summary="更新场景",
    description="更新指定场景信息，需要场景更新权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "场景id"},
                    "remark": {"type": "string", "description": "场景描述"},
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
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "remark": {"type": "string"},
                                    "status": {"type": "integer"},
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
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("scene:u")
async def update_scene(request):
    scene_id = request.json.get("id")
    remark = request.json.get("remark") or None
    if not scene_id:
        raise CustomError(ErrorCode.ParameterError)

    scene = await Scene.get_or_none(id=scene_id).prefetch_related("creator")
    if not scene:
        raise CustomError(ErrorCode.NotFound)

    if remark:
        scene.remark = remark
    scene.updator = request.ctx.session["account_info"]
    await scene.save()

    return response_ok(scene.json)


@scene_bp.post("/all")
@openapi.definition(
    summary="获取所有场景",
    description="获取所有场景列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码，默认为1"},
                    "page_size": {
                        "type": "integer",
                        "description": "每页数量，默认为10",
                    },
                    "key": {"type": "string", "description": "搜索关键字"},
                    "status": {
                        "type": "integer",
                        "description": "场景状态 0-禁用 1-启用",
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
                                    "scenes": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "name": {"type": "string"},
                                                "remark": {"type": "string"},
                                                "status": {"type": "integer"},
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
                                            },
                                        },
                                    },
                                    "page": {"type": "integer"},
                                    "page_size": {"type": "integer"},
                                    "total": {"type": "integer"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
async def get_all_scene(request):
    page = int(request.json.get("page") or 1)
    page_size = int(request.json.get("page_size") or 10)
    key = request.json.get("key") or None
    status = request.json.get("status")

    condition = Q()

    if status is not None:
        condition = condition & Q(status__not=status)

    if key:
        condition = condition & (Q(name__icontains=key) | Q(remark__icontains=key))

    total = await Scene.filter(condition).count()
    scenes = list()
    if total > 0:
        scenes = await Scene.filter(condition).limit(page_size).offset((page - 1) * page_size).all().prefetch_related("creator", "updator")

    return response_ok(
        {
            "scenes": [s.json for s in scenes],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


@scene_bp.post("/current")
@openapi.definition(
    summary="设置当前场景",
    description="设置指定场景为当前场景，需要场景更新权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "场景id"}},
            }
        }
    },
    response={
        "200": {
            "description": "设置成功",
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
@requires_permission("scene:u")
async def set_current_scene(request):
    scene_id = request.json.get("id")
    if not scene_id:
        raise CustomError(ErrorCode.ParameterError)
    await Scene.filter(status=1).update(status=0)
    scene = await Scene.get_or_none(id=scene_id)
    if not scene:
        raise CustomError(ErrorCode.NotFound)

    scene.status = 1
    scene.updator = request.ctx.session["account_info"]
    await scene.save()

    await caches.get("redis_chat").set("aioe_current_scene", scene.id)

    return response_ok("设置成功")


@scene_bp.post("/remove")
@openapi.definition(
    summary="删除场景",
    description="删除指定场景，需要场景删除权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "场景id"}},
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
@requires_permission("scene:d")
async def remove_scene(request):
    scene_id = request.json.get("id")
    if not scene_id:
        raise CustomError(ErrorCode.ParameterError)

    scene = await Scene.get_or_none(id=scene_id)

    if not scene:
        raise CustomError(ErrorCode.NotFound)

    if scene.status == 1:
        raise CustomError(ErrorCode.SceneDeleteNotAllowed)

    async with in_transaction():
        playbooks = await Playbook.filter(scenes__id=id).all()

        for playbook in playbooks:
            await playbook.scenes.remove(scene)

        scene.deleted = True
        await scene.save()

    return response_ok("删除成功")
