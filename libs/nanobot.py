"""Nanobot AI 客户端封装

通过 pip 包 `nanobot-ai` 引用 nanobot 运行时。
"""

import asyncio
import os
import shutil
import orjson

from pathlib import Path
from sanic.log import logger

from nanobot import Nanobot
from nanobot.agent import AgentHook, AgentHookContext

# 项目根目录（libs 的上一级目录）
PROJECT_ROOT = Path(__file__).parent.parent
# config.json 的绝对路径
CONFIG_PATH = PROJECT_ROOT / "config.json"


# 工具名称到中文描述的映射
TOOL_DESCRIPTIONS = {
    "read_file": "正在读取文件",
    "write_file": "正在写入文件",
    "list_files": "正在列出文件",
    "execute_command": "正在执行命令",
    "search_files": "正在搜索文件",
    "get_url": "正在获取网页内容",
    "create_directory": "正在创建目录",
    "delete_file": "正在删除文件",
    "move_file": "正在移动文件",
    "copy_file": "正在复制文件",
    "run_python": "正在执行 Python 代码",
    "run_shell": "正在执行 Shell 命令",
    "search_code": "正在搜索代码",
    "analyze_file": "正在分析文件",
    "get_file_info": "正在获取文件信息",
}


def _generate_tool_description(tool_name: str, tool_args: dict) -> str:
    """生成工具的友好描述

    Args:
        tool_name: 工具名称
        tool_args: 工具参数

    Returns:
        友好的中文描述
    """
    # 如果有预定义的描述，直接使用
    if tool_name in TOOL_DESCRIPTIONS:
        return TOOL_DESCRIPTIONS[tool_name]

    # 对于通用的执行类工具，尝试从参数中提取操作类型
    if tool_name in ("exec", "execute", "run"):
        if not tool_args:
            return "正在执行操作"

        # 根据参数类型判断操作类型
        if "command" in tool_args or "cmd" in tool_args or "shell" in tool_args:
            return "正在执行 Shell 命令"
        elif "code" in tool_args or "python_code" in tool_args or "python" in tool_args:
            return "正在执行 Python 代码"
        elif "script" in tool_args or "script_path" in tool_args:
            return "正在执行脚本"
        elif "sql" in tool_args or "query" in tool_args:
            return "正在执行数据库查询"
        else:
            return "正在执行操作"

    # 默认使用通用格式
    return f"正在执行 {tool_name}"


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

    def __init__(self, room_id: str, receiver_id: str, tenant_id: str, redis_pub, stream_id: str | None = None):
        """
        初始化 Redis 流式 Hook

        Args:
            room_id: 作战室ID
            receiver_id: 接收者ID（用户ID）
            tenant_id: 租户ID
            redis_pub: Redis 发布客户端
            stream_id: 单次 AI 请求的唯一标识，用于避免同一房间/用户的并发回复串流
        """
        self.room_id = room_id
        self.receiver_id = receiver_id
        self.tenant_id = tenant_id
        self.redis_pub = redis_pub
        self.stream_id = stream_id
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
        if self.stream_id:
            message_data["stream_id"] = self.stream_id
        try:
            await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
        except Exception as e:
            # 记录错误但不中断流程
            logger.info(f"[RedisStreamingHook] 发布流式消息失败: {e}")

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """流式输出结束时发布结束消息

        Args:
            resuming: True 表示后面还有 tool calls 要执行，False 表示真正结束
        """
        # Nanobot 的 on_stream_end(resuming=True) 只表示当前文本片段结束，
        # 后面还会继续执行工具或继续生成；这时不能向前端发送 ai-reply-end，
        # 否则前端会提前结束展示，ChatManager 也会提前 pop 缓冲并保存半截内容。
        if resuming:
            return

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
            if self.stream_id:
                message_data["stream_id"] = self.stream_id
            try:
                await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
            except Exception as e:
                # 记录错误但不中断流程
                logger.info(f"[RedisStreamingHook] 发布结束消息失败: {e}")
            finally:
                self._has_content = False  # 重置标志

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """在执行工具前发送工具调用信息，让用户知道 AI 正在执行什么操作"""
        from settings import WR_TOPIC

        if not context.tool_calls:
            return

        # 为每个工具调用发送一条"思考中"消息
        for tool_call in context.tool_calls:
            tool_name = tool_call.name
            tool_args = tool_call.arguments or {}

            # 生成工具的友好描述
            description = _generate_tool_description(tool_name, tool_args)

            message_data = {
                "type": "ai-thinking",
                "content": description,
                "tool_name": tool_name,
                "room_id": self.room_id,
                "receiver_id": self.receiver_id,
                "tenant_id": self.tenant_id,
            }
            if self.stream_id:
                message_data["stream_id"] = self.stream_id
            try:
                await self.redis_pub.publish(WR_TOPIC, orjson.dumps(message_data).decode())
            except Exception as e:
                # 记录错误但不中断流程
                logger.info(f"[RedisStreamingHook] 发布思考中消息失败: {e}")

    async def after_iteration(self, context: AgentHookContext) -> None:
        """在每次迭代后的回调（工具执行完成后）"""
        # 不需要发送任何消息，工具执行状态由 before_execute_tools 已经通知
        pass


class NanobotClient:
    """Nanobot 客户端，支持会话隔离"""

    def __init__(self, session_id: str, *, workspace: str | Path):
        """
        初始化 Nanobot 客户端

        Args:
            session_id: 会话ID（用于区分不同的对话会话）
            workspace: workspace 路径
        """
        self.session_id = session_id
        self.workspace = str(workspace)
        workspace_path = Path(self.workspace)

        # 如果 workspace 不存在，从模板复制
        if not workspace_path.exists():
            template_path = PROJECT_ROOT / "workspace-template"
            if template_path.exists():
                shutil.copytree(template_path, workspace_path, dirs_exist_ok=True)
                logger.info(f"[NanobotClient] 已从模板创建 workspace: {workspace_path}")

        # 使用全局配置的配置文件路径
        self.config_path = CONFIG_PATH

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
            session_key: 会话标识，默认使用 session_id
            hooks: 自定义 hooks，如果为 None 且 streaming=True 则使用默认 StreamingHook

        Returns:
            回答内容
        """
        bot = Nanobot.from_config(config_path=self.config_path, workspace=self.workspace)

        # 处理 hooks
        if hooks is None and streaming:
            hooks = [StreamingHook()]

        # 使用会话ID作为默认 session_key
        if session_key is None:
            session_key = self.session_id

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
        workspace: 租户路径

    Returns:
        回答内容
    """
    bot = Nanobot.from_config(config_path=CONFIG_PATH, workspace=workspace)
    hooks = [StreamingHook()] if streaming else None
    result = await bot.run(message, session_key=session_key, hooks=hooks)
    return result.content
