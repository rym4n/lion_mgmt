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

from settings import NANOBOT_CONFIG_PATH


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
        self._has_content = False  # 标记当前流式会话是否有内容

    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """流式输出时发布到 Redis"""
        from settings import WR_TOPIC

        self._has_content = True  # 标记有内容被发送
        message_data = {
            "type": "ai-reply-delta",
            "content": delta,
            "room_id": self.room_id,
            "receiver_id": self.receiver_id,
            "tenant_id": self.tenant_id,
        }
        await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """流式输出结束时发布结束消息

        Args:
            resuming: True 表示后面还有 tool calls 要执行，False 表示真正结束
        """
        # 只有在有内容被发送时才发送结束消息
        # 这样可以确保：
        # 1. 每次 AI 生成文本后都保存消息记录
        # 2. 如果 AI 崩溃，至少之前的文本已经保存
        # 3. 避免创建空的消息记录
        if self._has_content:
            from settings import WR_TOPIC

            message_data = {
                "type": "ai-reply-end",
                "content": "",
                "room_id": self.room_id,
                "receiver_id": self.receiver_id,
                "tenant_id": self.tenant_id,
            }
            await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
            self._has_content = False  # 重置标志

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """在执行工具前发送工具调用信息，让用户知道 AI 正在执行什么操作"""
        from settings import WR_TOPIC

        if not context.tool_calls:
            return

        # 为每个工具调用发送一条消息
        for tool_call in context.tool_calls:
            tool_name = tool_call.name
            tool_args = tool_call.arguments

            # 构造简洁的工具调用描述
            content = f"\n🔧 执行工具: {tool_name}"

            # 简化参数显示
            if tool_args:
                args_parts = []
                for k, v in list(tool_args.items())[:3]:  # 只显示前3个参数
                    if isinstance(v, str):
                        # 字符串参数，截断到50个字符
                        v_str = v[:50] + "..." if len(v) > 50 else v
                        args_parts.append(f"{k}={repr(v_str)}")
                    elif isinstance(v, (list, dict)):
                        # 复杂对象，只显示类型和长度
                        args_parts.append(f"{k}=<{type(v).__name__} len={len(v)}>")
                    else:
                        args_parts.append(f"{k}={v}")

                if len(tool_args) > 3:
                    args_parts.append("...")

                content += f"({', '.join(args_parts)})"
            else:
                content += "()"

            content += "\n"

            message_data = {
                "type": "ai-reply-delta",
                "content": content,
                "room_id": self.room_id,
                "receiver_id": self.receiver_id,
                "tenant_id": self.tenant_id,
            }
            await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())

    async def after_iteration(self, context: AgentHookContext) -> None:
        """在每次迭代后发送工具执行结果"""
        from settings import WR_TOPIC

        # 只有在有工具调用时才发送结果
        if not context.tool_calls:
            return

        # 检查是否有错误
        if context.error:
            content = "❌ 工具执行失败\n"
        else:
            content = "✅ 工具执行完成\n"

        message_data = {
            "type": "ai-reply-delta",
            "content": content,
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

        # 使用全局配置的配置文件路径
        self.config_path = Path(NANOBOT_CONFIG_PATH)

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
        bot = Nanobot.from_config(config_path=self.config_path, workspace=self.workspace)

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
) -> str:
    """
    快速提问（不考虑租户隔离）

    Args:
        message: 问题内容
        streaming: 是否流式输出
        session_key: 会话标识
        workspace: 工作空间路径

    Returns:
        回答内容
    """
    bot = Nanobot.from_config(config_path=NANOBOT_CONFIG_PATH, workspace=workspace)
    hooks = [StreamingHook()] if streaming else None
    result = await bot.run(message, session_key=session_key, hooks=hooks)
    return result.content
