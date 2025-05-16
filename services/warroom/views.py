# coding=utf-8
import asyncio
import traceback

import jwt
import orjson
from sanic import Blueprint, Request, Websocket
from sanic.log import logger
from tortoise.expressions import Q
from websockets import ConnectionClosed

from core.error_code import ErrorCode
from core.response import response_ok
from libs.chat import ChatManager, get_or_create_private_chat_room
from libs.error_code.exception import CustomError
from libs.security.authentication import validate_token
from libs.security.authorization import requires_permission
from models.user import Account
from models.warroom import Room, Message
from settings import SECRET, SESSION_ENCODING_ALGORITHM

warroom_bp = Blueprint('warroom', url_prefix='v1/warroom')
__all__ = ['warroom_bp']


@warroom_bp.websocket('/ws')
async def warroom_ws(request: Request, ws: Websocket):
    """WebSocket连接处理"""
    user_id = None
    manager = None
    try:
        # 先获取认证信息
        auth_data = await asyncio.wait_for(ws.recv(), timeout=5.0)
        auth_json = orjson.loads(auth_data)
        payload = jwt.decode(auth_json['auth'], SECRET, SESSION_ENCODING_ALGORITHM)
        validate_token(payload, request)

        account = await request.ctx.cache_client.get(f"account_{payload['sub']}")
        if not account:
            logger.warning("redis里已经没有account信息了")
            raise CustomError(ErrorCode.SessionInvalid)
        user_id = account.id

        manager = ChatManager(account, ws, request.ctx.cache_client)
        # 建立连接
        await manager.connect()
        # 持续接收和处理消息
        while True:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=60.0)
                if not data:
                    continue
                message = orjson.loads(data)
                await manager.handle_message(message)
            except asyncio.TimeoutError:
                await ws.send(orjson.dumps({"type": "heartbeat", "status": "ping"}).decode())
            except asyncio.CancelledError:
                break
            except Exception as e1:
                logger.error(f"{e1} | {traceback.format_exc()}")
    except ConnectionClosed as e2:
        logger.error(f"{e2} | {traceback.format_exc()}")
    except Exception as e:
        logger.error(f"{e} | {traceback.format_exc()}")
        await manager.send_error(f"{e} | {traceback.format_exc()}")
        await ws.send({"message": "会话token无效", "error": str(e)})
    finally:
        if user_id:
            await manager.disconnect(user_id)
        await ws.close()


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
    if await Room.filter(name=room_name, deleted=False).exists():
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
@requires_permission('warroom:u')
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
    room = await Room.get(id=room_id).prefetch_related('creator')
    if room is None:
        return CustomError(ErrorCode.RoomNotExists)
    user = request.ctx.session['account_info']
    # 房间只能由创建者删除
    if str(room.creator.id) != str(user.id):
        return CustomError(ErrorCode.RoomDeleteNotAllowed)
    await Room.filter(id=room_id).update(deleted=True)
    return response_ok("房间删除成功")


@warroom_bp.post("/rooms_list")
@requires_permission('warroom:r')
async def get_room_list(request: Request):
    """
        获取所有作战室列表

        请求参数：
            keyword: 可选，搜索作战室
    """
    condition = Q(users__id=request.ctx.session['account_info'].id)
    keyword = request.json.get("keyword")
    if keyword:
        condition = condition & Q(name__icontains=keyword)

    # 前端是先获取再查询, 没有使用到这里的查询参数
    rooms = await Room.filter(condition).order_by('-ctime').prefetch_related('creator', 'users')
    return response_ok({
        "rooms": [r.json for r in rooms]
    })


@warroom_bp.post("/members")
@requires_permission('warroom:r')
async def acquire_members(request: Request):
    """
    获取指定作战室的用户列表

    请求参数：
        id: 必填，房间id
    """
    room_id = request.json.get('id')

    room = await Room.get(id=room_id).prefetch_related('creator', 'users')
    if room is None:
        raise CustomError(ErrorCode.RoomNotExists)

    user_list = []
    for user in room.users:
        # 获取用户角色（用户和角色是多对多的关系）
        user_roles = await user.roles.all()
        roles_list = [role.name for role in user_roles]
        user_list.append({
            "id": str(user.id),
            "username": user.username,
            "roles": roles_list
        })

    return response_ok(data={
        "members": user_list
    })


@warroom_bp.post("/get_private_room")
@requires_permission('warroom:c')
async def get_private_room(request: Request):
    """
    获取或创建与指定用户的私聊房间

    请求参数：
        partner_id: 必填，私聊对象用户ID
    """
    user = request.ctx.session['account_info']
    partner_id = request.json.get('partner_id')
    partner = await Account.get(id=partner_id)
    if partner is None:
        return CustomError(ErrorCode.AccountNotFound)
    # 先获取私聊房间
    chat_room = await get_or_create_private_chat_room(request.ctx.cache_client, user, partner)
    return response_ok({"room_id": chat_room.id, "name": chat_room.name})


@warroom_bp.post("/user_rooms")
@requires_permission('warroom:a')
async def get_user_rooms(request: Request):
    """
    获取当前用户已加入的作战室列表

    请求参数：
        keyword: 可选，搜索作战室
    """
    user = request.ctx.session['account_info']
    keyword = request.json.get('keyword', '')
    # 筛选 deleted=False, 该项目使用软删除的方式
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
@requires_permission('warroom:a')
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

    # 群聊历史. 先查询后创建消息发送
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

        partner = await Account.get(id=partner_id)
        if partner is None:
            return CustomError(ErrorCode.AccountNotFound)
        # 先获取私聊房间
        chat_room = await get_or_create_private_chat_room(request.ctx.cache_client, user, partner)
        # 消息计数
        total_count = await Message.filter(
            Q(Q(sender_id=user_id) & Q(receiver_id=partner_id)) |
            Q(Q(sender_id=partner_id) & Q(receiver_id=user_id)),
            type="private",
            room_id=chat_room.id
        ).count()
        # 分页处理
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
        # 传入房间ID, 搜索房间群聊记录
        if room_id:
            query = Message.filter(
                type="public",
                room_id=room_id,
                content__contains=keyword,
                is_recalled=False,
            )
        # 传入私聊用户ID, 搜索私聊历史记录
        elif partner_id:
            partner = await Account.get(id=partner_id)
            if partner is None:
                return CustomError(ErrorCode.AccountNotFound)
            # 先获取私聊房间
            chat_room = await get_or_create_private_chat_room(request.ctx.cache_client, user, partner)
            query = Message.filter(
                # 筛消息发送/接收包含私聊双方的，建议改造创建私聊房间逻辑，直接按房间号筛就得了
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
            room_id=room_id,
            deleted=False
        ).count()

        announcements = await Message.filter(
            type="announcement",
            room_id=room_id,
            deleted=False
        ).order_by("-ctime").offset(offset).limit(limit)

        announcement_list = []
        for msg in announcements:
            sender = await msg.sender
            announcement_list.append({
                "id": str(msg.id),
                "content": msg.content,
                "image_content": msg.image_content,
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
