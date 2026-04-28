"""Nanobot AI 客户端封装

本模块使用项目内置的 nanobot 源码（位于项目根目录的 nanobot/ 目录）。
不需要安装 nanobot-ai 包，所有依赖已合并到项目的 pyproject.toml 中。

注意：
- nanobot 源码已修改为兼容 websockets 14.1
- 导入会自动使用本地的 nanobot 源码，而不是系统安装的包
"""

import asyncio
import os
from pathlib import Path
import orjson

# 从项目内置的 nanobot 源码导入
from nanobot import Nanobot
from nanobot.agent import AgentHook, AgentHookContext


class StreamingHook(AgentHook):
    """流式输出Hook"""

    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        print(delta, end="", flush=True)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        print()


class RedisStreamingHook(AgentHook):
    """Redis 流式输出 Hook - 将 Nanobot 流式输出桥接到 Redis Pub/Sub"""

    def __init__(self, room_id: str, receiver_id: str, tenant_id: str, redis_pub):
        """
        初始化 Redis 流式 Hook

        Args:
            room_id: 作战室ID
            receiver_id: 接收者ID（用户ID）
            tenant_id: 租户ID
            redis_pub: Redis 发布客户端
        """
        self.room_id = room_id
        self.receiver_id = receiver_id
        self.tenant_id = tenant_id
        self.redis_pub = redis_pub

    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """流式输出时发布到 Redis"""
        from settings import WR_TOPIC

        message_data = {
            "type": "ai-reply-delta",
            "content": delta,
            "room_id": self.room_id,
            "receiver_id": self.receiver_id,
            "tenant_id": self.tenant_id,
        }
        await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """流式输出结束时发布结束消息"""
        from settings import WR_TOPIC

        message_data = {
            "type": "ai-reply-end",
            "content": "",
            "room_id": self.room_id,
            "receiver_id": self.receiver_id,
            "tenant_id": self.tenant_id,
        }
        await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())


class NanobotClient:
    """Nanobot 客户端，支持多租户 workspace 隔离"""

    def __init__(self, tenant_id: str, *, workspace_base: str | Path):
        """
        初始化 Nanobot 客户端

        Args:
            tenant_id: 租户ID
            workspace_base: workspace 基础路径，实际路径为 {workspace_base}/{tenant_id}
        """
        self.tenant_id = tenant_id
        self.workspace = str(Path(workspace_base) / tenant_id)
        # 懒加载：确保 workspace 目录存在
        os.makedirs(self.workspace, exist_ok=True)

    async def ask(
        self,
        message: str,
        *,
        streaming: bool = True,
        session_key: str | None = None,
        hooks: list[AgentHook] | None = None,
    ) -> str:
        """
        向 nanobot 提问

        Args:
            message: 问题内容
            streaming: 是否流式输出
            session_key: 会话标识，默认使用 tenant_id
            hooks: 自定义 hooks，如果为 None 且 streaming=True 则使用默认 StreamingHook

        Returns:
            回答内容
        """
        bot = Nanobot.from_config(workspace=self.workspace)

        # 处理 hooks
        if hooks is None and streaming:
            hooks = [StreamingHook()]

        # 使用租户ID作为默认 session_key
        if session_key is None:
            session_key = f"tenant:{self.tenant_id}"

        # 执行查询
        result = await bot.run(message, session_key=session_key, hooks=hooks)
        return result.content


# 便捷函数：快速提问（不考虑租户隔离）
async def ask(
    message: str,
    *,
    streaming: bool = True,
    session_key: str = "default",
    workspace: str | None = None,
    config_path: str | None = None,
) -> str:
    """
    快速提问（不考虑租户隔离）

    Args:
        message: 问题内容
        streaming: 是否流式输出
        session_key: 会话标识
        workspace: 工作空间路径
        config_path: 配置文件路径

    Returns:
        回答内容
    """
    bot = Nanobot.from_config(config_path=config_path, workspace=workspace)
    hooks = [StreamingHook()] if streaming else None
    result = await bot.run(message, session_key=session_key, hooks=hooks)
    return result.content
