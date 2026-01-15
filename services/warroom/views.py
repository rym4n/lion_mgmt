# coding=utf-8
import ast
import traceback

import orjson
from aiocache import caches
from sanic import Blueprint, Request, Websocket
from sanic import response
from sanic.log import logger
from sanic_ext import openapi
from tortoise.expressions import Q
from websockets import ConnectionClosed

from core.error_code import ErrorCode
from core.response import response_ok
from libs.chat import get_or_create_private_chat_room, chat_manager
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authentication import token_decode
from libs.security.authorization import requires_permission, check_wildcard
from libs.utils import gen_run_id, gen_random_code
from models import Events, ToolAction, Tool, Runlog
from models.user import Account
from models.warroom import Room, Message, MessageAccount
from services.timed.views import runlog_list
from settings import TASK_TOPIC, WR_TOPIC, CACHE

warroom_bp = Blueprint("warroom", url_prefix="/warroom")
__all__ = ["warroom_bp"]

TYPE_DICT = {"PLAYBOOK": "flow", "ACTION": "action"}


@warroom_bp.websocket("/ws")
@openapi.exclude()
async def warroom_ws(request: Request, ws: Websocket):
    account = None
    try:
        # 先获取认证信息
        # auth_data = await asyncio.wait_for(ws.recv(), timeout=15)
        # auth_json = orjson.loads(auth_data)
        # payload = jwt.decode(auth_json.get('auth', ''), SECRET, SESSION_ENCODING_ALGORITHM)
        # validate_token(payload, request)

        # account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
        account = await request.ctx.session.get("account_info")
        if not account:
            logger.warning("redis里已经没有account信息了")
            raise CustomError(ErrorCode.SESSION_INVALID)
        # 建立连接
        session_id = request.ctx.session.get("id")
        tenant_id = request.ctx.session.get("tenant_id")
        await chat_manager.connect(account, ws, session_id)
        has_perm = False
        if not has_perm:
            for role in account.roles:
                for role_permission in role.permissions:
                    if check_wildcard(role_permission, "warroom:r"):
                        has_perm = True
        # 持续接收和处理消息
        async for msg in ws:
            if not has_perm and orjson.loads(msg).get("type") != "ping":
                await ws.send(orjson.dumps({"type": "error", "message": "无权限访问作战室"}).decode())
                continue
            try:
                message = orjson.loads(msg)
                await chat_manager.handle_message(account, ws, message, session_id, tenant_id)
            except Exception as e1:
                logger.error(f"{e1} | {traceback.format_exc()}")
    except ConnectionClosed as e2:
        logger.error(f"{e2} | {traceback.format_exc()}")
    except Exception as e:
        logger.error(f"{e} | {traceback.format_exc()}")
        await ws.send(orjson.dumps({"type": "error", "message": str(e)}).decode())
    finally:
        if account and session_id:
            await chat_manager.disconnect(account.id, session_id)
        await ws.close()


@warroom_bp.post("/create")
@openapi.definition(
    summary="新建作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "作战室名称"},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "邀请其他用户ID列表",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "room_id": {"type": "string", "example": "房间ID"},
                                    "name": {"type": "string", "example": "作战室名称"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:c")
async def create_room(request: Request):
    room_name = request.json.get("name")
    members_ids = request.json.get("members", [])
    user_id = []
    tenant_id = request.ctx.session.get("tenant_id")
    # 作战室名称唯一
    if await Room.filter(name=room_name, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.ROOM_NAME_EXISTS)

    creator = request.ctx.session["account_info"]
    room = await Room.create(name=room_name, creator=creator, tenant_id=tenant_id)
    await room.users.add(creator)
    await creator.star_rooms.add(room)
    user_id.append(creator.id)
    # 可选择邀请其他用户
    if members_ids and isinstance(members_ids, list):
        for member_id in members_ids:
            member = await Account.get(id=member_id)
            if member:
                await room.users.add(member)
                await member.star_rooms.add(room)
                user_id.append(member.id)

    await Events.create(
        event_name=room_name,
        event_process_status="待处理",
        event_source="人工",
        owner={"role": [], "user": user_id},
        creator=creator,
        updator=creator,
        room=room,
        tenant_id=tenant_id,
    )

    return response_ok({"room_id": str(room.id), "name": room.name})


@warroom_bp.post("/invite")
@openapi.definition(
    summary="邀请成员加入作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "members"],
                "properties": {
                    "id": {"type": "string", "description": "作战室ID"},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "邀请用户ID列表",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "邀请成功"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:u")
async def invite_members(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    room = await Room.get_or_none(id=request.json.get("id"), tenant_id=tenant_id)
    accounts = await Account.filter(id__in=request.json.get("members")).all()
    for account in accounts:
        if await room.users.filter(id=account.id).exists():
            continue
        await room.users.add(account)
        await account.star_rooms.add(room)

    return response_ok("邀请成功")


@warroom_bp.post("/join")
@openapi.definition(
    summary="加入指定作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "作战室ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "成功加入作战室"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:u")
async def join_room(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    room = await Room.get(id=request.json.get("id"), tenant_id=tenant_id)
    user = request.ctx.session["account_info"]
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if is_member:
        return response_ok("用户已经是该作战室成员")
    await room.users.add(user)
    return response_ok("成功加入作战室")


@warroom_bp.post("/quit")
@openapi.definition(
    summary="退出指定作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "作战室ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "成功退出作战室"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:u")
async def quit_room(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    room = await Room.get(id=request.json.get("id"), tenant_id=tenant_id)
    user = request.ctx.session["account_info"]
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if not is_member:
        return response_ok("用户并不在该作战室，无需退出")
    await room.users.remove(user)
    await user.star_rooms.remove(room)
    return response_ok("成功退出作战室")


@warroom_bp.post("/rooms_list")
@openapi.definition(
    summary="获取当前用户所有作战室列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "搜索作战室关键词"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "rooms": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "description": "作战室信息",
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def get_room_list(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    condition = Q(users__id=request.ctx.session["account_info"].id, tenant_id=tenant_id)
    keyword = request.json.get("keyword")
    if keyword:
        condition = condition & Q(name__icontains=keyword)

    # 前端是先获取再查询, 没有使用到这里的查询参数
    rooms = await Room.filter(condition).order_by("-ctime").prefetch_related("creator", "users")
    return response_ok({"rooms": [r.json for r in rooms]})


@warroom_bp.post("/all-rooms")
@openapi.definition(
    summary="获取所有作战室列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "size": {"type": "integer", "description": "页大小"},
                    "keyword": {"type": "string", "description": "搜索作战室关键词"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "rooms": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "description": "作战室信息",
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def get_all_rooms(request: Request):
    req_json = request.json or {}
    page = req_json.get("page", 1)
    size = req_json.get("size", 10)
    offset = (page - 1) * size
    limit = size
    tenant_id = request.ctx.session.get("tenant_id")
    condition = Q(tenant_id=tenant_id)
    keyword = req_json.get("keyword")
    if keyword:
        condition = condition & Q(name__icontains=keyword)

    # 前端是先获取再查询, 没有使用到这里的查询参数
    rooms = await Room.filter(condition).offset(offset).limit(limit).order_by("-ctime").prefetch_related("creator", "users")
    return response_ok({"rooms": [r.json for r in rooms]})


@warroom_bp.post("/members")
@openapi.definition(
    summary="获取指定作战室的用户列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "作战室ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "members": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                                "roles": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def acquire_members(request: Request):
    room_id = request.json.get("id")
    tenant_id = request.ctx.session.get("tenant_id")
    room = await Room.get(id=room_id, tenant_id=tenant_id).prefetch_related("creator", "users")

    user_list = []
    for user in room.users:
        # 获取用户角色（用户和角色是多对多的关系）
        user_roles = await user.roles.all()
        roles_list = [role.name for role in user_roles]
        user_list.append(
            {
                "id": str(user.id),
                "username": user.username,
                "display_name": user.display_name,
                "roles": roles_list,
            }
        )

    return response_ok(data={"members": user_list})


@warroom_bp.post("/get_private_room")
@openapi.definition(
    summary="获取或创建与指定用户的私聊作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["partner_id"],
                "properties": {"partner_id": {"type": "string", "description": "私聊对象用户ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {"room_id": {"type": "string", "example": "房间ID"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:c")
async def get_private_room(request: Request):
    user = request.ctx.session["account_info"]
    partner_id = request.json.get("partner_id")
    partner = await Account.get(id=partner_id)
    if partner is None:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
    tenant_id = request.ctx.session.get("tenant_id")
    # 先获取私聊作战室
    room_info = await get_or_create_private_chat_room(request.app.ctx.cache_client, user, partner, tenant_id)
    room = await Room.get_or_none(id=room_info["room_id"], tenant_id=tenant_id)
    await user.star_rooms.add(room)
    await partner.star_rooms.add(room)
    return response_ok(room_info)


@warroom_bp.post("/get_star_rooms")
@openapi.definition(
    summary="获取关注列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "搜索关键词"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "star_rooms": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "description": "作战室信息",
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def get_star_rooms(request: Request):
    user = request.ctx.session["account_info"]
    tenant_id = request.ctx.session.get("tenant_id")
    return response_ok({"star_rooms": [r.json for r in await user.star_rooms.filter(tenant_id=tenant_id).all().prefetch_related("creator", "users")]})


@warroom_bp.post("/star_room")
@openapi.definition(
    summary="关注指定作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "作战室ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "成功关注"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def star_room(request: Request):
    room_id = request.json.get("id")
    tenant_id = request.ctx.session.get("tenant_id")
    room = await Room.get(id=room_id, tenant_id=tenant_id)
    user = request.ctx.session["account_info"]
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if not is_member:
        return response_ok("用户并不在该作战室，无需关注")
    await user.star_rooms.add(room)
    return response_ok("成功关注")


@warroom_bp.post("/unstar_room")
@openapi.definition(
    summary="取消关注指定作战室",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "作战室ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "成功取消关注"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:u")
async def unstar_room(request: Request):
    room_id = request.json.get("id")
    tenant_id = request.ctx.session.get("tenant_id")
    room = await Room.get(id=room_id, tenant_id=tenant_id)
    user = request.ctx.session["account_info"]
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if not is_member:
        return response_ok("用户并不在该作战室，无需取消关注")
    await user.star_rooms.remove(room)
    return response_ok("成功取消关注")


@warroom_bp.post("/user_rooms")
@openapi.definition(
    summary="获取当前用户已加入的作战室列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "搜索作战室关键词"}},
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "rooms": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "creator_id": {"type": "string"},
                                                "creator_name": {"type": "string"},
                                                "ctime": {"type": "string"},
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def get_user_rooms(request: Request):
    user = request.ctx.session["account_info"]
    keyword = request.json.get("keyword", "")
    tenant_id = request.ctx.session.get("tenant_id")
    query = Room.filter(users__id=user.id, tenant_id=tenant_id)
    if keyword:
        query = query.filter(name__contains=keyword)
    user_rooms = await query.order_by("-ctime").prefetch_related("creator")

    room_list = []
    for room in user_rooms:
        room_list.append(
            {
                "id": str(room.id),
                "name": room.name,
                "creator_id": room.creator.id,
                "creator_name": room.creator.display_name,
                "ctime": str(room.ctime),
            }
        )

    return response_ok({"rooms": room_list})


@warroom_bp.post("/query_messages")
@openapi.definition(
    summary="统一消息查询接口",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["query_type"],
                "properties": {
                    "query_type": {
                        "type": "string",
                        "description": "查询类型: public_history, private_history, search_messages, announcements",
                        "enum": [
                            "public_history",
                            "private_history",
                            "search_messages",
                            "announcements",
                        ],
                    },
                    "room_id": {"type": "string", "description": "作战室ID"},
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "每页条数，默认20"},
                    "offset": {"type": "integer", "description": "偏移量，默认0"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "messages": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "description": "消息列表",
                                        },
                                    },
                                    "pagination": {
                                        "type": "object",
                                        "properties": {
                                            "total": {"type": "integer"},
                                            "offset": {"type": "integer"},
                                            "limit": {"type": "integer"},
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
@requires_permission("warroom:r")
async def query_messages(request: Request):
    query_type = request.json.get("query_type")
    room_id = request.json.get("room_id")
    keyword = request.json.get("keyword", "")
    limit = request.json.get("limit", 20)
    offset = request.json.get("offset", 0)
    tenant_id = request.ctx.session.get("tenant_id")
    # 群聊历史. 先查询后创建消息发送
    if query_type == "public_history" and keyword in ["", None]:
        if not room_id:
            raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
        total_count = await Message.filter(
            type__in=["public", "ai", "runlog", "execute", "ai-question"],
            room_id=room_id,
            tenant_id=tenant_id,
        ).count()
        messages = (
            await Message.filter(
                type__in=["public", "ai", "runlog", "execute", "ai-question"],
                room_id=room_id,
                tenant_id=tenant_id,
            )
            .order_by("-ctime")
            .offset(offset)
            .limit(limit)
            .prefetch_related("sender")
        )
        message_list = []
        run_id_list = []
        for msg in reversed(messages):
            if msg.type == "runlog":
                run_id = msg.content.split(":")[0]
                if run_id not in run_id_list:
                    run_id_list.append(run_id)
                    runlogs = await Runlog.filter(run_id=run_id, deleted=False).order_by("ctime").all()
                    run_logs = await runlog_list(runlogs)
                    content = "[已撤回]" if msg.is_recalled else run_logs
                else:
                    continue
            else:
                content = "[已撤回]" if msg.is_recalled else msg.content
            message_list.append(
                {
                    "type": msg.type,
                    "message_id": str(msg.id),
                    "sender_id": str(msg.sender.id),
                    "sender_name": msg.sender.username,
                    "sender_display_name": msg.sender.display_name,
                    "content": content,
                    "image_content": msg.image_content,
                    "timestamp": str(msg.ctime),
                    "room_id": room_id,
                    "is_recalled": msg.is_recalled,
                }
            )
        return response_ok(
            {
                "messages": message_list,
                "pagination": {"total": total_count, "offset": offset, "limit": limit},
            }
        )

    # 私聊历史
    elif query_type == "private_history" and keyword in ["", None]:
        # 消息计数
        total_count = await Message.filter(
            type__in=["private", "ai", "runlog", "execute", "ai-question"],
            room_id=room_id,
            tenant_id=tenant_id,
        ).count()
        # 分页处理
        messages = (
            await Message.filter(
                type__in=["private", "ai", "runlog", "execute", "ai-question"],
                room_id=room_id,
                tenant_id=tenant_id,
            )
            .order_by("-ctime")
            .offset(offset)
            .limit(limit)
            .prefetch_related("sender", "receiver")
        )
        message_list = []
        run_id_list = []
        for msg in reversed(messages):
            if msg.type == "runlog":
                run_id = msg.content.split(":")[0]
                if run_id not in run_id_list:
                    run_id_list.append(run_id)
                    runlogs = await Runlog.filter(run_id=run_id, deleted=False).order_by("ctime").all()
                    run_logs = await runlog_list(runlogs)
                    content = "[已撤回]" if msg.is_recalled else run_logs
                else:
                    continue
            else:
                content = "[已撤回]" if msg.is_recalled else msg.content
            message_list.append(
                {
                    "type": msg.type,
                    "message_id": msg.id,
                    "sender_id": msg.sender.id,
                    "sender_name": msg.sender.username,
                    "sender_display_name": msg.sender.display_name,
                    "receiver_id": msg.receiver.id,
                    "receiver_name": msg.receiver.username,
                    "receiver_display_name": msg.receiver.display_name,
                    "content": content,
                    "image_content": msg.image_content,
                    "timestamp": str(msg.ctime),
                    "is_recalled": msg.is_recalled,
                    "room_id": room_id,
                }
            )

        return response_ok(
            {
                "messages": message_list,
                "pagination": {"total": total_count, "offset": offset, "limit": limit},
            }
        )

    # 搜索消息
    elif keyword:
        if query_type == "public_history":
            query = Message.filter(
                type="public",
                room_id=room_id,
                content__contains=keyword,
                is_recalled=False,
                tenant_id=tenant_id,
            )
        # 传入私聊用户ID, 搜索私聊历史记录
        elif query_type == "private_history":
            query = Message.filter(
                type="private",
                room_id=room_id,
                content__contains=keyword,
                is_recalled=False,
                tenant_id=tenant_id,
            )
        else:
            raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

        total_count = await query.count()
        messages = await query.order_by("-ctime").offset(offset).limit(limit).prefetch_related("sender", "receiver")
        message_list = []
        for msg in messages:
            message_list.append(
                {
                    "message_id": str(msg.id),
                    "type": msg.type,
                    "content": "[已撤回]" if msg.is_recalled else msg.content,
                    "image_content": msg.image_content,
                    "timestamp": str(msg.ctime),
                    "sender_id": msg.sender.id,
                    "sender_name": msg.sender.username,
                    "sender_display_name": msg.sender.display_name,
                    "receiver_id": msg.receiver.id,
                    "receiver_name": msg.receiver.username,
                    "receiver_display_name": msg.receiver.display_name,
                    "is_recalled": msg.is_recalled,
                    "room_id": msg.room_id,
                }
            )

        return response_ok(
            {
                "messages": message_list,
                "pagination": {"total": total_count, "offset": offset, "limit": limit},
            }
        )

    # 获取公告列表
    elif query_type == "announcements":
        if not room_id:
            raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

        total_count = await Message.filter(type="announcement", room_id=room_id, tenant_id=tenant_id).count()

        announcements = await Message.filter(type="announcement", room_id=room_id, tenant_id=tenant_id).order_by("-ctime").offset(offset).limit(limit).prefetch_related("sender")

        announcement_list = []
        for msg in announcements:
            announcement_list.append(
                {
                    "id": str(msg.id),
                    "content": msg.content,
                    "image_content": msg.image_content,
                    "timestamp": str(msg.ctime),
                    "sender_name": msg.sender.username,
                    "sender_display_name": msg.sender.display_name,
                    "sender_id": msg.sender.id,
                    "room_id": room_id,
                }
            )

        return response_ok(
            {
                "announcements": announcement_list,
                "pagination": {"total": total_count, "offset": offset, "limit": limit},
            }
        )
    else:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)


@warroom_bp.post("/get/list")
@openapi.definition(
    summary="获取消息列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码, 默认1"},
                    "size": {"type": "integer", "description": "每页大小, 默认20"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message_list": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "description": "消息列表",
                                        },
                                    },
                                    "maintenance_mode": {"type": "boolean"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def get_list(request):
    req_json = request.json or {}
    page = req_json.get("page", 1)
    size = req_json.get("size", 20)
    offset = (page - 1) * size
    limit = size
    message_list = []
    account = request.ctx.session["account_info"]
    tenant_id = request.ctx.session.get("tenant_id")
    count = await MessageAccount.filter(account_id=account.id, status=0, tenant_id=tenant_id).count()
    messages = await MessageAccount.filter(account_id=account.id, status=0, tenant_id=tenant_id).offset(offset).limit(limit).all().prefetch_related("message__sender")
    for message in messages:
        message_list.append(message.json)
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    if maintenance_mode:
        return response_ok({"message_list": message_list, "maintenance_mode": True, "count": count})
    return response_ok({"message_list": message_list, "maintenance_mode": False, "count": count})


@warroom_bp.post("/up/status")
@openapi.definition(
    summary="更新消息阅读状态",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["ids"],
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "未读消息ID列表",
                    }
                },
            }
        }
    },
    response={
        "200": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "integer", "example": 0},
                        "data": {"type": "boolean", "example": True},
                    },
                }
            }
        }
    },
)
@requires_permission("warroom:u")
async def up_status(request):
    ids = request.json.get("ids")
    if not ids:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    if "*" in ids:
        account = request.ctx.session["account_info"]
        await MessageAccount.filter(account_id=account.id, status=0, tenant_id=tenant_id).update(status=1)
        return response_ok(True)
    await MessageAccount.filter(id__in=ids, tenant_id=tenant_id).update(status=1)
    return response_ok(True)


@warroom_bp.post("/run")
@openapi.definition(
    summary="运行剧本或动作，需要作战室查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "作战室ID",
                        "example": "作战室123",
                    },
                    "type": {
                        "type": "string",
                        "description": "运行类型：flow/action",
                        "enum": ["flow", "action"],
                        "example": "flow",
                    },
                    "playbook_input": {
                        "type": "object",
                        "description": "剧本输入参数（当type为flow时必填）",
                        "properties": {
                            "flow_id": {
                                "type": "string",
                                "description": "剧本版本ID",
                                "example": "73ecfba0-1aa4-4128-b99a-b97fb08a14be",
                            },
                            "flow_input": {
                                "type": "object",
                                "description": "剧本输入参数",
                            },
                        },
                    },
                    "action_input": {
                        "type": "object",
                        "description": "动作输入参数（当type为action时必填）",
                        "properties": {
                            "app_name": {
                                "type": "string",
                                "description": "应用名称，英文标识",
                            },
                            "name": {"type": "string", "description": "应用中文名称"},
                            "app_ver": {"type": "string", "description": "应用版本号"},
                            "main": {"type": "string", "description": "主程序文件名"},
                            "action_input": {
                                "type": "object",
                                "description": "动作输入参数",
                            },
                            "resource": {
                                "type": "string",
                                "description": "资源ID，多个时用逗号分割",
                                "example": "11111,22222,33333",
                            },
                            "node_name": {"type": "string", "description": "节点名称"},
                            "action": {
                                "type": "string",
                                "description": "动作名称，英文标识",
                            },
                            "action_name": {
                                "type": "string",
                                "description": "动作名称，中文名称",
                            },
                            "retry_times": {
                                "type": "integer",
                                "description": "重试次数",
                                "default": 1,
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "超时时间，单位秒",
                                "default": 300,
                            },
                        },
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "运行成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "运行成功"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def run(request):
    room_id = request.json.get("id")
    run_type = request.json.get("type")
    user = request.ctx.session["account_info"]
    if not room_id or not run_type:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    params = request.json.get("playbook_input") or request.json.get("action_input")
    if not params:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    params["executor"] = user.id
    msg = {"run_type": run_type, "run_id": gen_run_id(), "run_args": params, "tenant_id": tenant_id}
    room = await Room.get_or_none(id=room_id, tenant_id=tenant_id)
    room.updator = user
    room.run_id.append(msg["run_id"])
    await room.save()
    await request.app.ctx.cache_client.set(str(msg["run_id"]), {"room_id": str(room_id), "updator": user.id, "tenant_id": tenant_id}, 3600 * 24 * 7)
    await aproduce(TASK_TOPIC, msg)

    return response_ok({"run_id": str(msg["run_id"])})


@warroom_bp.post("/runlog")
@openapi.definition(
    summary="获取运行日志",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["run_id"],
                "properties": {
                    "run_id": {"type": "string", "description": "请求run接口返回的ID"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "运行成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "list",
                                "example": """[
                                    [
                                        [{....}]
                                    ], 
                                    [
                                        [{....}, {....}], 
                                        [{....}]
                                    ]
                                ]""",
                                "description": "返回的runlog3层list含义，最外层对应的执行次数，中间对应的执行的不同节点，最里面对应不同资源的执行结果",
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def get_runlog(request):
    if not request.json.get("run_id"):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    logs = await Runlog.filter(run_id=request.json.get("run_id"), deleted=False).all()

    run_logs = await runlog_list(logs)

    return response.json({"code": 0, "data": run_logs})


@warroom_bp.post("/execution")
@openapi.exclude()
@requires_permission("warroom:r")
async def execution(request):
    eventId = request.json.get("eventId")
    executorInstanceType = request.json.get("executorInstanceType")
    executorInstanceId = request.json.get("executorInstanceId")
    executorActionAssetIds = request.json.get("executorActionAssetIds")
    executorParams = request.json.get("params")
    user = request.ctx.session["account_info"]

    tenant_id = request.ctx.session.get("tenant_id")

    event = await Events.get_or_none(id=eventId, tenant_id=tenant_id)
    if not event.room:
        room_name = event.event_name + "_" + gen_random_code(6)
        if await Room.filter(name=room_name, deleted=False, tenant_id=tenant_id).exists():
            raise CustomError(ErrorCode.ROOM_NAME_EXISTS)
        room = await Room.create(name=room_name, creator=user, tenant_id=tenant_id)
        event.room = room
    room_id = event.room_id
    run_type = TYPE_DICT.get(executorInstanceType)
    if not room_id or not run_type:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    params = {}
    input_data = {}
    if run_type == "flow":
        if executorParams:
            for param in executorParams:
                input_data[param["key"]] = param["value"]
        params["flow_id"] = executorInstanceId
        params["flow_input"] = input_data
    elif run_type == "action":
        action = await ToolAction.get_or_none(id=executorInstanceId, deleted=False, tenant_id=tenant_id)
        if not action:
            raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)
        tool = await Tool.get_or_none(name=action.tool_name, version=action.version_number, deleted=False, tenant_id=tenant_id)
        if executorParams:
            for param in executorParams:
                input_data[param["key"]] = param["value"]
        params["app_name"] = tool.name
        params["name"] = tool.description
        params["app_ver"] = tool.version
        params["main"] = tool.main
        params["action_input"] = input_data
        params["node_name"] = tool.description
        params["action"] = action.action["func"]
        params["action_name"] = action.action["name"]
        params["retry_times"] = 0
        params["timeout"] = 300
        params["resource"] = ""
    params["executor"] = user.id
    if executorActionAssetIds and run_type == "action":
        params["resource"] = executorActionAssetIds
    run_id = gen_run_id()
    msg = {"run_type": run_type, "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    room = await Room.get_or_none(id=room_id, tenant_id=tenant_id)
    room.updator = user
    room.run_id.append(msg["run_id"])
    await room.save()
    await request.app.ctx.cache_client.set(str(msg["run_id"]), {"room_id": str(room_id), "updator": user.id}, 3600 * 24 * 7)
    await aproduce(TASK_TOPIC, msg)

    return response.json({"code": 0, "data": {"activityId": str(run_id)}, "result": str(run_id)})


@warroom_bp.get("/activity/<activityId>")
@openapi.exclude()
@requires_permission("warroom:r")
async def activity(request, activityId):
    if not activityId:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    logs = await Runlog.filter(run_id=activityId, deleted=False).all()
    result = {}
    if logs[0].playbook_name != "执行动作":
        executorInstanceId = logs[0].playbook_version_id
        executorInstanceName = logs[0].playbook_name
        executorInstanceType = "PLAYBOOK"
    else:
        actions = await ToolAction.filter(tool_name=logs[0].tool_name, version_number=logs[0].tool_version, tenant_id=tenant_id).all()
        for action in actions:
            if action.action["func"] == logs[0].action_name and action.action["name"] == logs[0].action_display_name:
                executorInstanceId = action.id
                executorInstanceName = logs[0].action_display_name
                executorInstanceType = "ACTION"
                break
    result["id"] = logs[0].id
    result["activityId"] = logs[0].run_id
    result["eventId"] = logs[0].event_id
    result["executorInstanceId"] = executorInstanceId
    result["executorInstanceName"] = executorInstanceName
    result["executorInstanceType"] = executorInstanceType
    result["execMode"] = None
    result["postStatus"] = None
    result["postCode"] = None
    result["postMsg"] = None
    status = []
    for log in logs:
        status.append(log.status)
    if 1 in status:
        result["excuteStatus"] = "RUNNING"
    elif 2 in status:
        result["excuteStatus"] = "WAITING_APPROVE"
    elif -1 in status:
        result["excuteStatus"] = "FAIL"
    else:
        result["excuteStatus"] = "SUCCESS"

    return response.json({"code": 0, "data": [log.json for log in logs], "result": result})


@warroom_bp.post("/send")
@openapi.definition(
    summary="给作战室发送消息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username", "msg"],
                "properties": {
                    "type": {"type": "string", "description": "消息类型"},
                    "content": {"type": "string", "description": "消息内容"},
                    "image_content": {"type": "string", "description": "图片内容"},
                    "room_id": {"type": "string", "description": "作战室ID"},
                    "username": {"type": "string", "description": "用户名"},
                    "msg": {"type": "string", "description": "消息内容"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "发送成功"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("warroom:r")
async def send_message(request):
    username = request.json.get("username")
    partner = await Account.get(username=username)
    sender = await Account.get(username="system", type="api")
    if partner is None:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
    tenant_id = request.ctx.session.get("tenant_id")
    room_info = await get_or_create_private_chat_room(request.app.ctx.cache_client, sender, partner, tenant_id)
    room = await Room.get_or_none(id=room_info["room_id"], tenant_id=tenant_id)
    await sender.star_rooms.add(room)
    await partner.star_rooms.add(room)

    data = {
        "type": "public",
        "content": request.json.get("msg"),
        "image_content": "",
        "room_id": room.id,
    }

    content = data.get("content", "")
    image_content = data.get("image_content", "")

    message = await Message.create(type="private", sender=sender, room=room, content=content, image_content=image_content, receiver=partner, tenant_id=tenant_id)

    await MessageAccount.create(message=message, account_id=partner.id, tenant_id=tenant_id)

    message_data = {
        "type": "private",
        "message_id": str(message.id),
        "sender_id": sender.id,
        "sender_name": sender.username,
        "sender_display_name": sender.display_name,
        "content": content,
        "image_content": image_content,
        "timestamp": str(message.ctime),
        "room_id": room.id,
    }
    caches.set_config(CACHE)
    await caches.get("redis_chat").client.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    return response_ok("发送成功")
