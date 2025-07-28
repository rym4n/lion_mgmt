# coding=utf-8

import asyncio
import traceback

import orjson
from sanic import Blueprint, Request, Websocket
from sanic.log import logger
from tortoise.expressions import Q
from websockets import ConnectionClosed

from core.error_code import ErrorCode
from core.response import response_ok
from libs.chat import get_or_create_private_chat_room, chat_manager
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from libs.utils import gen_order_version, gen_random_code
from models import Approvement, Events, ToolAction, Tool, Runlog
from models.user import Account
from models.warroom import Room, Message, MessageAccount
from settings import KAFKA_SERVERS, TASK_TOPIC

warroom_bp = Blueprint('warroom', url_prefix='/warroom')
__all__ = ['warroom_bp']

TYPE_DICT = {
    "PLAYBOOK": "flow",
    "ACTION": "action"
}


@warroom_bp.websocket('/ws')
@requires_permission('warroom:r')
async def warroom_ws(request: Request, ws: Websocket):
    """WebSocket连接处理"""
    account = None
    try:
        # 先获取认证信息
        # auth_data = await asyncio.wait_for(ws.recv(), timeout=15)
        # auth_json = orjson.loads(auth_data)
        # payload = jwt.decode(auth_json.get('auth', ''), SECRET, SESSION_ENCODING_ALGORITHM)
        # validate_token(payload, request)

        # account = await request.ctx.cache_client.get(f"account_{payload['sub']}")
        account = await request.ctx.session.get('account_info')
        if not account:
            logger.warning("redis里已经没有account信息了")
            raise CustomError(ErrorCode.SessionInvalid)
        # 建立连接
        await chat_manager.connect(account, ws)
        # 持续接收和处理消息f
        async for msg in ws:
            try:
                message = orjson.loads(msg)
                await chat_manager.handle_message(account, ws, message)
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
        await ws.send(orjson.dumps({"type": "error", "message": str(e)}).decode())
    finally:
        if account:
            await chat_manager.disconnect(account.id)
        await ws.close()


@warroom_bp.post("/create")
@requires_permission('warroom:c')
async def create_room(request: Request):
    """
    新建作战室

    请求参数：
        name: 必填， 作战室名称
        members: 选填，邀请其他用户ID列表
    """
    room_name = request.json.get('name')
    members_ids = request.json.get('members', [])
    user_id = []
    # 作战室名称唯一
    if await Room.filter(name=room_name).exists():
        raise CustomError(ErrorCode.RoomNameExists)
    creator = request.ctx.session['account_info']
    room = await Room.create(name=room_name, creator=creator)
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

    await Events.create(event_name=room_name, event_process_status="待处理", event_source="人工",
                        owner={"role": [], "user": user_id}, creator=creator, updator=creator,
                        room=room)

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
        id: 必填，作战室id
    """
    room = await Room.get(id=request.json.get('id'))
    user = request.ctx.session['account_info']
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if is_member:
        return response_ok("用户已经是该作战室成员")
    await room.users.add(user)
    return response_ok("成功加入作战室")


@warroom_bp.post("/quit")
@requires_permission('warroom:u')
async def quit_room(request: Request):
    """
    退出指定作战室，只有群聊可以退出，私聊不可以

    请求参数：
        id: 必填， 作战室id
    """
    room = await Room.get(id=request.json.get('id'))
    user = request.ctx.session['account_info']
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if not is_member:
        return response_ok("用户并不在该作战室，无需退出")
    await room.users.remove(user)
    await user.star_rooms.remove(room)
    return response_ok("成功退出作战室")


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
        id: 必填，作战室id
    """
    room_id = request.json.get('id')

    room = await Room.get(id=room_id).prefetch_related('creator', 'users')

    user_list = []
    for user in room.users:
        # 获取用户角色（用户和角色是多对多的关系）
        user_roles = await user.roles.all()
        roles_list = [role.name for role in user_roles]
        user_list.append({
            "id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "roles": roles_list
        })

    return response_ok(data={
        "members": user_list
    })


@warroom_bp.post("/get_private_room")
@requires_permission('warroom:c')
async def get_private_room(request: Request):
    """
    获取或创建与指定用户的私聊作战室

    请求参数：
        partner_id: 必填，私聊对象用户ID
    """
    user = request.ctx.session['account_info']
    partner_id = request.json.get('partner_id')
    partner = await Account.get(id=partner_id)
    if partner is None:
        raise CustomError(ErrorCode.AccountNotFound)
    # 先获取私聊作战室
    room_info = await get_or_create_private_chat_room(request.ctx.cache_client, user, partner)
    room = await Room.get_or_none(id=room_info['room_id'])
    await user.star_rooms.add(room)
    await partner.star_rooms.add(room)
    return response_ok(room_info)


@warroom_bp.post("/get_star_rooms")
@requires_permission('warroom:r')
async def get_star_rooms(request: Request):
    """
    获取关注列表

    请求参数：
        keyword: 可选，搜索关注列表
    """
    user = request.ctx.session['account_info']
    return response_ok({
        "star_rooms": [r.json for r in await user.star_rooms.all().prefetch_related('creator', 'users')]
    })


@warroom_bp.post("/star_room")
@requires_permission('warroom:r')
async def star_room(request: Request):
    """
    关注指定作战室

    请求参数：
        id: 必填， 作战室id
    """
    room_id = request.json.get('id')
    room = await Room.get(id=room_id)
    user = request.ctx.session['account_info']
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if not is_member:
        return response_ok("用户并不在该作战室，无需关注")
    await user.star_rooms.add(room)
    return response_ok("成功关注")


@warroom_bp.post("/unstar_room")
@requires_permission('warroom:u')
async def unstar_room(request: Request):
    """
    取消关注指定作战室

    请求参数：
        id: 必填， 作战室id
    """
    room_id = request.json.get('id')
    room = await Room.get(id=room_id)
    user = request.ctx.session['account_info']
    # 检查当前用户是否已加入该作战室
    is_member = await room.users.filter(id=user.id).exists()
    if not is_member:
        return response_ok("用户并不在该作战室，无需取消关注")
    await user.star_rooms.remove(room)
    return response_ok("成功取消关注")


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
    query = Room.filter(users__id=user.id)
    if keyword:
        query = query.filter(name__contains=keyword)
    user_rooms = await query.order_by('-ctime').prefetch_related('creator')

    room_list = []
    for room in user_rooms:
        room_list.append({
            "id": str(room.id),
            "name": room.name,
            "creator_id": room.creator.id,
            "creator_name": room.creator.display_name,
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
        room_id: 除了私聊以外必填，作战室ID
        partner_id: 私聊必填，私聊对象ID
        keyword: 搜索必填，搜索关键词
        limit: 可选，每页条数，默认20
        offset: 可选，偏移量，默认0
    """
    user = request.ctx.session['account_info']
    query_type = request.json.get("query_type")
    room_id = request.json.get("room_id")
    partner_id = request.json.get("partner_id")
    keyword = request.json.get("keyword", "")
    limit = request.json.get("limit", 20)
    offset = request.json.get("offset", 0)

    # 群聊历史. 先查询后创建消息发送
    if query_type == "public_history":
        if not room_id:
            raise CustomError(ErrorCode.ParamValueError)
        total_count = await Message.filter(type__in=["public", "ai", "runlog"], room_id=room_id).count()
        messages = await Message.filter(type__in=["public", "ai", "runlog"], room_id=room_id).order_by("-ctime").offset(offset).limit(limit).prefetch_related('sender')
        message_list = []
        for msg in reversed(messages):
            if msg.type == "runlog":
                runlog = await Runlog.get(id=msg.content)
                content = "[已撤回]" if msg.is_recalled else runlog.json
            else:
                content = "[已撤回]" if msg.is_recalled else msg.content
            message_list.append({
                "type": msg.type,
                "message_id": str(msg.id),
                "sender_id": str(msg.sender.id),
                "sender_name": msg.sender.username,
                "sender_display_name": msg.sender.display_name,
                "content": content,
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
            raise CustomError(ErrorCode.ParamValueError)

        partner = await Account.get(id=partner_id)
        if partner is None:
            raise CustomError(ErrorCode.AccountNotFound)
        # 先获取私聊作战室
        chat_room = await get_or_create_private_chat_room(request.ctx.cache_client, user, partner)
        condition = (Q(sender__id=user.id) & Q(receiver_id=partner_id)) | (Q(sender_id=partner_id) & Q(receiver_id=user.id))
        # 消息计数
        total_count = await Message.filter(condition, type__in=["private", "ai", "runlog"], room_id=chat_room['room_id']).count()
        # 分页处理
        messages = await Message.filter(condition, type__in=["private", "ai", "runlog"], room_id=chat_room['room_id']).order_by("-ctime").offset(offset).limit(limit).prefetch_related('sender')
        message_list = []
        if partner:
            for msg in reversed(messages):
                message_list.append({
                    "type": msg.type,
                    "message_id": msg.id,
                    "sender_id": msg.sender.id,
                    "sender_name": msg.sender.username,
                    "sender_display_name": msg.sender.display_name,
                    "receiver_id": partner.id,
                    "receiver_name": partner.username,
                    "receiver_display_name": partner.display_name,
                    "content": "[已撤回]" if msg.is_recalled else msg.content,
                    "image_content": msg.image_content,
                    "timestamp": str(msg.ctime),
                    "is_recalled": msg.is_recalled,
                    "room_id": chat_room['room_id']
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
            raise CustomError(ErrorCode.ParamValueError)
        # 传入作战室ID, 搜索作战室群聊记录
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
                raise CustomError(ErrorCode.AccountNotFound)
            # 先获取私聊作战室
            chat_room = await get_or_create_private_chat_room(request.ctx.cache_client, user, partner)
            # 筛消息发送/接收包含私聊双方的，建议改造创建私聊作战室逻辑，直接按作战室号筛就得了
            query = Message.filter((Q(sender_id=user.id) & Q(receiver_id=partner_id)) | (Q(sender_id=partner_id) & Q(receiver_id=user.id)), type="private", room_id=chat_room['room_id'], content__contains=keyword, is_recalled=False)
        else:
            raise CustomError(ErrorCode.ParamValueError)

        total_count = await query.count()
        messages = await query.order_by("-ctime").offset(offset).limit(limit).prefetch_related('sender', 'receiver')
        message_list = []
        for msg in messages:
            message_list.append({
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
                "room_id": msg.room_id
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
            raise CustomError(ErrorCode.ParamValueError)

        total_count = await Message.filter(
            type="announcement",
            room_id=room_id
        ).count()

        announcements = await Message.filter(
            type="announcement",
            room_id=room_id
        ).order_by("-ctime").offset(offset).limit(limit).prefetch_related('sender')

        announcement_list = []
        for msg in announcements:
            announcement_list.append({
                "id": str(msg.id),
                "content": msg.content,
                "image_content": msg.image_content,
                "timestamp": str(msg.ctime),
                "sender_name": msg.sender.username,
                "sender_display_name": msg.sender.display_name,
                "sender_id": msg.sender.id,
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
    else:
        raise CustomError(ErrorCode.ParamValueError)


@warroom_bp.post("/get/list")
@requires_permission("warroom:r")
async def get_list(request):
    """
    获取待办和消息列表（需要作战室查看权限）

    请求参数：
        无
    """
    to_do_list = []
    message_list = []
    account = request.ctx.session['account_info']
    approvements = await Approvement.filter(approvers=account.id, status=0).all()
    for approvement in approvements:
        to_do_list.append(approvement.json)
    messages = await MessageAccount.filter(account_id=account.id, status=0).all().prefetch_related("message__sender")
    for message in messages:
        message_list.append(message.json)
    return response_ok({"to_do_list": to_do_list, "message_list": message_list})


@warroom_bp.post("/up/status")
@requires_permission("warroom:u")
async def up_status(request):
    """
    更新消息阅读状态（需要作战室更新权限）

    请求参数：
        id: 必填，消息ID
    """
    id = request.json.get("id")
    if not id:
        raise CustomError(ErrorCode.ParamValueError)
    await MessageAccount.filter(id=id).update(status=1)
    return response_ok("")


@warroom_bp.post("/run")
@requires_permission("warroom:r")
async def run(request):
    """
    运行剧本或动作（需要作战室创建权限）

    请求参数：
        id: 必填，作战室ID
        type: 必填，运行类型，flow/action
        -playbook_input dict 剧本输入参数 当type为flow时必填
            示例如下：
            {
                "flow_id": "73ecfba0-1aa4-4128-b99a-b97fb08a14be",      剧本版本id
                "flow_input": {
                    "l":"123456789",        剧本输入参数
                    "c":"123456789"         剧本输入参数
                }
            }
        action_input dict 动作输入参数 当type为action时必填
            示例如下：
            {
                "app_name":"huorong_edr",         应用名称，英文标识
                "name": "中文名称",               应用中文名称
                "app_ver":"v1746495758839",      应用版本号
                "main":"huorong_edr.py",          主程序文件名  获取工具版本时会传
                "action_input":{
                    "clients":"abcd",             动作输入参数
                    "groups":"123456789"          动作输入参数
                },
                "resource": "abc",               资源名称
                "node_name":"自定义扫描",    节点名称，定时任务模块中取动作描述
                "action":"custom_scan",     动作名称 英文标识
                "action_name":"自定义扫描",    动作名称 中文名称
                "retry_times":1,             重试次数
                "timeout":300,               超时时间,单位秒
            }
    """
    room_id = request.json.get("id")
    type = request.json.get("type")
    user = request.ctx.session['account_info']
    if not room_id or not type:
        raise CustomError(ErrorCode.ParamValueError)
    params = request.json.get("playbook_input") or request.json.get("action_input")
    if not params:
        raise CustomError(ErrorCode.ParamValueError)
    params["executor"] = user.id
    msg = {"run_type": type, "run_id": gen_order_version(length=6), "run_args": params}
    room = await Room.get_or_none(id=room_id)
    room.updator = user
    room.run_id.append(msg["run_id"])
    await room.save()
    await request.ctx.cache_client.set(str(msg["run_id"]), {"room_id": str(room_id), "updator": user.id}, 3600 * 24 * 7)
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok("运行成功")


@warroom_bp.post("/execution")
@requires_permission("warroom:r")
async def execution(request):
    """
    运行剧本或动作（需要作战室创建权限）

    请求参数：
        eventId: 必填，事件ID
        executorInstanceType: 必填，运行类型，PLAYBOOK/ACTION
        executorInstanceId: 必填 实例ID(剧本版本id或动作id)
        executorActionAssetId: 非必填 动作资源id
        params: 非必填 运行参数 [{"key":"a","value":"b"},{"key":"c","value":"d"}]
    """
    eventId = request.json.get("eventId")
    executorInstanceType = request.json.get("executorInstanceType")
    executorInstanceId = request.json.get("executorInstanceId")
    executorActionAssetId = request.json.get("executorActionAssetId")
    executorParams = request.json.get("params")
    user = request.ctx.session['account_info']
    event = await (Events.get_or_none(id=eventId))
    if not event.room:
        room_name = event.event_name + "_" + gen_random_code(6)
        if await Room.filter(name=room_name, deleted=False).exists():
            raise CustomError(ErrorCode.RoomNameExists)
        room = await Room.create(name=room_name, creator=user)
        event.room = room
    room_id = event.room_id
    type = TYPE_DICT.get(executorInstanceType)
    if not room_id or not type:
        raise CustomError(ErrorCode.ParamValueError)
    params = {}
    input_data = {}
    if type == "flow":
        if executorParams:
            for param in executorParams:
                input_data[param["key"]] = param["value"]
        params["flow_id"] = executorInstanceId
        params["flow_input"] = input_data
    elif type == "action":
        action = await ToolAction.get_or_none(id=executorInstanceId, deleted=False)
        tool = await Tool.get_or_none(name=action.tool_name, version=action.version_number, deleted=False)
        if executorParams:
            for param in executorParams:
                input_data[param["key"]] = param["value"]
        params["app_name"] = tool.name
        params["name"] = tool.description
        params["app_ver"] = tool.version
        params["main"] = tool.main
        params["action_input"] = input_data
        params["resource"] = executorActionAssetId
        params["node_name"] = action.action["name"]
        params["action"] = action.action["func"]
        params["action_name"] = action.action["name"]
        params["retry_times"] = 1
        params["timeout"] = 300
    params["executor"] = user.id
    msg = {"run_type": type, "run_id": gen_order_version(length=6), "run_args": params}
    room = await Room.get_or_none(id=room_id)
    room.updator = user
    room.run_id.append(msg["run_id"])
    await room.save()
    await request.ctx.cache_client.set(str(msg["run_id"]), {"room_id": str(room_id), "updator": user.id}, 3600 * 24 * 7)
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok({"activityId": msg.get("run_id")})


@warroom_bp.get("/activity/<activityId>")
@requires_permission("warroom:r")
async def activity(request, activityId):
    """
    获取执行记录

    请求参数：
        activityId: 必填，活动ID
    """
    if not activityId:
        raise CustomError(ErrorCode.ParamValueError)
    logs = await Runlog.filter(run_id=activityId, deleted=False).all()

    return response_ok([log.json for log in logs])
