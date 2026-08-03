# coding=utf-8
import traceback

from aiocache import caches
import orjson
from sanic import Blueprint, Request, Websocket
from sanic import response
from sanic.log import logger
from sanic_ext import openapi
from tortoise.expressions import Q
from websockets import ConnectionClosed

from core.error_code import ErrorCode
from core.response import response_ok
from libs.chat import chat_manager, get_or_create_private_chat_room
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.runlog_pagination import runlog_batch_summaries, runlog_node_page
from libs.security.authorization import check_action_execute_permission, check_wildcard, requires_permission
from libs.utils import gen_random_code, gen_run_id
from models import Events, Runlog, Tool, ToolAction
from models.user import Account
from models.warroom import Message, MessageAccount, Room
from libs.utils import runlog_list
from settings import CACHE, TASK_TOPIC, WR_TOPIC

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
                "required": ["name", "room_type"],
                "properties": {
                    "name": {"type": "string", "description": "作战室名称"},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "邀请其他用户ID列表",
                    },
                    "room_type": {"type": "string", "description": "作战室类型：public、private"},
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
    room_type = request.json.get("room_type")
    members_ids = request.json.get("members", [])
    user_id = []
    tenant_id = request.ctx.session.get("tenant_id")
    # 作战室名称唯一
    if await Room.filter(name=room_name, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.ROOM_NAME_EXISTS)

    if not room_type:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if room_type == "private" and len(members_ids) > 1:
        raise CustomError(ErrorCode.PARAM_ERROR)

    creator = request.ctx.session["account_info"]
    room = await Room.create(name=room_name, creator=creator, tenant_id=tenant_id, room_type=room_type)
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

    # Hook SecOps Expert (Initialize Agent)
    if hasattr(request.app.ctx, "agent_lifecycle"):

        async def init_agent_task():
            try:
                # Build members list
                # Creator
                roles = await creator.roles.filter(tenant_id=tenant_id).all()
                perms = []
                for r in roles:
                    if r.permissions:
                        perms.extend(r.permissions)

                members = [{"id": creator.id, "display_name": creator.display_name, "role": roles[0].name if roles else "User", "permissions": perms}]

                # Invited members
                if members_ids and isinstance(members_ids, list):
                    for member_id in members_ids:
                        m = await Account.get_or_none(id=member_id)
                        if m:
                            m_roles = await m.roles.filter(tenant_id=tenant_id).all()
                            m_perms = []
                            for r in m_roles:
                                if r.permissions:
                                    m_perms.extend(r.permissions)
                            members.append({"id": m.id, "display_name": m.display_name, "role": m_roles[0].name if m_roles else "User", "permissions": m_perms})
                # 取消agent相关模块创建
                # await request.app.ctx.agent_lifecycle.on_warroom_created(
                #     warroom_id=str(room.id),
                #     tenant_id=tenant_id,
                #     event={"event_name": room_name, "event_source": "人工", "event_summary": "人工创建作战室"},
                #     creator={"id": creator.id, "display_name": creator.display_name},
                #     initial_members=members,
                # )
            except Exception as e:
                logger.error(f"Failed to init SecOps agent: {e} | {traceback.format_exc()}")

        request.app.add_task(init_agent_task())

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
    if room.room_type == "private":
        raise CustomError(ErrorCode.INVITE_USER_ERROR)
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
    if room.room_type == "private":
        raise CustomError(ErrorCode.PRIVATE_ROOM_JOIN_NOT_ALLOWED)
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
    if room.room_type == "private":
        raise CustomError(ErrorCode.EXIT_ROOM_ERROR)
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
    tenant_id = request.ctx.session.get("tenant_id")
    user = request.ctx.session["account_info"]
    condition = Q(tenant_id=tenant_id) & (
        ~Q(room_type="private") | Q(users__id=user.id)
    )
    keyword = req_json.get("keyword")
    if keyword:
        condition = condition & Q(name__icontains=keyword)

    # 前端是先获取再查询, 没有使用到这里的查询参数
    query = Room.filter(condition).distinct()
    rooms = await query.offset(offset).limit(size).order_by("-ctime").prefetch_related("creator", "users")
    total = await query.count()
    return response_ok({"total": total, "rooms": [r.json for r in rooms]})


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
    room = await Room.get(id=room_id, tenant_id=tenant_id).prefetch_related(
        "creator", "users"
    )
    user = request.ctx.session["account_info"]
    if not await room.users.filter(id=user.id).exists():
        raise CustomError(ErrorCode.ROOM_MEMBER_ACCESS_DENIED)

    user_list = []
    for user in room.users:
        # 获取用户角色（用户和角色是多对多的关系）
        tenant_ids = await user.member.all()
        if tenant_id not in [t.id for t in tenant_ids]:
            continue
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


def _account_identity(account) -> str:
    return str(getattr(account, "id", None) or getattr(account, "username", "") or "")


def _private_room_dedupe_key(room):
    if getattr(room, "room_type", None) != "private":
        return ("room", str(getattr(room, "id", "")))

    users = getattr(room, "users", None) or []
    participant_ids = sorted(_account_identity(user) for user in users if _account_identity(user))
    if not participant_ids:
        return ("room", str(getattr(room, "id", "")))
    return ("private", tuple(participant_ids))


def _dedupe_star_rooms(rooms):
    seen = set()
    result = []
    for room in rooms:
        key = _private_room_dedupe_key(room)
        if key in seen:
            continue
        seen.add(key)
        result.append(room)
    return result


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
    await ensure_default_ai_private_room(request.app.ctx.cache_client, user, tenant_id)
    rooms = await user.star_rooms.filter(tenant_id=tenant_id).all().prefetch_related("creator", "users")
    return response_ok({"star_rooms": [r.json for r in _dedupe_star_rooms(rooms)]})


async def ensure_default_ai_private_room(cache_client, user: Account, tenant_id):
    system_user = await Account.get_or_none(username="system", disabled=False)
    if not system_user or system_user.id == user.id:
        return

    room_info = await get_or_create_private_chat_room(cache_client, user, system_user, tenant_id)
    room = await Room.get_or_none(id=room_info["room_id"], tenant_id=tenant_id)
    if not room:
        return

    await user.star_rooms.add(room)
    await system_user.star_rooms.add(room)


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
        raise CustomError(ErrorCode.ROOM_NOT_JOINED)
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
    user_rooms = await query.order_by("-ctime").prefetch_related("creator", "users")

    return response_ok({"rooms": [room.json for room in user_rooms]})


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
                        "description": "announcements",
                        "enum": [
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
    compact_runlogs = bool(request.json.get("compact_runlogs"))
    tenant_id = request.ctx.session.get("tenant_id")
    # 群聊历史. 先查询后创建消息发送
    if not room_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

        # 获取公告列表
    if query_type == "announcements":
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
    condition = Q(room_id=room_id, tenant_id=tenant_id)
    if keyword:
        condition &= Q(content__contains=keyword)

    total_count = await Message.filter(condition).count()
    messages = await Message.filter(condition).order_by("-ctime").offset(offset).limit(limit).prefetch_related("sender")
    message_list = []
    run_id_list = []
    for msg in messages:
        if msg.type != "runlog":
            continue
        run_id = msg.content.split(":")[0]
        if run_id and run_id not in run_id_list:
            run_id_list.append(run_id)
    compact_summaries = await runlog_batch_summaries(Runlog, Q(tenant_id=tenant_id, deleted=False), run_id_list) if compact_runlogs else {}
    emitted_run_ids = set()
    for msg in reversed(messages):
        if msg.type == "runlog":
            run_id = msg.content.split(":")[0]
            if run_id not in emitted_run_ids:
                emitted_run_ids.add(run_id)
                if msg.is_recalled:
                    content = "[已撤回]"
                elif compact_runlogs:
                    content = {"run_id": run_id, "runlog_summary": compact_summaries.get(run_id)}
                else:
                    runlogs = await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).order_by("ctime").all()
                    content = await runlog_list(runlogs)
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


async def _require_warroom_run_scope(room_id: str, run_id: str, tenant_id: str) -> None:
    room = await Room.get_or_none(id=room_id, tenant_id=tenant_id, deleted=False)
    if not room:
        raise CustomError(ErrorCode.NOT_FOUND)
    linked = run_id in (room.run_id or [])
    if not linked:
        linked = await Message.filter(room_id=room_id, tenant_id=tenant_id, type="runlog", content__startswith=run_id).exists()
    if not linked or not await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).exists():
        raise CustomError(ErrorCode.NOT_FOUND)


@warroom_bp.post("/runlog/nodes")
@openapi.definition(summary="分页获取作战室单次执行的节点摘要")
@requires_permission("warroom:r")
async def get_warroom_runlog_nodes(request):
    data = request.json or {}
    room_id = str(data.get("room_id") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    if not room_id or not run_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    await _require_warroom_run_scope(room_id, run_id, tenant_id)
    return response_ok(
        await runlog_node_page(
            Runlog,
            Q(tenant_id=tenant_id, deleted=False),
            run_id=run_id,
            cursor=data.get("cursor"),
            limit=data.get("limit"),
            cursor_kind="warroom-node",
            cursor_scope={"room_id": room_id},
        )
    )


@warroom_bp.post("/runlog/detail")
@openapi.definition(summary="获取作战室单条运行日志详情")
@requires_permission("warroom:r")
async def get_warroom_runlog_detail(request):
    data = request.json or {}
    room_id = str(data.get("room_id") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    runlog_id = str(data.get("runlog_id") or "").strip()
    if not room_id or not run_id or not runlog_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    await _require_warroom_run_scope(room_id, run_id, tenant_id)
    runlog = await Runlog.get_or_none(id=runlog_id, run_id=run_id, tenant_id=tenant_id, deleted=False)
    if not runlog:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(runlog.json)


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
    message_list = []
    account = request.ctx.session["account_info"]
    tenant_id = request.ctx.session.get("tenant_id")
    count = await MessageAccount.filter(account_id=account.id, status=0, tenant_id=tenant_id).count()
    messages = await MessageAccount.filter(account_id=account.id, status=0, tenant_id=tenant_id).offset(offset).limit(size).all().prefetch_related("message__sender")
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
    maintenance_mode = await request.app.ctx.cache_client.get(
        request.ctx.session["id"]
    )
    if run_type == "action" and not await check_action_execute_permission(
        user,
        params.get("app_name"),
        params.get("action"),
        params.get("resource"),
        tenant_id,
        bool(maintenance_mode),
        params.get("app_ver"),
    ):
        raise CustomError(ErrorCode.UNAUTH)
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
                "required": ["run_ids"],
                "properties": {
                    "run_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "run_id列表",
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
    # 保留request.json.get("run_id")，兼容之前只传单个run_id的情况
    if not request.json.get("run_ids") and not request.json.get("run_id"):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    logs = await Runlog.filter(run_id__in=request.json.get("run_ids") if request.json.get("run_ids") else [request.json.get("run_id")], deleted=False).all()

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
    if not event:
        raise CustomError(ErrorCode.NOT_FOUND)
    run_type = TYPE_DICT.get(executorInstanceType)
    if not run_type:
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
    if run_type == "action" and not await check_action_execute_permission(
        user,
        params.get("app_name"),
        params.get("action"),
        params.get("resource"),
        tenant_id,
        bool(await request.app.ctx.cache_client.get(request.ctx.session["id"])),
        params.get("app_ver"),
    ):
        raise CustomError(ErrorCode.UNAUTH)
    if not event.room:
        room_name = event.event_name + "_" + gen_random_code(6)
        if await Room.filter(name=room_name, deleted=False, tenant_id=tenant_id).exists():
            raise CustomError(ErrorCode.ROOM_NAME_EXISTS)
        room = await Room.create(name=room_name, creator=user, tenant_id=tenant_id, room_type="public")
        event.room = room
    room_id = event.room_id
    if not room_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    run_id = gen_run_id()
    msg = {"run_type": run_type, "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    room = await Room.get_or_none(id=room_id, tenant_id=tenant_id)
    room.updator = user
    room.run_id.append(msg["run_id"])
    await room.save()
    await request.app.ctx.cache_client.set(str(msg["run_id"]), {"room_id": str(room_id), "updator": user.id, "tenant_id": tenant_id}, 3600 * 24 * 7)
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
    sender = await Account.get(username="system", type="user")
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
