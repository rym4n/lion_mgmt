from uuid import UUID, uuid4

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage
import orjson
from sanic import Blueprint, Request, response
from sanic.log import logger

from services.mcp.server import create_mcp_server

mcp_bp = Blueprint("mcp", url_prefix="/mcp")

# 存储活跃会话
# session_id -> MemoryObjectSendStream
_write_stream_writers = {}


@mcp_bp.get("/sse")
async def handle_sse(request: Request):
    """
    处理MCP的SSE连接。
    """
    session_id = uuid4()

    # 创建流
    # read_stream: 服务器从此读取（客户端通过POST发送到此）
    read_stream_writer, read_stream = anyio.create_memory_object_stream(100)

    # write_stream: 服务器写入此流（通过SSE发送给客户端）
    write_stream, write_stream_reader = anyio.create_memory_object_stream(100)

    _write_stream_writers[session_id] = read_stream_writer

    # 为POST消息构建端点URL
    # 假设服务器在同一主机上可访问
    # 需要完整URL还是绝对路径？
    # MCP规范说明：带有POST到的URI的`endpoint`事件。
    # 如果客户端支持，相对路径也可以，但完整路径更安全。
    # 让使用到/mcp/messages的相对路径
    endpoint_url = f"/mcp/messages?session_id={session_id.hex}"

    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    tenant_id = request.ctx.session.get("tenant_id")

    # 1. 在后台启动MCP服务器
    # 需要运行mcp_server.run(...)
    # 它处理协议逻辑。
    async with anyio.create_task_group() as tg:

        async def run_server():
            # 为该会话创建一个新的服务器实例以确保隔离
            server = create_mcp_server(tenant_id)
            try:
                # 启用无状态模式以允许在初始化完成前接收请求
                # 这将修复客户端立即发送请求时的
                # "在初始化完成前收到请求"警告。
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                    stateless=True,
                )
            except Exception as e:
                logger.error(f"MCP Server Error: {e}")
                # 关闭流？
                try:
                    await resp.eof()
                except:
                    pass

        tg.start_soon(run_server)

        # 等待片刻以确保服务器已准备好接受连接
        await anyio.sleep(0.5)

        # 2. 发送端点事件
        await resp.send(f"event: endpoint\ndata: {endpoint_url}\n\n")

        # 3. 循环遍历write_stream并发送到SSE
        try:
            async for message in write_stream_reader:
                if isinstance(message, Exception):
                    logger.error(f"MCP Error in stream: {message}")
                    break

                # message是SessionMessage
                # 需要序列化它
                data = message.message.model_dump_json(by_alias=True, exclude_none=True)
                await resp.send(f"event: message\ndata: {data}\n\n")
        except Exception as e:
            logger.error(f"SSE Loop Error: {e}")
        finally:
            logger.info(f"Closing session {session_id}")
            if session_id in _write_stream_writers:
                del _write_stream_writers[session_id]
            # 取消服务器任务？tg将等待/取消
            tg.cancel_scope.cancel()
            await resp.eof()


@mcp_bp.post("/messages")
async def handle_messages(request: Request):
    """
    通过POST处理客户端消息(JSON-RPC)。
    """
    session_id_str = request.args.get("session_id")
    if not session_id_str:
        return response.text("Missing session_id", status=400)

    try:
        session_id = UUID(hex=session_id_str)
    except ValueError:
        return response.text("Invalid session_id", status=400)

    writer = _write_stream_writers.get(session_id)
    if not writer:
        return response.text("Session not found", status=404)

    try:
        # 解析请求体
        body = request.json
        # 转换为MCP JSONRPCMessage
        if isinstance(body, str):
            body = orjson.loads(body)

        message = JSONRPCMessage.model_validate(body)

        # 需要构造SessionMessage
        # metadata = ServerMessageMetadata(request_context=request) # Request不可序列化/非标准？
        # 如果允许，让为metadata传递None，或简单字典
        session_message = SessionMessage(message, metadata=None)

        await writer.send(session_message)

        return response.text("Accepted", status=202)

    except Exception as e:
        logger.error(f"消息处理错误: {e}")
        return response.text(f"Error: {str(e)}", status=500)
