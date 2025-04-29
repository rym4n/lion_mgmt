import traceback
from sanic import Websocket
import json
import asyncio
from datetime import datetime
from collections import defaultdict
from typing import Dict, Set, Tuple
from websockets import ConnectionClosed
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from sanic.log import logger
from models.warroom import Room, Message
from models.user import Account


async def get_or_create_private_chat_room(user_id1: str, user_id2: str) -> Room:
    if user_id1 > user_id2:
        user_id1, user_id2 = user_id2, user_id1
    users = await Account.filter(id__in=[user_id1, user_id2])
    user_map = {str(user.id): user for user in users}
    user1 = user_map.get(user_id1)
    user2 = user_map.get(user_id2)
    if not user1 or not user2:
        raise CustomError(ErrorCode.AccountNotFound)
    private_room_name = f"私聊_{user1.username}_{user2.username}"
    room = await Room.filter(name=private_room_name).prefetch_related('users').first()
    if not room:
        room = await Room.create(name=private_room_name, creator=user1)
        await room.users.add(user1, user2)
    return room


class ConnectionManager:
    """
    Websocket连接管理器，处理所有连接和消息
    """

    def __init__(self):
        self.active_connections: Dict[str, Websocket] = {}  # 活跃连接
        self.heartbeats: Dict[str, datetime] = {}  # 心跳时间记录
        self.room_members: Dict[str, Set[str]] = defaultdict(set)  # 房间成员集合
        self.user_current_room: Dict[str, str] = {}  # 用户当前所在房间
        self.private_chat_rooms: Dict[Tuple[str, str], str] = {}  # 私聊房间映射
        self.online_users: Set[str] = set()  # 在线用户ID集合

    async def connect(self, ws: Websocket, user_id: str):
        """处理用户连接"""
        self.active_connections[user_id] = ws
        self.heartbeats[user_id] = datetime.now()
        self.online_users.add(user_id)

        await self._broadcast_user_status(user_id, True)

    async def disconnect(self, user_id: str):
        """处理用户断开连接"""
        current_room_id = self.user_current_room.get(user_id)
        affected_rooms = []

        # 从所有房间成员列表中移除该用户
        for room_id in list(self.room_members.keys()):
            if user_id in self.room_members[room_id]:
                self.room_members[room_id].discard(user_id)
                affected_rooms.append(room_id)

        # 清除内存相关记录
        self.user_current_room.pop(user_id, None)
        self.active_connections.pop(user_id, None)
        self.heartbeats.pop(user_id, None)
        self.online_users.discard(user_id)

        # 广播用户离开作战室
        if current_room_id:
            user = await Account.get(id=user_id)
            if user:
                await self._broadcast_to_room(current_room_id, {
                    "type": "system",
                    "message": f"{user.username} 离开作战室"
                })

                # 广播用户离线状态变更
                await self._broadcast_user_status(user_id, False)

    async def _broadcast_user_status(self, user_id: str, is_online: bool):
        """广播用户在线状态变更"""
        try:
            user = await Account.get(id=user_id)
            if not user:
                return

            status_message = {
                "type": "user_status",
                "user_id": user_id,
                "username": user.username,
                "online": is_online,
                "timestamp": str(datetime.now())
            }

            # 广播给所有连接的客户端
            for uid, connection in self.active_connections.items():
                await connection.send(json.dumps(status_message))
        except Exception as e:
            logger.error(f"广播用户状态错误: {str(e)}")

    async def handle_message(self, user_id: str, data: dict):
        """消息处理"""
        try:
            message_type = data.get("type")
            # 按消息类型调用处理方法
            if message_type == "public":
                await self._handle_public_message(user_id, data)
            elif message_type == "private":
                await self._handle_private_message(user_id, data)
            elif message_type == "recall":
                await self._handle_recall_message(user_id, data)
            elif message_type == "announcement":
                await self._handle_announcement(user_id, data)
            elif message_type == "delete_announcement":
                await self._handle_delete_announcement(user_id, data)
            elif message_type == "request_online_users":
                await self._handle_request_online_users(user_id, data)
            elif message_type == "heartbeat":
                await self._handle_heartbeat(user_id)
            else:
                logger.error(f"{traceback.format_exc()}")
                await self._send_error(user_id, "未知消息类型")
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self._send_error(user_id, "服务器内部错误")

    async def _handle_public_message(self, user_id: str, data: dict):
        """处理发送群聊消息"""
        try:
            current_room_id = data.get("room_id") or self.user_current_room.get(user_id)
            if not current_room_id:
                await self._send_error(user_id, "未加入任何作战室")
                return

            user = await Account.get(id=user_id)
            if not user:
                await self._send_error(user_id, "找不到用户")
                return

            room = await Room.get(id=current_room_id)
            if not room:
                await self._send_error(user_id, "找不到作战室")
                return

            content = data.get("content", "")
            image_content = data.get("image_content", "")

            # 保存消息到数据库
            message = await Message.create(
                type="public",
                sender=user,
                room=room,
                content=content,
                image_content=image_content,
                receiver=user  # 群聊消息设为发送者自己作为占位符
            )

            # 广播消息到房间所有用户
            message_data = {
                "type": "public",
                "message_id": str(message.id),
                "sender_id": user_id,
                "sender_name": user.username,
                "content": content,
                "image_content": image_content,
                "timestamp": str(message.ctime),
                "room_id": current_room_id
            }

            await self._broadcast_to_room(current_room_id, message_data)

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self._send_error(user_id, "发送消息失败")

    async def _handle_private_message(self, user_id: str, data: dict):
        """处理发送私聊消息"""
        try:
            receiver_id = data.get("receiver_id")
            if not receiver_id:
                await self._send_error(user_id, "缺少接收者ID")
                return

            sender = await Account.get(id=user_id)
            if not sender:
                await self._send_error(user_id, "找不到发送者")
                return

            receiver = await Account.get(id=user_id)
            if not receiver:
                await self._send_error(user_id, "接收者不存在")
                return

            content = data.get("content", "")
            image_content = data.get("image_content", "")

            # 获取或创建私聊房间
            try:
                chat_room = await get_or_create_private_chat_room(user_id, receiver_id)
                # 缓存房间ID
                if user_id < receiver_id:
                    self.private_chat_rooms[(user_id, receiver_id)] = str(chat_room.id)
                else:
                    self.private_chat_rooms[(receiver_id, user_id)] = str(chat_room.id)
            except Exception as e:
                await self._send_error(user_id, str(e))
                return

            message = await Message.create(
                type="private",
                sender=sender,
                receiver=receiver,
                content=content,
                image_content=image_content,
                room=chat_room
            )

            # 构建消息数据
            message_data = {
                "type": "private",
                "message_id": str(message.id),
                "sender_id": user_id,
                "sender_name": sender.username,
                "receiver_id": receiver_id,
                "receiver_name": receiver.username,
                "content": content,
                "image_content": image_content,
                "timestamp": str(message.ctime),
                "room_id": str(chat_room.id)
            }

            # 发送给发送方
            if user_id in self.active_connections:
                await self.active_connections[user_id].send(json.dumps(message_data))

            # 发送给接收方
            if receiver_id in self.active_connections:
                await self.active_connections[receiver_id].send(json.dumps(message_data))

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self._send_error(user_id, "发送私聊消息失败")

    async def _handle_recall_message(self, user_id: str, data: dict):
        """处理消息撤回"""
        message_id = data.get("message_id")
        if not message_id:
            await self._send_error(user_id, "缺少消息ID")
            return

        message = await Message.get(id=message_id)
        if not message:
            await self._send_error(user_id, "消息不存在")
            return

        # 非管理员只能2分钟以内撤回
        now = datetime.now()
        message_time = message.ctime

        # 确保时区一致，将两个时间都转换为无时区信息
        if message_time.tzinfo is not None:
            message_time = message_time.replace(tzinfo=None)

        if (now - message_time).total_seconds() > 120:
            await self._send_error(user_id, "消息已超过2分钟，无法撤回")
            return

        message.is_recalled = True
        await message.save()

        recall_notice = {
            "type": "recall",
            "message_id": str(message.id),
            "sender_id": user_id,
            "timestamp": str(now)
        }

        if message.type == "public":
            # 获取关联的room对象
            room = await message.room
            await self._broadcast_to_room(str(room.id), recall_notice)
        elif message.type == "private":
            sender = await message.sender
            receiver = await message.receiver
            targets = {str(sender.id), str(receiver.id)}
            for uid in targets:
                if uid in self.active_connections:
                    await self.active_connections[uid].send(json.dumps(recall_notice))

        if user_id in self.active_connections:
            await self.active_connections[user_id].send(json.dumps({
                "type": "recall_success",
                "message_id": message_id
            }))

    async def _handle_announcement(self, user_id: str, data: dict):
        """处理发布公告请求"""
        user = await Account.get(id=user_id)

        content = data.get("content")
        image_content = data.get("image_content", "")
        room_id = data.get("room_id")
        if not content or not room_id:
            await self._send_error(user_id, "公告内容和作战室ID不能为空")
            return

        room = await Room.get(id=room_id)
        if not room:
            await self._send_error(user_id, "作战室不存在")
            return

        announcement = await Message.create(
            type="announcement",
            sender=user,
            room=room,
            content=content,
            image_content=image_content,
            is_pinned=True,
            receiver=user  # 设公告发送者自己作为占位符
        )

        # 指定房间广播公告
        await self._broadcast_to_room(room_id, {
            "type": "new_announcement",
            "id": str(announcement.id),
            "content": announcement.content,
            "image_content": announcement.image_content,
            "timestamp": str(announcement.ctime),
            "sender_name": user.username,
            "sender_id": str(user.id),
            "room_id": room_id
        })

    async def _handle_delete_announcement(self, user_id: str, data: dict):
        """处理删除公告请求"""
        announcement_id = data.get("announcement_id")
        if not announcement_id:
            await self._send_error(user_id, "缺少公告ID")
            return

        announcement = await Message.get(id=announcement_id, type="announcement")
        if not announcement:
            await self._send_error(user_id, "公告不存在")
            return

        sender = await announcement.sender
        sender_id = str(sender.id)

        if sender_id != user_id:
            await self._send_error(user_id, "无权删除该公告")
            return

        # 获取房间对象
        room = await announcement.room
        room_id = str(room.id)
        announcement.deleted = True
        await announcement.save()

        # 通知房间内所有成员
        await self._broadcast_to_room(room_id, {
            "type": "announcement_deleted",
            "announcement_id": announcement_id
        })

        # 通知删除成功
        if user_id in self.active_connections:
            await self.active_connections[user_id].send(json.dumps({
                "type": "delete_announcement_success",
                "announcement_id": announcement_id
            }))

    async def _handle_heartbeat(self, user_id: str):
        """处理心跳消息"""
        self.heartbeats[user_id] = datetime.now()
        if user_id in self.active_connections:
            await self.active_connections[user_id].send(json.dumps({
                "type": "heartbeat",
                "status": "pong"
            }))

    async def _broadcast_to_room(self, room_id: str, message: dict, exclude: list = None):
        """向指定作战室广播消息"""
        exclude = exclude or []
        closed_connections = []
        # 获取房间成员
        room = await Room.get(id=room_id)
        if not room:
            return

        try:
            room_members = self.room_members.get(room_id, set())
            if not room_members:
                members = await room.users.all()
                room_members = {str(member.id) for member in members}
                # 更新内存中的房间成员
                self.room_members[room_id] = room_members

            # 广播消息给所有在线成员
            for uid in room_members:
                if uid in exclude:
                    continue
                if uid in self.active_connections:
                    try:
                        await self.active_connections[uid].send(json.dumps(message))
                    except ConnectionClosed:
                        closed_connections.append(uid)
                    except Exception as e:
                        closed_connections.append(uid)

            # 处理已关闭的连接
            for uid in closed_connections:
                await self.disconnect(uid)

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            for uid in self.active_connections.keys():
                if uid not in exclude:
                    await self.active_connections[uid].send(json.dumps(message))


    async def _send_error(self, user_id: str, message: str):
        """发送错误消息给指定用户"""
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].send(json.dumps({
                    "type": "error",
                    "message": message,
                    "timestamp": str(datetime.now())
                }))
            except ConnectionClosed as e:
                logger.error(f"{e} | {traceback.format_exc()}")
                await self.disconnect(user_id)

    async def _handle_request_online_users(self, user_id: str, data: dict):
        """处理请求在线用户列表"""
        try:
            room_id = data.get("room_id")
            if not room_id:
                await self._send_error(user_id, "缺少房间ID")
                return

            # 获取指定房间的所有用户
            room = await Room.get(id=room_id)
            if not room:
                await self._send_error(user_id, "找不到作战室")
                return

            room_users = await room.users.all()
            online_status = []

            # 构建在线状态信息
            for user in room_users:
                is_online = str(user.id) in self.online_users
                online_status.append({
                    "user_id": str(user.id),
                    "username": user.username,
                    "online": is_online
                })

            # 发送在线状态列表
            if user_id in self.active_connections:
                await self.active_connections[user_id].send(json.dumps({
                    "type": "online_users_list",
                    "room_id": room_id,
                    "users": online_status
                }))

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self._send_error(user_id, "获取在线用户列表失败")

    async def check_heartbeats(self):
        """定期检查心跳状态和广播在线用户列表"""
        while True:
            try:
                now = datetime.now()
                inactive_users = []
                for uid, last_beat in self.heartbeats.items():
                    if (now - last_beat).total_seconds() > 60:
                        inactive_users.append(uid)
                for uid in inactive_users:
                    await self.disconnect(uid)
                # 每30秒广播一次完整在线用户列表
                for uid, connection in self.active_connections.items():
                    await connection.send(json.dumps({
                        "type": "online_users_update",
                        "online_users": list(self.online_users),
                        "timestamp": str(now)
                    }))
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"{e} | {traceback.format_exc()}")
                await asyncio.sleep(60)