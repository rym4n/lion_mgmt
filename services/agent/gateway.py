# coding=utf-8
import asyncio
from contextlib import asynccontextmanager
import time
from typing import Any, AsyncIterator, Optional

import httpx
import orjson
from sanic.log import logger

from models.agent import AgentMapping

from .prompts import INITIAL_PERSONA_BLOCK, build_members_roster, build_system_prompt
from .tools import register_tools_to_agent_server


class AgentGateway:
    """
    Agent 网关 - 管理 Warroom 到 Agent 的映射
    使用 httpx 直接调用 Agent Server API
    """

    # Lua 脚本：原子性检查值并删除
    UNLOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, agent_server_url: str, redis_client):
        self.base_url = agent_server_url.rstrip("/")
        self.redis = redis_client
        self._cache_prefix = "secops:agent_map:"
        self._lock_prefix = "secops:lock:"
        self._lock_timeout = 30

        # 预加载 Lua 脚本
        self._unlock_script_sha = None

        # Shared httpx client (optional, but good for connection pooling)
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def _init_scripts(self):
        """初始化 Lua 脚本 (在 listener 启动时调用)"""
        try:
            self._unlock_script_sha = await self.redis.script_load(self.UNLOCK_SCRIPT)
        except Exception as e:
            logger.error(f"Failed to load Lua script: {e}")

    @asynccontextmanager
    async def _distributed_lock(self, warroom_id: str):
        """
        分布式锁 - 防止并发创建多个 Agent
        """
        lock_key = f"{self._lock_prefix}{warroom_id}"
        task = asyncio.current_task()
        task_name = task.get_name() if task else "unknown"
        lock_value = f"{task_name}:{time.time()}"

        acquired = await self.redis.set(lock_key, lock_value, nx=True, ex=self._lock_timeout)  # 仅当 key 不存在时设置

        if not acquired:
            # 等待锁释放
            retry_delay = 0.05
            max_delay = 1.0

            for _ in range(int(self._lock_timeout / retry_delay)):
                await asyncio.sleep(retry_delay)
                acquired = await self.redis.set(lock_key, lock_value, nx=True, ex=self._lock_timeout)
                if acquired:
                    break
                # 指数退避
                retry_delay = min(retry_delay * 1.5, max_delay)
            else:
                raise TimeoutError(f"Failed to acquire lock for warroom {warroom_id}")

        try:
            yield
        finally:
            # 释放锁
            try:
                if self._unlock_script_sha:
                    await self.redis.evalsha(self._unlock_script_sha, 1, lock_key, lock_value)
                else:
                    await self.redis.eval(self.UNLOCK_SCRIPT, 1, lock_key, lock_value)
            except Exception as e:
                logger.error(f"Failed to release lock for {warroom_id}: {e}")

    async def get_or_create_agent(self, warroom_id: str, tenant_id: str, members: list[dict]) -> str:
        """
        获取作战室对应的 Agent，不存在则创建
        """
        agent_id = await self._get_agent_id(warroom_id)
        if agent_id:
            return agent_id

        # 创建新 Agent (加锁)
        async with self._distributed_lock(warroom_id):
            # Double check
            agent_id = await self._get_agent_id(warroom_id)
            if agent_id:
                return agent_id

            agent_id = await self._create_agent(warroom_id=warroom_id, tenant_id=tenant_id, members=members)
            await self._set_agent_id(warroom_id, agent_id)
            return agent_id

    async def send_message(self, warroom_id: str, user: dict, content: str) -> dict:
        """
        发送消息到 Agent
        """
        agent_id = await self._get_agent_id(warroom_id)
        if not agent_id:
            raise ValueError(f"No agent found for warroom {warroom_id}")

        formatted_msg = f"[User: {user['display_name']} ({user['role']})] {content}"

        # 调用 Agent Server API: POST /v1/agents/{agent_id}/messages
        response = await self.client.post(f"/v1/agents/{agent_id}/messages", json={"messages": [{"role": "user", "content": formatted_msg}]})
        response.raise_for_status()
        resp_data = response.json()

        # 获取最新状态
        agent_state = await self._get_agent_state(agent_id)
        l1_memory = self._parse_core_memory(agent_state.get("memory", {}))

        return {"response": self._extract_assistant_reply(resp_data), "l1_state": l1_memory, "tool_calls": self._extract_tool_calls(resp_data)}

    async def send_message_stream(self, warroom_id: str, user: dict, content: str) -> AsyncIterator[dict]:
        """
        流式发送消息到 Agent
        """
        agent_id = await self._get_agent_id(warroom_id)
        if not agent_id:
            raise ValueError(f"No agent found for warroom {warroom_id}")

        formatted_msg = f"[User: {user['display_name']} ({user['role']})] {content}"

        # Agent Server Streaming API: POST /v1/agents/{agent_id}/messages/stream
        async with self.client.stream("POST", f"/v1/agents/{agent_id}/messages/stream", json={"messages": [{"role": "user", "content": formatted_msg}]}) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]  # Strip "data: "
                if data_str == "[DONE]":
                    break

                try:
                    chunk = orjson.loads(data_str)

                    if chunk.get("message_type") == "assistant_message":
                        yield {"type": "message", "data": {"text": chunk.get("content", "")}}
                    elif chunk.get("message_type") == "function_call":
                        # Simplistic handling
                        yield {"type": "tool_call", "data": {"name": chunk.get("function_call", {}).get("name"), "arguments": chunk.get("function_call", {}).get("arguments")}}
                except orjson.JSONDecodeError:
                    continue

        # 对话结束，获取最新 L1
        agent_state = await self._get_agent_state(agent_id)
        l1_memory = self._parse_core_memory(agent_state.get("memory", {}))

        yield {"type": "l1_update", "data": l1_memory}

    async def archive_agent(self, warroom_id: str, trigger_reflection: bool = True) -> dict:
        """
        作战室归档时：触发反思 -> 销毁 Agent
        """
        agent_id = await self._get_agent_id(warroom_id)
        if not agent_id:
            return {"status": "no_agent"}

        reflection_result = None
        if trigger_reflection:
            reflection_result = await self._trigger_reflection(agent_id)

        # 删除 Agent: DELETE /v1/agents/{agent_id}
        try:
            await self.client.delete(f"/v1/agents/{agent_id}")
        except Exception as e:
            logger.warning(f"Failed to delete agent {agent_id}: {e}")

        await self._delete_agent_id(warroom_id)

        return {"status": "archived", "reflection": reflection_result}

    async def get_agent_l1_state(self, agent_id: str) -> Optional[dict]:
        try:
            agent_state = await self._get_agent_state(agent_id)
            return self._parse_core_memory(agent_state.get("memory", {}))
        except Exception:
            return None

    # --- Internal Helpers ---

    async def _get_agent_state(self, agent_id: str) -> dict:
        response = await self.client.get(f"/v1/agents/{agent_id}")
        response.raise_for_status()
        return response.json()

    async def _get_agent_id(self, warroom_id: str) -> Optional[str]:
        cache_key = f"{self._cache_prefix}{warroom_id}"
        agent_id = await self.redis.get(cache_key)
        if agent_id:
            return agent_id.decode() if isinstance(agent_id, bytes) else agent_id

        mapping = await AgentMapping.get_or_none(warroom_id=warroom_id, deleted=False)
        if mapping:
            await self.redis.set(cache_key, mapping.agent_id)
            return mapping.agent_id
        return None

    async def _set_agent_id(self, warroom_id: str, agent_id: str):
        mapping, _ = await AgentMapping.get_or_create(warroom_id=warroom_id, defaults={"tenant_id": "system", "agent_id": agent_id})
        mapping.agent_id = agent_id
        mapping.status = "active"
        await mapping.save()

        cache_key = f"{self._cache_prefix}{warroom_id}"
        await self.redis.set(cache_key, agent_id)

    async def _delete_agent_id(self, warroom_id: str):
        mapping = await AgentMapping.get_or_none(warroom_id=warroom_id, deleted=False)
        if mapping:
            mapping.status = "archived"
            await mapping.save()

        cache_key = f"{self._cache_prefix}{warroom_id}"
        await self.redis.delete(cache_key)

    async def _create_agent(self, warroom_id: str, tenant_id: str, members: list[dict]) -> str:
        """创建新 Agent 实例"""

        system_prompt = build_system_prompt(tenant_id)

        human_block = build_members_roster(members)

        await register_tools_to_agent_server(self.client, tenant_id)

        human_text = orjson.dumps(human_block).decode()
        persona_text = orjson.dumps(INITIAL_PERSONA_BLOCK).decode()

        tool_names = ["call_playbook", "list_available_playbooks", "submit_case_learning"]

        payload = {
            "name": f"secops_{warroom_id[:8]}",
            "system": system_prompt,
            "memory": {
                "blocks": [
                    {"label": "human", "value": human_text},
                    {"label": "persona", "value": persona_text},
                ]
            },
            "tools": tool_names,
        }

        response = await self.client.post("/v1/agents/", json=payload)
        if response.is_error:
            logger.error(f"Agent creation failed: {response.text}")
        response.raise_for_status()
        agent_data = response.json()
        agent_id = agent_data["id"]

        await AgentMapping.create(warroom_id=warroom_id, agent_id=agent_id, tenant_id=tenant_id)

        return agent_id

    async def _trigger_reflection(self, agent_id: str) -> list:
        """触发 Agent 结案反思"""
        reflection_prompt = """
        作战室即将归档。请回顾整个案件处理过程，调用 submit_case_learning 工具提交：
        1. 案例总结
        2. 攻击类型分类
        3. 关键 IOC 指标
        4. 经验教训
        5. 是否为误报
        """
        response = await self.client.post(f"/v1/agents/{agent_id}/messages", json={"messages": [{"role": "user", "content": reflection_prompt}]})
        resp_data = response.json()
        return self._extract_tool_calls(resp_data)

    def _parse_core_memory(self, memory_obj: Any) -> dict:
        """Parse Agent memory dict to dict"""
        # Agent Server API returns memory as dict with blocks list or directly blocks
        try:
            # Check structure of memory object from API
            # It usually has 'blocks' list
            blocks = memory_obj.get("blocks", [])
            for block in blocks:
                if block.get("label") == "persona":
                    persona_text = block.get("value", "")
                    return orjson.loads(persona_text)

            # Or legacy structure?
            if "persona" in memory_obj:
                return orjson.loads(memory_obj["persona"])

            return {}
        except Exception:
            return {}

    def _extract_assistant_reply(self, response_data: dict) -> str:
        """Extract text reply from Agent Server response dict"""
        try:
            messages = response_data.get("messages", [])
            # Look for assistant message
            for msg in reversed(messages):
                if msg.get("message_type") == "assistant_message":
                    return msg.get("content", "")
            return ""
        except Exception:
            return ""

    def _extract_tool_calls(self, response_data: dict) -> list:
        """Extract tool calls from response dict"""
        tool_calls = []
        try:
            messages = response_data.get("messages", [])
            for msg in messages:
                if msg.get("message_type") == "function_call":
                    fc = msg.get("function_call", {})
                    tool_calls.append({"name": fc.get("name"), "arguments": fc.get("arguments")})
        except Exception:
            pass
        return tool_calls
