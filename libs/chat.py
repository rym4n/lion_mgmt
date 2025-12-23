# coding=utf-8
import asyncio
import hashlib
import time
import traceback
from datetime import datetime

import httpx
import orjson
import pytz
from aiocache import caches
from sanic.exceptions import WebsocketClosed
from sanic.log import logger
from tortoise.expressions import Q
from tortoise.functions import Max

from libs.kafka import aconsume
from models import Runlog, Approvement, Events, Config, ToolAction, ToolResourcesModel, ToolResources, Playbook, PlaybookVersion
from models.user import Account
from models.warroom import Room, Message, MessageAccount
from settings import KAFKA_SERVERS, CACHE, RUNLOG_TOPIC, WR_TOPIC, AUTHENTICATION_SESSION_EXPIRATION, TZ


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

    payload = {"model": model, "messages": messages, "max_tokens": 4000, "temperature": 0.75}

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(url, headers=headers, json=payload)
            data = response.json()
            return data["choices"][0]["message"]["content"] if data.get("choices") else ""
    except Exception as e:
        logger.error(f"Error on fetch_completion: {e}")
        return ""


# 获取动作推荐列表
async def get_prompt_words(prompt_word):
    tool_names = []
    actions = await ToolResourcesModel.filter(deleted=False, status=0).all().values("tool_name")
    for action in actions:
        if action["tool_name"] not in tool_names:
            tool_names.append(action["tool_name"])
    tools = await ToolResources.filter(deleted=False).all().values("tool_name")
    for tool in tools:
        if tool["tool_name"] not in tool_names:
            tool_names.append(tool["tool_name"])
    if "lion_mgmt" in tool_names:
        tool_names.remove("lion_mgmt")

    latest_versions = await ToolAction.filter(deleted=False, tool_name__in=tool_names).annotate(latest_version=Max("version_number")).group_by("tool_name").values("tool_name", "latest_version")

    pairs = [(row["tool_name"], row["latest_version"]) for row in latest_versions]

    cond = Q()
    for name, ver in pairs:
        cond |= Q(tool_name=name, version_number=ver)

    actions = await ToolAction.filter(deleted=False).filter(cond).order_by("-ctime").all()

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
async def get_playbook_prompt_words(prompt_word):
    playbooks = await Playbook.filter(deleted=False, internal=False).all()

    a = 0
    playbooks_desc = {}
    playbooks_dict = {}
    for playbook in playbooks:
        if await PlaybookVersion.filter(status=1, playbook_id=playbook.id).exists():
            playbooks_desc[str(a)] = {"名称": playbook.name, "描述": playbook.remark}
            a += 1
            pv = await PlaybookVersion.filter(status=1, playbook_id=playbook.id).order_by("-ctime").first()
            playbooks_dict[playbook.name] = pv.id

    logger.info(playbooks_dict)
    logger.info(playbooks_desc)
    for k, v in playbooks_desc.items():
        prompt_word += f"{k}:{v}\n"
    return prompt_word, playbooks_dict, playbooks_desc


# 获取或创建私聊作战室
async def get_or_create_private_chat_room(cache_client, sender: Account, receiver: Account) -> dict:
    # 保证顺序
    key = f"{sender.id}&{receiver.id}"
    private_room_name = f"{sender.display_name}和{receiver.display_name}的私聊"
    if sender.id > receiver.id:
        key = f"{receiver.id}&{sender.id}"
        private_room_name = f"{receiver.display_name}和{sender.display_name}的私聊"
    user_private_chat_rooms = await cache_client.get("user_private_chat_rooms") or {}
    if key not in user_private_chat_rooms:
        # 作战室名字
        room = await Room.get_or_none(name=private_room_name)
        # 先查后创
        if not room:
            room = await Room.create(name=private_room_name, creator=sender)
            await room.users.add(sender)
            await room.users.add(receiver)
        data = {"room_id": room.id, "name": room.name}
        user_private_chat_rooms[key] = data
        logger.info(user_private_chat_rooms)
        await cache_client.set("user_private_chat_rooms", user_private_chat_rooms, 60 * 60 * 24)
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
    if got_lock:
        # 当前是第一个拿到锁的人，创建记录
        msg, _ = await Message.update_or_create(type="runlog", sender=sender, room_id=room_id, content=content, receiver=user, defaults={"image_content": ""})
    else:
        # 其他执行引擎进入这里：稍等一下，再尝试获取已创建的记录
        for i in range(5):
            msg = await Message.get_or_none(content=content, room_id=room_id, receiver=user)
            if msg:
                break
            await asyncio.sleep(0.01 * (i + 1))  # 指数轮询避免数据库压力过大

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


async def get_ai_config():
    records = await Config.filter(module="ai").values("key", "value")
    ai_cfg = {c["key"]: c["value"] for c in records}
    return ai_cfg


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
        self.run_connections: dict[str, dict] = {}

    # 现在使用动作实现ai相关代码注释以防动作代码出问题可以进行对比维护
    #     async def stream_chat_completion(self, prompt: str, message_data: dict):
    #         """
    #         阻碍式对话响应
    #         """
    #         ai_cfg = await get_ai_config()
    #         host = ai_cfg.get('llm_host')
    #         if host is None:
    #             message_data["content"] = "当前未配置ai服务地址，请前往系统设置进行配置。"
    #             await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
    #             return None
    #
    #         api_key = ai_cfg.get('llm_api_key')
    #         model = ai_cfg.get('llm_model')
    #         if model is None:
    #             message_data["content"] = "当前未配置ai服务模型，请前往系统设置进行配置。"
    #             await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
    #             return None
    #         logger.info(host)
    #         logger.info(api_key)
    #         logger.info(model)
    #
    #         system_prompt, actions_dict, actions_desc = await get_prompt_words(ai_cfg["PROMPT_WORD"])
    #         logger.info(system_prompt)
    #         messages = [
    #             {"role": "system", "content": system_prompt},
    #             {"role": "user", "content": "用户输入的是: " + prompt}
    #         ]
    #         full_response = await fetch_completion(host, api_key, model, messages)
    #         if not full_response:
    #             message_data["content"] = "ai请求超时"
    #             await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
    #             return message_data
    #         action_list = []
    #         logger.info(full_response)
    #         if "</think>" in full_response:
    #             match = re.search(r"</think>\s*(.*)", full_response, re.S)
    #             actions = match.group(1).strip()
    #             actions = orjson.loads(actions)
    #         else:
    #             actions = orjson.loads(full_response)
    #         AI = int(ai_cfg.get("AI"))
    #         if AI in [2, 3]:
    #             if "暂无支持的动作" not in str(actions) and "[" in str(actions):
    #                 message_data["type"] = "execute"
    #                 for action in actions:
    #                     logger.info(action)
    #                     data = actions_dict.get(actions_desc.get(action))
    #                     logger.info(data)
    #                     if not data:
    #                         continue
    #
    #                     # 获取动作输入参数列表
    #                     tool_actions = await ToolAction.filter(id__in=data, deleted=False).all()
    #                     for tool_action in tool_actions:
    #                         # 这里处理请求参数的动作
    #                         if tool_action.action["input"]:
    #                             action_inputs = tool_action.action["input"]
    #                             actions_input = ""
    #                             for a_input in action_inputs:
    #                                 actions_input += "- {}:{}\n".format(a_input["key"], a_input["description"])
    #                             prompt_word = """
    # # 角色
    # 请根据动作的参数描述从用户输入中提取合适参数，严格按照JSON格式输出文本，不要用markdown格式。示例：{"key": "value"}。
    #
    # # 动作参数描述
    # """ + actions_input
    #                             logger.info(prompt_word)
    #                             # 再次进行AI生成，进行输入参数值确认
    #                             messages = [
    #                                 {"role": "system", "content": prompt_word},
    #                                 {"role": "user", "content": "用户输入的是: " + prompt}
    #                             ]
    #                             full_response = await fetch_completion(host, api_key, model, messages)
    #                             if not full_response:
    #                                 message_data["content"] = "ai请求超时"
    #                                 await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
    #                                 return message_data
    #                             logger.info(full_response)
    #                             if full_response:
    #                                 if "</think>" in full_response:
    #                                     match = re.search(r"</think>\s*(.*)", full_response, re.S)
    #                                     result = match.group(1).strip()
    #                                     inputs = orjson.loads(result)
    #                                 else:
    #                                     inputs = orjson.loads(full_response)
    #                                 tool = await Tool.get_or_none(name=tool_action.tool_name, version=tool_action.version_number,
    #                                                               deleted=False)
    #                                 if not tool:
    #                                     continue
    #                                 resource = await ToolResourcesModel.get_or_none(tool_name=tool_action.tool_name, deleted=False)
    #                                 action_list.append({
    #                                     "type": "action",
    #                                     "action_input": {
    #                                         "app_name": tool_action.tool_name,
    #                                         "app_ver": tool_action.version_number,
    #                                         "resource": resource.status if resource else 0,
    #                                         "node_name": tool.description,
    #                                         "name": tool.description,
    #                                         "main": tool.main,
    #                                         "action": tool_action.action["func"],
    #                                         "action_name": tool_action.action["name"],
    #                                         "input": tool_action.action["input"],
    #                                         "params": inputs,
    #                                         "timeout": 300
    #                                     }
    #                                 })
    #                         else:  # 这里处理无请求参数的动作
    #                             tool = await Tool.get_or_none(name=tool_action.tool_name, version=tool_action.version_number,
    #                                                           deleted=False)
    #                             if not tool:
    #                                 continue
    #                             resource = await ToolResourcesModel.get_or_none(tool_name=tool_action.tool_name, deleted=False)
    #                             action_list.append({
    #                                 "type": "action",
    #                                 "action_input": {
    #                                     "app_name": tool_action.tool_name,
    #                                     "app_ver": tool_action.version_number,
    #                                     "resource": resource.status if resource else 0,
    #                                     "node_name": tool.description,
    #                                     "name": tool.description,
    #                                     "main": tool.main,
    #                                     "action": tool_action.action["func"],
    #                                     "action_name": tool_action.action["name"],
    #                                     "input": tool_action.action["input"],
    #                                     "params": {},
    #                                     "timeout": 300
    #                                 }
    #                             })
    #                 message_data["content"] = action_list
    #             else:
    #                 message_data["content"] = "暂无支持动作"
    #         elif AI in [1, 3]:
    #             # 这里处理ai问答内容
    #             if "</think>" in full_response:
    #                 match = re.search(r"</think>\s*(.*)", full_response, re.S)
    #                 result = match.group(1).strip()
    #                 message_data["content"] = result
    #             else:
    #                 message_data["content"] = orjson.loads(full_response)
    #         await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
    #
    #         return message_data

    async def connect(self, user: Account, ws):
        """处理用户连接"""
        self.active_connections[user.id] = {"conn": ws, "current_room": None, "run_id": None}
        status_message = {"type": "user_status", "user_id": user.id, "username": user.username, "online": True, "timestamp": str(datetime.now())}
        await self.cache_client.set(f"room_online_{user.id}", int(time.time()), AUTHENTICATION_SESSION_EXPIRATION)
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(status_message).decode())

    async def disconnect(self, user_id):
        """处理用户断开连接"""
        if user_id in self.active_connections:
            self.active_connections.pop(user_id, None)
        if user_id in self.run_connections:
            self.run_connections.pop(user_id, None)
        await self.cache_client.delete(f"room_online_{user_id}")
        logger.info(f"用户 {user_id} 断开连接")
        # await self.msg_pub.publish(WR_TOPIC, orjson.dumps({"type": "system", "message": f"{user.username} 离开作战室"}).decode())

    async def msg_listener(self):
        async with self.cache_client.client.pubsub() as msg_sub:
            await msg_sub.subscribe(WR_TOPIC)
            async for message in msg_sub.listen():
                try:
                    logger.info(message)
                    if message.get("type") != "message":
                        continue
                    msg = orjson.loads(message["data"])
                    # 处理待办消息
                    if "approval" in msg.keys():
                        for k in self.active_connections:
                            if k == msg.get("user"):
                                await self.active_connections[msg.get("user")]["conn"].send(orjson.dumps(msg.get("approval")).decode())
                        continue
                    if "mention" in msg.keys():
                        for k in self.active_connections:
                            if k == msg.get("user"):
                                await self.active_connections[msg.get("user")]["conn"].send(orjson.dumps(msg.get("mention")).decode())
                        continue
                    # 处理作战室产生的runlog
                    if "log" in msg.keys():
                        if msg["log"].get("type") == "approval-info":
                            continue
                        message["data"] = orjson.dumps(await creat_message(room_id=msg["room_id"], log=msg["log"], user=msg["updator"], redis=self.redis))
                    room_id = msg.get("room_id")
                    for k, v in self.active_connections.items():
                        if room_id and v["current_room"] == room_id:
                            await v["conn"].send(message["data"].decode())
                        elif room_id is None:
                            await v["conn"].send(message["data"].decode())
                except orjson.JSONDecodeError:
                    logger.error(f"JSONDecodeError | {message.get('data')}")
                except WebsocketClosed:
                    await self.disconnect(k)
                except Exception as e:
                    logger.error(f"{e} | {traceback.format_exc()}")
                    break
            await msg_sub.unsubscribe(WR_TOPIC)

    async def log_listener(self):
        await aconsume(KAFKA_SERVERS, RUNLOG_TOPIC, "lion_mgmt_listener", self.handle_runlog, 500)

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
            else:
                key = log.get("run_id", "")

                data = await self.redis.get(key)
                if data:
                    # 消费作战室的执行记录
                    data["log"] = log
                    await self.msg_pub.publish(WR_TOPIC, orjson.dumps(data).decode())
                    event = await Events.get_or_none(room_id=data["room_id"])
                    if event:
                        if log.get("node_type") == "start-node":
                            event.event_process_status = "处理中"
                            await event.save()
                        elif log.get("node_type") == "end-node":
                            event.event_process_status = "已处理"
                            event.close_time = TZ.localize(datetime.now())
                            await event.save()
                        log["event_id"] = event.id
                to_insert.append(Runlog(**log))
        try:
            await Runlog.bulk_create(to_insert, update_fields=["status", "end_time", "result", "utime", "id"], on_conflict=["run_id", "node_id", "node_name", "resource_name"])
        except Exception as e:
            logger.info(f"runlog消息：{to_insert}")
            logger.error(f"{e} | {traceback.format_exc()}")

    async def handle_message(self, user: Account, ws, data: dict):
        """消息处理"""
        try:
            message_type = data.get("type")
            # 按消息类型调用处理方法 (前端发送)
            if message_type in ["public", "runlog"]:
                await self._handle_public_message(user, data)
            elif message_type == "private":
                await self._handle_private_message(user, data)
            elif message_type == "audit":
                await self._handle_audit_message(user, data)
            elif message_type == "recall":
                await self._handle_recall_message(user, data)
                await ws.send(orjson.dumps({"type": "recall_success", "message_id": data.get("message_id")}).decode())
            elif message_type == "announcement":  # 发布公告，公告查询在消息查询API接口里
                await self._handle_announcement(user, data)
            elif message_type == "delete_announcement":
                await self._handle_delete_announcement(user, data)
                # 通知删除成功
                await ws.send(orjson.dumps({"type": "delete_announcement_success", "announcement_id": data.get("announcement_id")}).decode())
            elif message_type == "request_online_users":
                online_users = await self._handle_request_online_users(data)
                if user.id in self.active_connections:
                    self.active_connections[user.id]["current_room"] = data["room_id"]
                # 发送在线状态列表
                await ws.send(orjson.dumps({"type": "online_users_list", "room_id": data.get("room_id"), "users": online_users}).decode())
            elif message_type == "ping":
                await self.cache_client.set(f"room_online_{user.id}", int(time.time()), AUTHENTICATION_SESSION_EXPIRATION)
                await ws.send(orjson.dumps({"type": "pong"}).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            await ws.send(orjson.dumps({"type": "error", "message": str(e), "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}).decode())

    async def publish_ai_message(self, data: dict):
        """消息处理"""
        try:
            await self.msg_pub.publish(WR_TOPIC, orjson.dumps(data).decode())
            return None
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            return {"type": "error", "message": str(e)}

    async def _handle_public_message(self, user: Account, data: dict):
        """处理发送群聊消息"""
        current_room_id = data.get("room_id")
        room = await Room.filter(id=current_room_id).get()
        if not room:
            raise Exception("作战室不存在")

        content = data.get("content", "")
        image_content = data.get("image_content", "")
        specify_users = data.get("specify_user", "")

        if not content and not image_content:
            raise Exception("消息不能为空")

        # 保存消息到数据库
        message = await Message.create(type="public", sender=user, room=room, content=content, image_content=image_content, receiver=user)  # 群聊消息设为发送者自己作为占位符

        if specify_users:
            for specify_user in specify_users:
                # 指定用户发送消息
                sf_user = await Account.filter(id=specify_user).first()
                ma = await MessageAccount.create(message=message, account_id=specify_user)
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
        }

        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    # 现在使用动作实现ai相关代码注释以防动作代码出问题可以进行对比维护
    # async def _handle_ai_message(self, user: Account, data: dict):
    #     """处理发送ai消息"""
    #     current_room_id = data.get("room_id")
    #     room = await Room.filter(id=current_room_id).get()
    #     if not room:
    #         raise Exception("作战室不存在")
    #
    #     content = data.get("content", "")
    #     image_content = data.get("image_content", "")
    #
    #     if not content and not image_content:
    #         raise Exception("消息不能为空")
    #     sender = await Account.get(username="system")
    #     # 保存消息到数据库
    #     message = await Message.create(
    #         type="ai-question",
    #         sender=user,
    #         room=room,
    #         content=content,
    #         image_content=image_content,
    #         receiver=user  # 群聊消息设为发送者自己作为占位符
    #     )
    #
    #     # 广播
    #     message_data = {
    #         "type": "ai-question",
    #         "message_id": str(message.id),
    #         "sender_id": user.id,
    #         "sender_name": user.username,
    #         "sender_display_name": user.display_name,
    #         "content": content,
    #         "image_content": image_content,
    #         "timestamp": str(message.ctime),
    #         "room_id": current_room_id
    #     }
    #
    #     await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
    #     # 在发送完用户消息后，切换系统回复AI消息
    #     message_data["sender_id"] = sender.id
    #     message_data["sender_name"] = sender.username
    #     message_data["type"] = "ai"
    #     message_data = await self.stream_chat_completion(content, message_data)
    #
    #     if message_data.get("content"):
    #         await Message.create(
    #             type=message_data.get("type"),
    #             sender=sender,
    #             room=room,
    #             content=message_data.get("content"),
    #             image_content=image_content,
    #             receiver=user  # 群聊消息设为发送者自己作为占位符
    #         )

    async def _handle_private_message(self, user: Account, data: dict):
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
            specify_users = data.get("specify_user", "")

            # 获取或创建私聊房间
            chat_room = await get_or_create_private_chat_room(self.redis, user, receiver)

            chat_room = await Room.get_or_none(id=chat_room["room_id"])

            message = await Message.create(type="private", sender=user, receiver=receiver, content=content, image_content=image_content, room=chat_room)

            if specify_users:
                # 指定用户发送消息
                for specify_user in specify_users:
                    await MessageAccount.create(message=message, account_id=specify_user)

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
            }
            await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            raise Exception("发送私聊消息失败")

    async def _handle_audit_message(self, user: Account, data: dict):
        """处理发送私聊消息"""
        try:
            run_node_ids = data.get("run_node_id", {})
            room_id = data.get("room_id")
            sender = await Account.get(username="system")
            run_logs = await Runlog.filter(run_id__in=list(run_node_ids.keys()), node_id__in=list(run_node_ids.values())).all()

            for runlog in run_logs:
                message_data = {
                    "type": "runlog",
                    "message_id": "",
                    "sender_id": sender.id,
                    "sender_name": sender.username,
                    "sender_display_name": sender.display_name,
                    "content": [runlog.json],
                    "timestamp": "",
                    "room_id": room_id,
                }

                await self.msg_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
        except Exception as e:
            logger.error(f"{e} | {traceback.format_exc()}")
            raise Exception("查询超时审批失败")

    async def _handle_recall_message(self, user: Account, data: dict):
        """处理消息撤回"""
        message_id = data.get("message_id")
        if not message_id:
            raise Exception("缺少消息ID")

        message = await Message.filter(id=message_id).first().prefetch_related("room", "sender", "receiver")
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

        recall_notice = {"type": "recall", "message_id": str(message.id), "sender_id": user.id, "room_id": message.room.id, "timestamp": str(now)}
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(recall_notice).decode())

    async def _handle_announcement(self, user: Account, data: dict):
        """处理发布公告请求"""
        content = data.get("content")
        image_content = data.get("image_content", "")
        room_id = data.get("room_id")
        if not content or not room_id:
            raise Exception("公告内容和作战室ID不能为空")

        room = await Room.get_or_none(id=room_id)
        if not room:
            raise Exception("作战室不存在")
        announcement = await Message.create(type="announcement", sender=user, room=room, content=content, image_content=image_content, is_pinned=True, receiver=user)  # 设公告发送者自己作为占位符

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
                }
            ).decode(),
        )

    async def _handle_delete_announcement(self, user: Account, data: dict):
        """处理删除公告请求"""
        announcement_id = data.get("announcement_id")
        if not announcement_id:
            raise Exception("缺少公告ID")

        announcement = await Message.filter(id=announcement_id, type="announcement").first().prefetch_related("sender", "room")
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
        }
        await self.msg_pub.publish(WR_TOPIC, orjson.dumps(msg).decode())

    async def _handle_request_online_users(self, data: dict):
        """处理请求在线用户列表"""
        online_status = []
        try:
            room_id = data.get("room_id")
            if not room_id:
                raise Exception("缺少房间ID")

            # 获取指定房间的所有用户
            room = await Room.get(id=room_id).prefetch_related("users")
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
