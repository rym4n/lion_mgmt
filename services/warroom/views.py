# coding=utf-8
import traceback
import json
import asyncio
from libs.ws_connect import ConnectionManager, get_or_create_private_chat_room
from websockets import ConnectionClosed
from sanic import Blueprint, Request, Websocket
from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from sanic.log import logger
from models.warroom import Room, Message
from models.user import Account
from tortoise.expressions import Q


warroom_bp = Blueprint('warroom', url_prefix='v1/warroom')
__all__ = ['warroom_bp']

manager = ConnectionManager()

@warroom_bp.websocket('/ws')
async def warroom_ws(request: Request, ws: Websocket):
    """WebSocket连接处理"""
    user_id = None
    try:
        # 先获取认证信息
        auth_data = await asyncio.wait_for(ws.recv(), timeout=5.0)
        auth_json = json.loads(auth_data)
        user_id = auth_json['request_header']['id']
        # user_id = '43782755-6382-4f42-82a2-5e557370d858'
        # 建立连接
        await manager.connect(ws, user_id)
        # 持续接收和处理消息
        while True:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=60.0)
                message = json.loads(data)
                await manager.handle_message(user_id, message)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "heartbeat", "status": "ping"}))
            except asyncio.CancelledError:
                break
            except Exception as e1:
                logger.error(f"{e1} | {traceback.format_exc()}")
    except Exception as e2:
        logger.error(f"{e2} | {traceback.format_exc()}")
    finally:
        if user_id is not None:
            await manager.disconnect(user_id)
        await ws.close(code=1011, reason="服务器错误")


@warroom_bp.after_server_start
async def start_heartbeat_check(_app, _loop):
    """启动服务后开始心跳检查"""
    asyncio.create_task(manager.check_heartbeats())


@warroom_bp.post("/create")
@requires_permission('warroom:c')
async def create_room(request: Request):
    """
    新建作战室

    请求参数：
        name: 必填， 房间名称
        members: 选填，邀请其他用户ID列表
    """
    room_name = request.json.get('name')
    members_ids = request.json.get('members', [])

    # 作战室名称唯一
    if await Room.filter(name=room_name).exists():
        return CustomError(ErrorCode.RoomNameExists)
    creator = request.ctx.session['account_info']
    room = await Room.create(name=room_name, creator=creator)
    await room.users.add(creator)
    # 可选择邀请其他用户
    if members_ids and isinstance(members_ids, list):
        for member_id in members_ids:
            member = await Account.get(id=member_id)
            if member:
                await room.users.add(member)

    return response_ok({
        "room_id": str(room.id),
        "name": room.name
    })


@warroom_bp.post("/join")
@requires_permission('warroom:c')
async def join_room(request: Request):
    """
    加入指定作战室

    请求参数：
        id: 必填，房间id
    """
    room = await Room.get(id=request.json.get('id'))
    user = request.ctx.session['account_info']
    # 检查当前用户是否已加入该房间
    is_member = await room.users.filter(id=user.id).exists()
    if is_member:
        return response_ok("用户已经是该作战室成员")
    await room.users.add(user)
    return response_ok("成功加入作战室")


@warroom_bp.post("/delete")
@requires_permission('warroom:d')
async def delete_room(request: Request):
    """
    删除指定作战室

    请求参数：
        id: 必填， 房间id
    """
    room_id = request.json.get("id")
    room = await Room.get(id=room_id)
    if room is None:
        return CustomError(ErrorCode.RoomNotExists)
    user = request.ctx.session['account_info']
    creator = await room.creator
    if str(creator.id) != str(user.id):
        return CustomError(ErrorCode.RoomDeleteNotAllowed)
    await Room.filter(id=room_id).update(deleted=True)
    return response_ok("房间删除成功")


@warroom_bp.post("/rooms_list")
@requires_permission('warroom:r')
async def get_roomsList(request: Request):
    """
        获取所有作战室列表

        请求参数：
            keyword: 可选，搜索作战室
    """
    keyword = request.json.get('keyword', '')
    query = Room.filter(deleted=False)

    if keyword:
        query = query.filter(name__contains=keyword)

    rooms = await query.order_by('-ctime').select_related('creator')

    room_list = []
    for room in rooms:
        room_list.append({
            "id": str(room.id),
            "name": room.name,
            "creator_id": str(room.creator.id) if room.creator else "",
            "creator_name": room.creator.username if room.creator else "",
            "ctime": str(room.ctime),
        })
    return response_ok({
        "rooms": room_list
    })


@warroom_bp.post("/members")
@requires_permission('warroom:r')
async def acquire_members(request: Request):
    """
    获取指定作战室的用户列表

    请求参数：
        id: 必填，房间id
        keyword: 可选，搜索用户
    """
    room_id = request.json.get('id')
    keyword = request.json.get('keyword', '')

    room = await Room.get(id=room_id)
    if room is None:
        raise CustomError(ErrorCode.RoomNotExists)

    query = room.users.all()
    if keyword:
        query = query.filter(Q(username__contains=keyword))

    room_users = await query
    user_list = []
    for user in room_users:
        # 角色待修改
        is_admin = any('*' in (role.permissions or []) for role in await user.roles.all())
        user_list.append({
            "id": str(user.id),
            "username": user.username,
            "is_admin": is_admin
        })

    return response_ok(data={
        "members": user_list
    })


@warroom_bp.post("/get_private_room")
@requires_permission('warroom:r')
async def get_private_room(request: Request):
    """
    获取或创建与指定用户的私聊房间

    请求参数：
        partner_id: 必填，私聊对象用户ID
    """
    user = request.ctx.session['account_info']
    partner_id = request.json.get('partner_id')

    chat_room = await get_or_create_private_chat_room(str(user.id), partner_id)

    # 缓存房间ID到连接管理器
    if str(user.id) < partner_id:
        manager.private_chat_rooms[(str(user.id), partner_id)] = str(chat_room.id)
    else:
        manager.private_chat_rooms[(partner_id, str(user.id))] = str(chat_room.id)

    return response_ok({
        "room_id": str(chat_room.id),
        "name": chat_room.name
    })


@warroom_bp.post("/user_rooms")
@requires_permission('warroom:r')
async def get_user_rooms(request: Request):
    """
    获取当前用户已加入的作战室列表

    请求参数：
        keyword: 可选，搜索作战室
    """
    user = request.ctx.session['account_info']
    keyword = request.json.get('keyword', '')

    query = Room.filter(users__id=user.id, deleted=False)
    if keyword:
        query = query.filter(name__contains=keyword)
    user_rooms = await query.order_by('-ctime')
    
    room_list = []
    for room in user_rooms:
        creator = await room.creator
        room_list.append({
            "id": str(room.id),
            "name": room.name,
            "creator_id": str(creator.id) if creator else "",
            "creator_name": creator.username if creator else "",
            "ctime": str(room.ctime),
        })
    
    return response_ok({
        "rooms": room_list
    })


@warroom_bp.post("/query_messages")
@requires_permission('warroom:r')
async def query_messages(request: Request):
    """
    统一消息查询接口，整合了原WebSocket的多个查询功能
    请求参数：
        query_type: 必填，查询类型：
            "public_history" - 获取公共聊天历史
            "private_history" - 获取私聊历史
            "search_messages" - 搜索消息
            "announcements" - 获取公告列表
        room_id: 除了私聊以外必填，房间ID
        partner_id: 私聊必填，私聊对象ID
        keyword: 搜索必填，搜索关键词
        limit: 可选，每页条数，默认20
        offset: 可选，偏移量，默认0
    """
    user = request.ctx.session['account_info']
    user_id = str(user.id)
    query_type = request.json.get("query_type")
    room_id = request.json.get("room_id")
    partner_id = request.json.get("partner_id")
    keyword = request.json.get("keyword", "")
    limit = request.json.get("limit", 20)
    offset = request.json.get("offset", 0)
    
    if not query_type:
        return CustomError(ErrorCode.ParamValueError)

    # 群聊历史
    if query_type == "public_history":
        if not room_id:
            return CustomError(ErrorCode.ParamValueError)
        total_count = await Message.filter(type="public", room_id=room_id).count()
        messages = await Message.filter(
            type="public",
            room_id=room_id
        ).order_by("-ctime").offset(offset).limit(limit)
        message_list = []
        for msg in reversed(messages):
            sender = await msg.sender
            message_list.append({
                "type": "public",
                "message_id": str(msg.id),
                "sender_id": str(sender.id),
                "sender_name": sender.username,
                "content": "[已撤回]" if msg.is_recalled else msg.content,
                "image_content": msg.image_content,
                "timestamp": str(msg.ctime),
                "room_id": room_id,
                "is_recalled": msg.is_recalled
            })
        return response_ok({
            "messages": message_list,
            "pagination": {
                "total": total_count,
                "offset": offset,
                "limit": limit
            }
        })

    # 私聊历史
    elif query_type == "private_history":
        if not partner_id:
            return CustomError(ErrorCode.ParamValueError)

        chat_room = await get_or_create_private_chat_room(user_id, partner_id)
        total_count = await Message.filter(
            Q(Q(sender_id=user_id) & Q(receiver_id=partner_id)) |
            Q(Q(sender_id=partner_id) & Q(receiver_id=user_id)),
            type="private",
            room_id=chat_room.id
        ).count()

        messages = await Message.filter(
            Q(Q(sender_id=user_id) & Q(receiver_id=partner_id)) |
            Q(Q(sender_id=partner_id) & Q(receiver_id=user_id)),
            type="private",
            room_id=chat_room.id
        ).order_by("-ctime").offset(offset).limit(limit)

        partner = await Account.get(id=partner_id)
        message_list = []
        if partner:
            for msg in reversed(messages):
                sender = await msg.sender
                message_list.append({
                    "type": "private",
                    "message_id": str(msg.id),
                    "sender_id": str(sender.id),
                    "sender_name": sender.username,
                    "receiver_id": str(partner.id),
                    "receiver_name": partner.username,
                    "content": "[已撤回]" if msg.is_recalled else msg.content,
                    "image_content": msg.image_content,
                    "timestamp": str(msg.ctime),
                    "is_recalled": msg.is_recalled,
                    "room_id": str(chat_room.id)
                })

        return response_ok({
            "messages": message_list,
            "pagination": {
                "total": total_count,
                "offset": offset,
                "limit": limit
            }
        })

    # 搜索消息
    elif query_type == "search_messages":
        if not keyword:
            return CustomError(ErrorCode.ParamValueError)

        if room_id:
            query = Message.filter(
                type="public",
                room_id=room_id,
                content__contains=keyword,
                is_recalled=False,
            )

        elif partner_id:
            chat_room = await get_or_create_private_chat_room(user_id, partner_id)
            query = Message.filter(
                Q(Q(sender_id=user_id) & Q(receiver_id=partner_id)) |
                Q(Q(sender_id=partner_id) & Q(receiver_id=user_id)),
                type="private",
                room_id=chat_room.id,
                content__contains=keyword,
                is_recalled=False,
            )
        else:
            return CustomError(ErrorCode.ParamValueError)

        total_count = await query.count()
        messages = await query.order_by("-ctime").offset(offset).limit(limit)
        message_list = []
        for msg in messages:
            sender = await msg.sender
            receiver = await msg.receiver
            message_list.append({
                "message_id": str(msg.id),
                "type": msg.type,
                "content": "[已撤回]" if msg.is_recalled else msg.content,
                "image_content": msg.image_content,
                "timestamp": str(msg.ctime),
                "sender_id": str(sender.id),
                "sender_name": sender.username,
                "receiver_id": str(receiver.id),
                "receiver_name": receiver.username,
                "is_recalled": msg.is_recalled,
                "room_id": str(msg.room)
            })

        return response_ok({
            "messages": message_list,
            "pagination": {
                "total": total_count,
                "offset": offset,
                "limit": limit
            }
        })

    # 获取公告列表
    elif query_type == "announcements":
        if not room_id:
            return CustomError(ErrorCode.ParamValueError)

        total_count = await Message.filter(
            type="announcement",
            room_id=room_id
        ).count()

        announcements = await Message.filter(
            type="announcement",
            room_id=room_id
        ).order_by("-ctime").offset(offset).limit(limit)

        announcement_list = []
        for msg in announcements:
            sender = await msg.sender
            announcement_list.append({
                "id": str(msg.id),
                "content": msg.content,
                "timestamp": str(msg.ctime),
                "sender_name": sender.username if sender else "",
                "sender_id": str(sender.id) if sender else "0",
                "room_id": room_id
            })

        return response_ok({
            "announcements": announcement_list,
            "pagination": {
                "total": total_count,
                "offset": offset,
                "limit": limit
            }
        })
