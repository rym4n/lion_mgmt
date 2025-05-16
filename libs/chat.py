import traceback
from datetime import datetime

import orjson
from sanic import Websocket
from sanic.log import logger
from websockets import ConnectionClosed

from models.user import Account
from models.warroom import Room, Message


# 获取或创建私聊作战室
async def get_or_create_private_chat_room(cache_client, sender: Account, receiver: Account) -> Room:
    # 保证顺序
    key = f"{sender.id}&{receiver.id}"
    private_room_name = f"{sender.display_name}和{receiver.display_name}的私聊"
    if sender.id > receiver.id:
        key = f"{receiver.id}&{sender.id}"
        private_room_name = f"{receiver.display_name}和{sender.display_name}的私聊"
    user_private_chat_rooms = await cache_client.get("user_private_chat_rooms")
    room = user_private_chat_rooms[key]
    if not room:
        # 作战室名字
        room = await Room.get(name=private_room_name)
        # 先查后创
        if not room:
            room = await Room.create(name=private_room_name, creator=sender)
            await room.users.add(*sender)
            await room.users.add(*receiver)
        user_private_chat_rooms[key] = room
        await cache_client.set("user_private_chat_rooms", user_private_chat_rooms)
    return room


class ChatManager:
    """
    Websocket连接管理器，处理所有连接和消息
    """

    def __init__(self, user: Account, ws: Websocket, cache_client):
        # active_connections 活跃连接
        # room_members 房间在线成员集合
        # user_current_room 用户当前所在房间
        # private_chat_rooms 私聊房间映射
        # online_users 在线用户ID集合
        self.cache_client = cache_client
        self.ws = ws
        self.user = user

    async def connect(self):
        """处理用户连接"""
        active_connections = await self.cache_client.get("active_connections") or {}
        active_connections[self.user.id] = self.ws
        await self.cache_client.set("active_connections", active_connections)
        online_users = await self.cache_client.get("online_users") or set()
        online_users.add(self.user.id)
        await self.cache_client.set("online_users", online_users)
        await self._broadcast_user_status(True)

    async def disconnect(self, user_id: str):
        """处理用户断开连接"""
        # current_room_id = await self.cache_client.hget(f"user_current_room", user_id)

        # 从所有房间成员列表中移除该用户
        rooms = await self.cache_client.get("room_members") or {}
        for room_id, members in rooms.items():
            if user_id in members:
                members.discard(user_id)
                await self.cache_client.set(f"room_members", rooms)

        # 清除相关记录
        user_current_room = await self.cache_client.get("user_current_room") or {}
        user_current_room.pop(user_id, None)
        await self.cache_client.set("user_current_room", user_current_room)

        active_connections = await self.cache_client.get("active_connections") or {}
        active_connections.pop(user_id, None)
        await self.cache_client.set("active_connections", active_connections)

        online_users = await self.cache_client.get("online_users") or set()
        online_users.discard(user_id)
        await self.cache_client.set("online_users", online_users)

        # 广播
        # if current_room_id:
        #     await self._broadcast_to_room(current_room_id, {"type": "system", "message": f"{self.user.username} 离开作战室"})
        #     # 广播用户离线状态变更
        #     await self._broadcast_user_status(user_id, False)

    async def _broadcast_user_status(self, is_online: bool):
        """广播用户在线状态变更"""
        try:
            status_message = {
                "type": "user_status",
                "user_id": self.user.id,
                "username": self.user.username,
                "online": is_online,
                "timestamp": str(datetime.now())
            }
            active_connections = await self.cache_client.get("active_connections") or {}
            for _, conn in active_connections.items():
                await conn.send(orjson.dumps(status_message).decode())
        except Exception as e:
            logger.error(f"广播用户状态错误: {str(e)}")

    async def handle_message(self, data: dict):
        """消息处理"""
        try:
            message_type = data.get("type")
            # 按消息类型调用处理方法 (前端发送)
            if message_type == "public":
                await self._handle_public_message(data)
            elif message_type == "private":
                await self._handle_private_message(data)
            elif message_type == "recall":
                await self._handle_recall_message(data)
            elif message_type == "announcement":  # 发布公告，公告查询在消息查询API接口里
                await self._handle_announcement(data)
            elif message_type == "delete_announcement":
                await self._handle_delete_announcement(data)
            elif message_type == "request_online_users":
                await self._handle_request_online_users(data)
            elif message_type == "heartbeat":
                await self._handle_heartbeat()
            else:
                logger.error(f"{traceback.format_exc()}")
                await self.send_error("未知消息类型")
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self.send_error("服务器内部错误")

    async def _handle_public_message(self, data: dict):
        """处理发送群聊消息"""
        try:
            current_room_id = data.get("room_id")
            if not current_room_id:
                user_current_room = await self.cache_client.get("user_current_room") or {}
                current_room_id = user_current_room.get(self.user.id)
            if not current_room_id:
                await self.send_error("未加入任何作战室")
                return

            room = await Room.get(id=current_room_id)
            if not room:
                await self.send_error("找不到作战室")
                return

            content = data.get("content", "")
            image_content = data.get("image_content", "")

            # 保存消息到数据库
            message = await Message.create(
                type="public",
                sender=self.user,
                room=room,
                content=content,
                image_content=image_content,
                receiver=self.user  # 群聊消息设为发送者自己作为占位符
            )

            # 广播
            message_data = {
                "type": "public",
                "message_id": str(message.id),
                "sender_id": self.user.id,
                "sender_name": self.user.username,
                "content": content,
                "image_content": image_content,
                "timestamp": str(message.ctime),
                "room_id": current_room_id
            }

            await self._broadcast_to_room(current_room_id, message_data)

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self.send_error("发送消息失败")

    async def _handle_private_message(self, data: dict):
        """处理发送私聊消息"""
        try:
            receiver_id = data.get("receiver_id")
            if not receiver_id:
                await self.send_error("缺少接收者ID")
                return

            receiver = await Account.get(id=receiver_id)
            if not receiver:
                await self.send_error("接收者不存在")
                return

            content = data.get("content", "")
            image_content = data.get("image_content", "")

            # 获取或创建私聊房间
            chat_room = await get_or_create_private_chat_room(self.cache_client, self.user, receiver)

            message = await Message.create(
                type="private",
                sender=self.user,
                receiver=receiver,
                content=content,
                image_content=image_content,
                room=chat_room
            )

            # 构建消息数据
            message_data = {
                "type": "private",
                "message_id": str(message.id),
                "sender_id": self.user.id,
                "sender_name": self.user.username,
                "receiver_id": receiver_id,
                "receiver_name": receiver.username,
                "content": content,
                "image_content": image_content,
                "timestamp": str(message.ctime),
                "room_id": str(chat_room.id)
            }
            active_connections = await self.cache_client.get("active_connections") or {}
            for uid in [self.user.id, receiver_id]:
                if uid in active_connections:
                    await active_connections[uid].send(orjson.dumps(message_data).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self.send_error("发送私聊消息失败")

    async def _handle_recall_message(self, data: dict):
        """处理消息撤回"""
        message_id = data.get("message_id")
        if not message_id:
            await self.send_error("缺少消息ID")
            return

        message = await Message.get(id=message_id).prefetch_related("room", 'sender', 'receiver')
        if not message:
            await self.send_error("消息不存在")
            return

        now = datetime.now()
        message_time = message.ctime

        # 确保时区一致，将两个时间都转换为无时区信息
        if message_time.tzinfo is not None:
            message_time = message_time.replace(tzinfo=None)

        if (now - message_time).total_seconds() > 600:
            await self.send_error("消息已超过10分钟，无法撤回")
            return

        message.is_recalled = True
        await message.save()

        recall_notice = {
            "type": "recall",
            "message_id": str(message.id),
            "sender_id": self.user.id,
            "timestamp": str(now)
        }
        if message.type == "public":
            await self._broadcast_to_room(message.room.id, recall_notice)
        elif message.type == "private":
            active_connections = await self.cache_client.get("active_connections") or {}
            for uid in [message.sender.id, message.receiver.id]:
                if uid in active_connections:
                    await active_connections[uid].send(orjson.dumps(recall_notice).decode())

        await self.ws.send(orjson.dumps({"type": "recall_success", "message_id": message_id}).decode())

    async def _handle_announcement(self, data: dict):
        """处理发布公告请求"""
        content = data.get("content")
        image_content = data.get("image_content", "")
        room_id = data.get("room_id")
        if not content or not room_id:
            await self.send_error("公告内容和作战室ID不能为空")
            return

        room = await Room.get(id=room_id)
        if not room:
            await self.send_error("作战室不存在")
            return

        announcement = await Message.create(
            type="announcement",
            sender=self.user,
            room=room,
            content=content,
            image_content=image_content,
            is_pinned=True,
            receiver=self.user  # 设公告发送者自己作为占位符
        )

        # 指定房间广播公告
        await self._broadcast_to_room(room_id, {
            "type": "new_announcement",
            "id": announcement.id,
            "content": announcement.content,
            "image_content": announcement.image_content,
            "timestamp": str(announcement.ctime),
            "sender_name": self.user.username,
            "sender_id": self.user.id,
            "room_id": room_id
        })

    async def _handle_delete_announcement(self, data: dict):
        """处理删除公告请求"""
        announcement_id = data.get("announcement_id")
        if not announcement_id:
            await self.send_error("缺少公告ID")
            return

        announcement = await Message.get(id=announcement_id, type="announcement").prefetch_related("sender", 'room')
        if not announcement:
            await self.send_error("公告不存在")
            return

        if announcement.sender.id != self.user.id:
            await self.send_error("无权删除该公告")
            return

        # 获取房间对象
        announcement.deleted = True
        await announcement.save()

        # 通知房间内所有成员
        await self._broadcast_to_room(announcement.room.id, {"type": "announcement_deleted", "announcement_id": announcement_id})

        # 通知删除成功
        await self.ws.send(orjson.dumps({"type": "delete_announcement_success", "announcement_id": announcement_id}).decode())

    async def _handle_heartbeat(self):
        """处理心跳消息"""
        now = datetime.now()
        online_users = await self.cache_client.get("online_users") or set()
        await self.ws.send(orjson.dumps({"type": "online_users_update", "online_users": list(online_users), "timestamp": str(now)}).decode())

    async def _broadcast_to_room(self, room_id: str, message: dict):
        """向指定作战室广播消息"""
        closed_connections = []
        # 获取房间成员
        room = await Room.get(id=room_id).prefetch_related("users")
        if not room:
            return
        try:
            rooms = await self.cache_client.get("room_members") or {}
            members = set()
            if rooms:
                members = rooms.get(room_id) or set()
            else:
                members = {u.id for u in room.users}
                # 更新内存中的房间成员
                rooms[room_id] = members
                await self.cache_client.set("room_members", rooms)

            # 广播消息给所有在线成员
            active_connections = await self.cache_client.get("active_connections") or {}
            for uid in members:
                try:
                    if uid in active_connections:
                        await active_connections[uid].send(orjson.dumps(message).decode())
                except ConnectionClosed:
                    closed_connections.append(uid)

            # 处理已关闭的连接
            for uid in closed_connections:
                await self.disconnect(uid)

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            active_connections = await self.cache_client.get("active_connections") or {}
            for k in active_connections:
                await active_connections[k].send(orjson.dumps(message).decode())

    async def send_error(self, message: str):
        """发送错误消息给指定用户"""
        try:
            await self.ws.send(orjson.dumps({"type": "error", "message": message, "timestamp": str(datetime.now())}).decode())
        except ConnectionClosed as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self.disconnect(self.user.id)

    async def _handle_request_online_users(self, data: dict):
        """处理请求在线用户列表"""
        try:
            room_id = data.get("room_id")
            if not room_id:
                await self.send_error("缺少房间ID")
                return

            # 获取指定房间的所有用户
            room = await Room.get(id=room_id)
            if not room:
                await self.send_error("找不到作战室")
                return

            room_users = await room.users.all()
            online_status = []

            # 构建在线状态信息
            online_users = await self.cache_client.get("online_users") or set()
            for user in room_users:
                online_status.append({"user_id": str(user.id), "username": user.username, "online": user.id in online_users})

            # 发送在线状态列表
            await self.ws.send(orjson.dumps({"type": "online_users_list", "room_id": room_id, "users": online_status}).decode())

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await self.send_error("获取在线用户列表失败")
