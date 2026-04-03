# coding=utf-8
import asyncio
from datetime import datetime
import hashlib
import time
import traceback

from aiocache import caches
import httpx
import orjson
import pytz
from sanic.exceptions import WebsocketClosed
from sanic.log import logger
from tortoise.expressions import Q
from tortoise.functions import Max

from libs.kafka import aconsume, aproduce
from models import (
    Approvement,
    Events,
    Playbook,
    PlaybookVersion,
    Runlog,
    ToolAction,
    ToolResources,
    ToolResourcesModel,
)
from models.user import Account
from models.warroom import Message, MessageAccount, Room
from settings import (
    AUTHENTICATION_SESSION_EXPIRATION,
    CACHE,
    RUNLOG_TOPIC,
    TZ,
    WR_TOPIC,
    TASK_TOPIC,
)


async def fetch_completion(host, api_key, model, messages):
    """
    异步调用大模型接口，获取对话补全结果。

    参数:
        host (str): 大模型服务地址
        api_key (str): 授权密钥
        model (str): 使用的模型名称
        messages (list[dict]): 对话历史，格式为 [{"role": "system|user|assistant", "content": "..."}]

    返回:
        str: 模型返回的补全内容；若请求失败或解析异常则返回空字符串
    """
    url = f"{host}/chat/completions"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4000,
        "temperature": 0.75,
    }

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(url, headers=headers, json=payload)
            data = response.json()
            return data["choices"][0]["message"]["content"] if data.get("choices") else ""
    except Exception as e:
        logger.error(f"Error on fetch_completion: {e}")
        return ""


# 获取动作推荐列表
async def get_prompt_words(prompt_word, tenant_id):
    tool_names = []
    actions = await ToolResourcesModel.filter(deleted=False, status=0, tenant_id=tenant_id).all().values("tool_name")
    for action in actions:
        if action["tool_name"] not in tool_names:
            tool_names.append(action["tool_name"])
    tools = await ToolResources.filter(deleted=False, tenant_id=tenant_id).all().values("tool_name")
    for tool in tools:
        if tool["tool_name"] not in tool_names:
            tool_names.append(tool["tool_name"])
    if "lion_mgmt" in tool_names or "lion_ai" in tool_names:
        tool_names.remove("lion_mgmt")
        tool_names.remove("lion_ai")

    latest_versions = await ToolAction.filter(deleted=False, tool_name__in=tool_names, tenant_id=tenant_id).annotate(latest_version=Max("version_number")).group_by("tool_name").values("tool_name", "latest_version")

    pairs = [(row["tool_name"], row["latest_version"]) for row in latest_versions]

    cond = Q()
    for name, ver in pairs:
        cond |= Q(tool_name=name, version_number=ver)

    actions = await ToolAction.filter(deleted=False, tenant_id=tenant_id).filter(cond).order_by("-ctime").all()

    actions_desc = {}
    actions_dict = {}
    n = 0
    for action in actions:
        name = action.action["name"].replace("\\", "/")
        if name not in actions_desc.values():
            actions_desc[str(n)] = name
            n += 1
        if name not in actions_dict:
            actions_dict[name] = [action.id]
        else:
            actions_dict[name].append(action.id)
    logger.info(actions_dict)
    logger.info(actions_desc)
    for k, v in actions_desc.items():
        prompt_word += f"{k}:{v}\n"
    return prompt_word, actions_dict, actions_desc


# 获取剧本推荐列表
async def get_playbook_prompt_words(prompt_word, tenant_id):
    playbooks = await Playbook.filter(deleted=False, internal=False, tenant_id=tenant_id).all()

    a = 0
    playbooks_desc = {}
    playbooks_dict = {}
    for playbook in playbooks:
        if await PlaybookVersion.filter(status=1, playbook_id=playbook.id, tenant_id=tenant_id).exists():
            playbooks_desc[str(a)] = {"名称": playbook.name, "描述": playbook.remark}
            a += 1
            pv = await PlaybookVersion.filter(status=1, playbook_id=playbook.id, tenant_id=tenant_id).order_by("-ctime").first()
            playbooks_dict[playbook.name] = pv.id

    logger.info(playbooks_dict)
    logger.info(playbooks_desc)
    for k, v in playbooks_desc.items():
        prompt_word += f"{k}:{v}\n"
    return prompt_word, playbooks_dict, playbooks_desc


# 获取或创建私聊作战室
async def get_or_create_private_chat_room(cache_client, sender: Account, receiver: Account, tenant_id) -> dict:
    # 保证顺序
    key = f"{sender.id}&{receiver.id}"
    private_room_name = f"{sender.username}({sender.display_name})_{receiver.username}({receiver.display_name})"
    if sender.id > receiver.id:
        key = f"{receiver.id}&{sender.id}"
        private_room_name = f"{receiver.username}({receiver.display_name})_{sender.username}({sender.display_name})"
    user_private_chat_rooms = await cache_client.get(f"user_private_chat_rooms_{tenant_id}") or {}
    if key not in user_private_chat_rooms:
        # 作战室名字
        # room = await Room.get_or_none(name=private_room_name, tenant_id=tenant_id)
        if sender.id != receiver.id:
            room = await Room.filter(room_type="private", tenant_id=tenant_id, users__id=sender.id).filter(users__id=receiver.id).first()
        else:
            # 只有自己
            all_rooms = await Room.filter(room_type="private", tenant_id=tenant_id, users__id=sender.id).prefetch_related("users")
            room = None
            for r in all_rooms:
                if len(r.users) == 1 and r.users[0].id == sender.id:
                    room = r
                    break

        # 先查后创
        if not room:
            room = await Room.create(name=private_room_name, creator=sender, tenant_id=tenant_id, room_type="private")
            await room.users.add(sender)
            await room.users.add(receiver)
        data = {"room_id": room.id, "name": room.name}
        user_private_chat_rooms[key] = data
        logger.info(user_private_chat_rooms)
        await cache_client.set(f"user_private_chat_rooms_{tenant_id}", user_private_chat_rooms, 60 * 60 * 24)
        return data
    else:
        return user_private_chat_rooms[key]


# 创建消息
async def creat_message(room_id, log, user, redis):
    sender = await Account.get(username="system")
    user = await Account.get(id=user)

    # 构造一个唯一 key（防止内容太长，建议 hash）
    key_hash = hashlib.sha1(f"{room_id}:{str(log)}".encode()).hexdigest()
    lock_key = f"create_message_lock:{key_hash}"

    # 尝试获取锁（5 秒自动过期）
    try:
        got_lock = await redis.add(lock_key, "1", ttl=5)
    except ValueError:
        got_lock = False  # key 已存在，没拿到锁

    content = log.get("run_id") + ":" + log.get("node_id")
    tenant_id = log.get("tenant_id")
    if got_lock:
        # 当前是第一个拿到锁的人，创建记录
        msg, _ = await Message.update_or_create(
            type="runlog",
            sender=sender,
            room_id=room_id,
            content=content,
            receiver=user,
            defaults={"image_content": ""},
            tenant_id=tenant_id,
        )
    else:
        # 其他执行引擎进入这里：稍等一下，再尝试获取已创建的记录
        for i in range(5):
            msg = await Message.get_or_none(content=content, room_id=room_id, receiver=user, tenant_id=tenant_id)
            if msg:
                break
            await asyncio.sleep(0.01 * (i + 1))  # 指数轮询避免数据库压力过大

    creator = await Account.get_or_none(id=log["executor"])
    log["creator_username"] = creator.username if creator else None
    log["creator_display_name"] = creator.display_name if creator else None

    message_data = {
        "type": "runlog",
        "message_id": str(msg.id),
        "sender_id": sender.id,
        "sender_name": sender.username,
        "sender_display_name": sender.display_name,
        "content": [log],
        "image_content": "",
        "timestamp": str(msg.ctime),
        "room_id": room_id,
    }

    return message_data

# 创建AI回复消息
async def creat_ai_message(msg, redis):
    sender = await Account.get(username="system")
    user = await Account.get_or_none(id=msg["receiver_id"])

    if not user:
        return None

    # 构造一个唯一 key（防止内容太长，建议 hash）
    key_hash = hashlib.sha1(f"{msg['room_id']}:{str(msg['content'])}".encode()).hexdigest()
    lock_key = f"create_message_lock:{key_hash}"

    # 尝试获取锁（5 秒自动过期）
    try:
        got_lock = await redis.add(lock_key, "1", ttl=5)
    except ValueError:
        got_lock = False  # key 已存在，没拿到锁
    
    content = msg["content"]
    room_id = msg["room_id"]
    tenant_id = msg['tenant_id']
    msg_type = msg['type']
    if got_lock:
        # 当前是第一个拿到锁的人，创建记录
        msg, _ = await Message.update_or_create(
            type=msg_type,
            sender=sender,
            room_id=room_id,
            content=content,
            receiver=user,
            defaults={"image_content": ""},
            tenant_id=tenant_id,
        )
    else:
        # 其他执行引擎进入这里：稍等一下，再尝试获取已创建的记录
        for i in range(5):
            msg = await Message.get_or_none(content=content, room_id=room_id, receiver=user, tenant_id=tenant_id)
            if msg:
                break
            await asyncio.sleep(0.01 * (i + 1))  # 指数轮询避免数据库压力过大

    message_data = {
        "type": msg_type,
        "message_id": str(msg.id),
        "sender_id": sender.id,
        "sender_name": sender.username,
        "sender_display_name": sender.display_name,
        "content": content,
        "image_content": "",
        "timestamp": str(msg.ctime),
        "room_id": room_id,
        "tenant_id": tenant_id,
    }

    return message_data



class ChatManager:
    """
    Websocket连接管理器，处理所有连接和消息
    """

    def __init__(self):
        caches.set_config(CACHE)
        self.redis = caches.get("redis_mgmt")
        self.cache_client = caches.get("redis_chat")
        self.msg_pub = self.cache_client.client
        self.active_connections: dict[str, dict] = {}
        self.event_update_buffer = {}

    async def connect(self, user: Account, ws, session_id):
        """处理用户连接"""
        if user.id not in self.active_connections:
            self.active_connections[user.id] = {session_id: {"conn": ws, "current_room": None, "run_id": None}}
        else:
            self.active_connections[user.id][session_id] = {
                "conn": ws,
                "current_room": None,
                "run_id": None,
            }
        logger.info(self.active_connections)
        status_message = {
            "type": "user_status",
            "user_id": user.id,
            "username": user.username,
            "online": True,
            "timestamp": str(datetime.now()),
        }
        await self.cache_client.set(
            f"room_online_{user.id}",
            int(time.time()),
            AUTHENTICATION_SESSION_EXPIRATION,
        )
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(status_message).decode())

    async def disconnect(self, user_id, session_id):
        """处理用户断开连接"""
        if user_id in self.active_connections:
            if len(list(self.active_connections[user_id].keys())) == 1:
                self.active_connections.pop(user_id, None)
            elif session_id in self.active_connections[user_id]:
                self.active_connections[user_id].pop(session_id, None)
        await self.cache_client.delete(f"room_online_{user_id}")
        logger.info(f"用户 {user_id} 断开连接")
        # await self.msg_pub.publish(WR_TOPIC, orjson.dumps({"type": "system", "message": f"{user.username} 离开作战室"}).decode())

    async def msg_listener(self):
        async with self.cache_client.client.pubsub() as msg_sub:
            await msg_sub.subscribe(WR_TOPIC)
            async for message in msg_sub.listen():
                try:
                    # logger.info(message)
                    if message.get("type") != "message":
                        continue
                    msg = orjson.loads(message["data"])
                    # 处理待办消息
                    if "approval" in msg.keys():
                        for key, values in self.active_connections.items():
                            if key == msg.get("user"):
                                for k, v in values.items():
                                    await v["conn"].send(orjson.dumps(msg.get("approval")).decode())
                        continue
                    # 处理@消息
                    if "mention" in msg.keys():
                        for key, values in self.active_connections.items():
                            if key == msg.get("user"):
                                for k, v in values.items():
                                    await v["conn"].send(orjson.dumps(msg.get("mention")).decode())
                        continue
                    # 处理作战室产生的runlog
                    if "log" in msg.keys():
                        if msg["log"].get("type") == "approval-info":
                            continue
                        message["data"] = orjson.dumps(
                            await creat_message(
                                room_id=msg["room_id"],
                                log=msg["log"],
                                user=msg["updator"],
                                redis=self.redis,
                            )
                        )
                    if msg.get("type") == "ai-reply":
                        message["data"] = orjson.dumps(await creat_ai_message(msg, self.redis))
                        if not message["data"]:
                            continue
                    room_id = msg.get("room_id")
                    for key, values in self.active_connections.items():
                        for k, v in values.items():
                            try:
                                if room_id and v["current_room"] == room_id:
                                    await v["conn"].send(message["data"].decode())
                                elif room_id is None:
                                    await v["conn"].send(message["data"].decode())
                            except WebsocketClosed as e:
                                logger.warning(f"{e} | 用户 {key} 的 {k} 会话的连接已关闭，正在移除连接")
                                await self.disconnect(key, k)
                except orjson.JSONDecodeError:
                    logger.error(f"JSONDecodeError | {message.get('data')}")
                except Exception as e:
                    logger.error(f"{e} | {traceback.format_exc()}")
                    break
            await msg_sub.unsubscribe(WR_TOPIC)

    async def _flush_events(self):
        # 1. 检查是否有数据需要处理
        if not self.event_update_buffer:
            return

        # 2. 取出当前所有待处理数据的快照，但先不清空 buffer
        #    这样如果处理失败，我们可以选择不清除，或者只清除成功的
        #    注意：这里假设 event_update_buffer 的 key 是唯一的，
        #    如果有新数据进来会覆盖旧值（这在业务上是可以接受的，因为只需要最后的状态）
        current_batch = dict(self.event_update_buffer)
        
        # 暂时清空 buffer，防止后续新进来的数据混在一起
        # 如果处理失败，我们需要把这些数据加回去
        self.event_update_buffer.clear()

        batch_size = 500
        event_ids = list(current_batch.keys())
        total_count = len(event_ids)
        
        logger.info(f"开始批量更新事件状态，总数: {total_count}")

        try:
            # 3. 分批处理
            for i in range(0, total_count, batch_size):
                batch_ids = event_ids[i : i + batch_size]
                logger.info(f"正在处理第 {i} 到 {i + len(batch_ids)} 条记录")

                try:
                    # 尝试查询和更新
                    events = await Events.filter(id__in=batch_ids).all()
                    
                    to_update = []
                    found_ids = set()
                    current_time = TZ.localize(datetime.now())
                    
                    for event in events:
                        found_ids.add(event.id)
                        expected_tenant = str(current_batch.get(event.id))
                        actual_tenant = str(event.tenant_id)
                        
                        if actual_tenant == expected_tenant:
                            event.event_process_status = "已处理"
                            event.close_time = current_time
                            to_update.append(event)
                    
                    if to_update:
                        await Events.bulk_update(to_update, fields=["event_process_status", "close_time"])
                        logger.info(f"批次更新成功: {len(to_update)} 条")
                    
                    # 关键逻辑：处理未找到的 ID
                    # 如果某些 ID 在数据库中还没查到（可能是主从延迟或写入滞后），将它们放回 buffer 重试
                    for eid in batch_ids:
                        if eid not in found_ids:
                            logger.warning(f"Event ID {eid} not found in DB, re-queueing for retry")
                            if eid not in self.event_update_buffer:
                                self.event_update_buffer[eid] = current_batch[eid]

                except Exception as batch_error:
                    # 无论是查询失败还是更新失败，都视为该批次处理失败
                    logger.error(f"批次处理失败(将回滚该批次): {batch_error} | {traceback.format_exc()}")
                    
                    # 将该批次的所有 ID 重新放回 buffer
                    # 注意：这里我们放回的是 batch_ids（原始请求的 ID），而不是 to_update（查到的 ID）
                    # 这样可以保证即使是因为查询失败（导致 events 为空），ID 也能被放回去重试
                    for eid in batch_ids:
                        if eid not in self.event_update_buffer:
                            self.event_update_buffer[eid] = current_batch[eid]

        except Exception as e:
            # 这里的异常通常是极其严重的（比如内存错误），恢复所有数据
            logger.error(f"Failed to flush events: {e} | {traceback.format_exc()}")
            for eid, tenant in current_batch.items():
                if eid not in self.event_update_buffer:
                    self.event_update_buffer[eid] = tenant

    async def periodic_flush_task(self):
        logger.info("启动更新事件状态任务")
        while True:
            await asyncio.sleep(2)
            await self._flush_events()

    async def log_listener(self):
        logger.info("启动runlog消费任务")
        await aconsume(RUNLOG_TOPIC, "lion_mgmt_listener", self.handle_runlog, 500)

    async def handle_runlog(self, logs):
        """
        专门处理 runlog
        """
        to_insert = []
        for msg in logs:
            log = msg.value
            logger.info(log)
            # 处理审批消息
            if log.get("type") == "approval-info":
                approve = await Approvement.create(
                    title=log["node_name"],
                    flow_id=log["flow_id"],
                    run_id=log["run_id"],
                    content=log["content"],
                    params=log["params"],
                    status=log["status"],
                    expire_time=log["expire_time"],
                    created_at=log["created_at"],
                    audit_node=log["audit_app"],
                    approve_type=int(log["approve_type"]),
                    callback_token=log.get("callback_token"),
                    tenant_id=log["tenant_id"],
                )
                if log.get("users"):
                    users = await Account.filter(id__in=log["users"].split(",")).all()
                    await approve.approvers.add(*users)
                if log.get("roles"):
                    users = await Account.filter(roles__id__in=log["roles"].split(",")).all()
                    await approve.approvers.add(*users)
                for user in await approve.approvers.all():
                    message_data = {
                        "approval": {
                            "type": "approval-info",
                            "message_id": str(approve.id),
                            "sender_id": log.get("creator_id"),
                            "sender_name": log.get("creator_name"),
                            "sender_display_name": log.get("creator_name"),
                            "content": {
                                "title": approve.title,
                                "approve_type": approve.approve_type,
                                "content": approve.content,
                                "created_at": approve.created_at.astimezone(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                                "expire_time": approve.expire_time.astimezone(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                            },
                            "image_content": "",
                            "timestamp": "",
                            "room_id": "",
                        },
                        "user": user.id,
                    }
                    await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
                pvdata = await PlaybookVersion.get_or_none(id=log["flow_id"])
                for node in pvdata.flow_json["nodes"]:
                    if node["id"] != log["audit_app"]:
                        continue
                    if node["properties"].get("callback") == "on":
                        callback_datas = node["properties"].get("callback_data")
                        for callback_data in callback_datas:
                            data = {}
                            for input_data in callback_data["input"]:
                                data[input_data.get("key")] = input_data.get("value")
                            params = {
                                "resource": callback_data["resource"],
                                "node_name": callback_data["description"],
                                "action": callback_data["func"],
                                "action_name": callback_data["name"],
                                "app_name": callback_data["tool_name"],
                                "name": callback_data["description"],
                                "app_ver": callback_data["version"],
                                "main": callback_data["tool_main"],
                                "action_input": data,
                                "retry_times": int(callback_data["retry_times"]),
                                "timeout": int(callback_data["timeout"]),
                                "executor": log["executor"],
                            }
                            msg = {"run_type": "action", "run_id": log["run_id"], "run_args": params, "tenant_id": log["tenant_id"]}
                            await aproduce(TASK_TOPIC, msg)
            else:
                key = log.get("run_id", "")

                data = await self.redis.get(key)
                if data:
                    # 消费作战室的执行记录
                    data["log"] = log
                    await self.msg_pub.publish(WR_TOPIC, orjson.dumps(data).decode())
                    event = await Events.get_or_none(room_id=data["room_id"], tenant_id=data["tenant_id"])
                    log["event_id"] = event.id if event else None
                if log["event_id"]:
                    if log.get("node_type") == "end-node":
                        self.event_update_buffer[log["event_id"]] = log["tenant_id"]
                        if len(self.event_update_buffer) >= 500:
                            await self._flush_events()                            
                to_insert.append(Runlog(**log))
        try:
            await Runlog.bulk_create(
                to_insert,
                update_fields=["status", "end_time", "result", "utime", "id"],
                on_conflict=["run_id", "node_id", "node_name", "resource_name"],
            )
        except Exception as e:
            logger.info(f"runlog消息：{to_insert}")
            logger.error(f"{e} | {traceback.format_exc()}")

    async def handle_message(self, user: Account, ws, data: dict, session_id, tenant_id):
        """消息处理"""
        try:
            message_type = data.get("type")
            # 按消息类型调用处理方法 (前端发送)
            if message_type in ["public", "runlog"]:
                await self._handle_public_message(user, data, tenant_id)
            elif message_type == "private":
                await self._handle_private_message(user, data, tenant_id)
            elif message_type == "ai":
                await self._handle_ai_message(user, data, tenant_id)
            elif message_type == "audit":
                await self._handle_audit_message(user, data, tenant_id)
            elif message_type == "file":
                await self._handle_file_message(user, data, tenant_id)
            elif message_type == "recall":
                await self._handle_recall_message(user, data, tenant_id)
                await ws.send(orjson.dumps({"type": "recall_success", "message_id": data.get("message_id")}).decode())
            elif message_type == "announcement":  # 发布公告，公告查询在消息查询API接口里
                await self._handle_announcement(user, data, tenant_id)
            elif message_type == "delete_announcement":
                await self._handle_delete_announcement(user, data, tenant_id)
                # 通知删除成功
                await ws.send(
                    orjson.dumps(
                        {
                            "type": "delete_announcement_success",
                            "announcement_id": data.get("announcement_id"),
                        }
                    ).decode()
                )
            elif message_type == "request_online_users":
                online_users = await self._handle_request_online_users(data, tenant_id)
                if user.id in self.active_connections:
                    self.active_connections[user.id][session_id]["current_room"] = data["room_id"]
                # 发送在线状态列表
                await ws.send(
                    orjson.dumps(
                        {
                            "type": "online_users_list",
                            "room_id": data.get("room_id"),
                            "users": online_users,
                        }
                    ).decode()
                )
            elif message_type == "ping":
                await self.cache_client.set(
                    f"room_online_{user.id}",
                    int(time.time()),
                    AUTHENTICATION_SESSION_EXPIRATION,
                )
                await ws.send(orjson.dumps({"type": "pong"}).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await ws.send(
                orjson.dumps(
                    {
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                ).decode()
            )

    async def publish_ai_message(self, data: dict):
        """消息处理"""
        try:
            await self.msg_pub.publish(WR_TOPIC, orjson.dumps(data).decode())
            return None
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            return {"type": "error", "message": str(e)}

    async def _handle_public_message(self, user: Account, data: dict, tenant_id):
        """处理发送群聊消息"""
        current_room_id = data.get("room_id")
        room = await Room.filter(id=current_room_id, tenant_id=tenant_id).get()
        if not room:
            raise Exception("作战室不存在")

        content = data.get("content", "")
        image_content = data.get("image_content", "")
        specify_users = data.get("specify_user", "")

        if not content and not image_content:
            raise Exception("消息不能为空")

        # 保存消息到数据库
        message = await Message.create(type="public", sender=user, room=room, content=content, image_content=image_content, receiver=user, tenant_id=tenant_id)  # 群聊消息设为发送者自己作为占位符

        if specify_users:
            for specify_user in specify_users:
                # 指定用户发送消息
                sf_user = await Account.filter(id=specify_user).first()
                ma = await MessageAccount.create(message=message, account_id=specify_user, tenant_id=tenant_id)
                await sf_user.star_rooms.add(room)
                msg = {
                    "mention": {
                        "type": "mention",
                        "message_id": str(ma.id),
                        "sender_id": user.id,
                        "sender_name": user.username,
                        "sender_display_name": user.display_name,
                        "content": ma.json,
                        "image_content": image_content,
                        "timestamp": str(ma.ctime),
                        "room_id": current_room_id,
                        "tenant_id": tenant_id,
                    },
                    "user": specify_user,
                }
                await self.msg_pub.publish(WR_TOPIC, orjson.dumps(msg).decode())

        # 广播
        message_data = {
            "type": "public",
            "message_id": str(message.id),
            "sender_id": user.id,
            "sender_name": user.username,
            "sender_display_name": user.display_name,
            "content": content,
            "image_content": image_content,
            "timestamp": str(message.ctime),
            "room_id": current_room_id,
            "tenant_id": tenant_id,
        }

        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    async def _handle_ai_message(self, user: Account, data: dict, tenant_id):
        """处理发送ai消息"""
        current_room_id = data.get("room_id")
        room = await Room.filter(id=current_room_id, tenant_id=tenant_id).get()
        if not room:
            raise Exception("作战室不存在")
    
        content = data.get("content", "")
        image_content = data.get("image_content", "")
    
        if not content and not image_content:
            raise Exception("消息不能为空")

        # 保存消息到数据库
        message = await Message.create(
            type="ai",
            sender=user,
            room=room,
            content=content,
            image_content=image_content,
            receiver=user, # 群聊消息设为发送者自己作为占位符
            tenant_id=tenant_id
        )
    
        # 广播
        message_data = {
            "type": "ai",
            "message_id": str(message.id),
            "sender_id": user.id,
            "sender_name": user.username,
            "sender_display_name": user.display_name,
            "content": content,
            "image_content": image_content,
            "timestamp": str(message.ctime),
            "room_id": current_room_id,
            "tenant_id": tenant_id,
        }
    
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    async def _handle_private_message(self, user: Account, data: dict, tenant_id):
        """处理发送私聊消息"""
        try:
            receiver_id = data.get("receiver_id")
            if not receiver_id:
                raise Exception("缺少接收者ID")

            receiver = await Account.get_or_none(id=receiver_id)
            if not receiver:
                raise Exception("接收者不存在")

            content = data.get("content", "")
            image_content = data.get("image_content", "")
            room_id = data.get("room_id")
            chat_room = {}
            # 获取或创建私聊房间
            if not room_id:
                chat_room = await get_or_create_private_chat_room(self.redis, user, receiver, tenant_id)
            
            chat_room["room_id"] = room_id
            chat_room = await Room.get_or_none(id=chat_room["room_id"], tenant_id=tenant_id)

            message = await Message.create(
                type="private",
                sender=user,
                receiver=receiver,
                content=content,
                image_content=image_content,
                room=chat_room,
                tenant_id=tenant_id,
            )

            ma = await MessageAccount.create(message=message, account_id=receiver, tenant_id=tenant_id)

            msg = {
                "mention": {
                    "type": "mention",
                    "message_id": str(ma.id),
                    "sender_id": user.id,
                    "sender_name": user.username,
                    "sender_display_name": user.display_name,
                    "content": ma.json,
                    "image_content": image_content,
                    "timestamp": str(ma.ctime),
                    "room_id": chat_room.id,
                    "tenant_id": tenant_id,
                },
                "user": receiver.id,
            }
            await self.msg_pub.publish(WR_TOPIC, orjson.dumps(msg).decode())

            # 构建消息数据
            message_data = {
                "type": "private",
                "message_id": str(message.id),
                "sender_id": user.id,
                "sender_name": user.username,
                "sender_display_name": user.display_name,
                "receiver_id": receiver_id,
                "receiver_name": receiver.username,
                "content": content,
                "image_content": image_content,
                "timestamp": str(message.ctime),
                "room_id": chat_room.id,
                "tenant_id": tenant_id,
            }
            await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            raise Exception("发送私聊消息失败")

    async def _handle_audit_message(self, user: Account, data: dict, tenant_id):
        """处理发送审批消息"""
        try:
            run_node_ids = data.get("run_node_id", {})
            room_id = data.get("room_id")
            sender = await Account.get(username="system")
            node_ids = set()
            for node_id in run_node_ids.values():
                node_ids.update(node_id.split(",") if "," in node_id else [node_id])
            node_ids = list(node_ids)
            run_logs = await Runlog.filter(run_id__in=list(run_node_ids.keys()), node_id__in=node_ids, tenant_id=tenant_id).all()

            for runlog in run_logs:
                if runlog.status == 3:
                    message_data = {
                        "type": "runlog",
                        "message_id": "",
                        "sender_id": sender.id,
                        "sender_name": sender.username,
                        "sender_display_name": sender.display_name,
                        "content": [runlog.json],
                        "timestamp": "",
                        "room_id": room_id,
                        "tenant_id": tenant_id,
                    }

                    await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            raise Exception("查询超时审批失败")

    async def _handle_file_message(self, user: Account, data: dict, tenant_id):
        """处理发送文件消息"""
        current_room_id = data.get("room_id")
        room = await Room.filter(id=current_room_id, tenant_id=tenant_id).get()
        if not room:
            raise Exception("作战室不存在")

        content = data.get("content", "")
        image_content = data.get("image_content", "")

        if not content and not image_content:
            raise Exception("消息不能为空")

        # 保存消息到数据库
        message = await Message.create(type="file", sender=user, room=room, content=content, image_content=image_content, receiver=user, tenant_id=tenant_id)  # 群聊消息设为发送者自己作为占位符

        # 广播
        message_data = {
            "type": "file",
            "message_id": str(message.id),
            "sender_id": user.id,
            "sender_name": user.username,
            "sender_display_name": user.display_name,
            "content": content,
            "image_content": image_content,
            "timestamp": str(message.ctime),
            "room_id": current_room_id,
            "tenant_id": tenant_id,
        }

        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    async def _handle_recall_message(self, user: Account, data: dict, tenant_id):
        """处理消息撤回"""
        message_id = data.get("message_id")
        if not message_id:
            raise Exception("缺少消息ID")

        message = await Message.filter(id=message_id, tenant_id=tenant_id).first().prefetch_related("room", "sender", "receiver")
        if not message:
            raise Exception("消息不存在")

        now = datetime.now()
        message_time = message.ctime

        # 确保时区一致，将两个时间都转换为无时区信息
        if message_time.tzinfo is not None:
            message_time = message_time.replace(tzinfo=None)

        if (now - message_time).total_seconds() > 600:
            raise Exception("消息已超过10分钟，无法撤回")

        message.is_recalled = True
        await message.save()

        recall_notice = {
            "type": "recall",
            "message_id": str(message.id),
            "sender_id": user.id,
            "room_id": message.room.id,
            "timestamp": str(now),
            "tenant_id": tenant_id,
        }
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(recall_notice).decode())

    async def _handle_announcement(self, user: Account, data: dict, tenant_id):
        """处理发布公告请求"""
        content = data.get("content")
        image_content = data.get("image_content", "")
        room_id = data.get("room_id")
        if not content or not room_id:
            raise Exception("公告内容和作战室ID不能为空")

        room = await Room.get_or_none(id=room_id, tenant_id=tenant_id)
        if not room:
            raise Exception("作战室不存在")
        announcement = await Message.create(type="announcement", sender=user, room=room, content=content, image_content=image_content, is_pinned=True, receiver=user, tenant_id=tenant_id)  # 设公告发送者自己作为占位符

        # 指定房间广播公告
        # await self._broadcast_to_room(room_id, )
        await self.msg_pub.publish(
            WR_TOPIC,
            orjson.dumps(
                {
                    "type": "new_announcement",
                    "id": announcement.id,
                    "content": announcement.content,
                    "image_content": announcement.image_content,
                    "timestamp": str(announcement.ctime),
                    "sender_name": user.username,
                    "sender_id": user.id,
                    "room_id": room_id,
                    "tenant_id": tenant_id,
                }
            ).decode(),
        )

    async def _handle_delete_announcement(self, user: Account, data: dict, tenant_id):
        """处理删除公告请求"""
        announcement_id = data.get("announcement_id")
        if not announcement_id:
            raise Exception("缺少公告ID")

        announcement = await Message.filter(id=announcement_id, type="announcement", tenant_id=tenant_id).first().prefetch_related("sender", "room")
        if not announcement:
            raise Exception("公告不存在")

        if announcement.sender.id != user.id:
            raise Exception("无权删除该公告")

        # 获取房间对象
        announcement.deleted = True
        await announcement.save()

        # 通知房间内所有成员
        msg = {
            "type": "announcement_deleted",
            "announcement_id": announcement_id,
            "timestamp": str(announcement.ctime),
            "sender_name": user.username,
            "sender_id": user.id,
            "room_id": announcement.room.id,
            "tenant_id": tenant_id,
        }
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(msg).decode())

    async def _handle_request_online_users(self, data: dict, tenant_id):
        """处理请求在线用户列表"""
        online_status = []
        try:
            room_id = data.get("room_id")
            if not room_id:
                raise Exception("缺少房间ID")

            # 获取指定房间的所有用户
            room = await Room.get(id=room_id, tenant_id=tenant_id).prefetch_related("users")
            if not room:
                raise Exception("找不到作战室")

            online_users = [u.id for u in room.users]

            async for key in self.msg_pub.scan_iter(match="room_online_*", count=100):
                user_id = key.decode().split("_")[2]
                if user_id in online_users:
                    online_status.append({"user_id": user_id, "online": True})

        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            raise Exception("获取在线用户列表失败")
        return online_status


chat_manager = ChatManager()
