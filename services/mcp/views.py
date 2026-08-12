# coding=utf-8

import asyncio
import time
from uuid import UUID, uuid4
import traceback
import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage
import orjson
from sanic import Blueprint, Request, response
from sanic.log import logger

from services.mcp.server import create_mcp_server

mcp_bp = Blueprint("mcp", url_prefix="/mcp")

# Session 宽限期（秒）：SSE 断开后 Redis 键保留时间，任意 worker 仍可对 POST 返回 202
MCP_SESSION_GRACE_SECONDS = 60

# Redis 键/频道前缀（多进程下用 Redis 做 session 存在性 + Pub/Sub 转发消息）
MCP_REDIS_KEY_PREFIX = "mcp:session:"
MCP_REDIS_CHAN_PREFIX = "mcp:channel:"

# 本进程内活跃会话（仅持有 SSE 的 worker 使用）
# session_id -> {"writer": MemoryObjectSendStream, "closed_at": float | None}
_write_stream_writers = {}


def _wants_sse(request: Request) -> bool:
    """检查客户端是否请求 SSE 传输模式。"""
    accept = request.headers.get("Accept", "")
    # SSE 模式：GET 请求或明确请求 text/event-stream
    if request.method == "GET":
        return True
    if "text/event-stream" in accept:
        return True
    return False


def _get_redis_client(request: Request):
    """获取 Redis 客户端（用于 MCP session 键与 pub/sub）。"""
    cache = getattr(request.app.ctx, "cache_client", None)
    if cache is None or not hasattr(cache, "client"):
        return None
    return cache.client


async def _remove_session_after_grace(session_id: UUID, delay: float) -> None:
    """宽限期结束后从本进程存储中移除 session，避免内存泄漏。"""
    await asyncio.sleep(delay)
    if session_id in _write_stream_writers:
        del _write_stream_writers[session_id]
        logger.info(f"MCP session {session_id} removed after grace period")


@mcp_bp.route("/sse", methods=["GET", "POST"])
async def handle_sse(request: Request):
    """
    处理MCP连接，支持两种传输模式：
    1. Streamable HTTP（默认）：POST 请求直接返回 JSON 响应
    2. SSE：GET 请求或 Accept: text/event-stream 的 POST 请求使用流式传输

    多进程下：用 Redis 键标记 session 存在，用 Redis Pub/Sub 将 POST 消息转发到本 worker 的流。
    """
    # 根据请求类型选择传输模式
    if not _wants_sse(request):
        return await _handle_streamable_http(request)

    # SSE 模式
    return await _handle_sse_mode(request)


async def _handle_streamable_http(request: Request):
    """
    Streamable HTTP 模式：处理单个 JSON-RPC 请求并返回 JSON 响应。
    支持无状态请求处理，每个请求独立处理。
    """
    tenant_id = request.ctx.session.get("tenant_id")
    body = request.json

    if not body:
        return response.json(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status=400,
        )

    # 检查是否有 session_id（用于有状态会话）
    session_id_str = request.args.get("session_id")

    if session_id_str:
        # 有状态模式：尝试使用现有会话
        try:
            session_id = UUID(hex=session_id_str)
        except ValueError:
            return response.json(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid session_id"}, "id": body.get("id")},
                status=400,
            )

        sid_hex = session_id.hex
        body_raw = orjson.dumps(body).decode("utf-8") if not isinstance(body, str) else body

        # 尝试本进程内会话
        entry = _write_stream_writers.get(session_id)
        if entry:
            closed_at = entry.get("closed_at")
            if closed_at is not None and (time.monotonic() - closed_at) > MCP_SESSION_GRACE_SECONDS:
                del _write_stream_writers[session_id]
            else:
                try:
                    body_obj = orjson.loads(body_raw) if isinstance(body_raw, str) else body_raw
                    message = JSONRPCMessage.model_validate(body_obj)
                    session_message = SessionMessage(message, metadata=None)
                    await entry["writer"].send(session_message)
                    return response.text("Accepted", status=202)
                except Exception as e:
                    logger.error(f"Streamable HTTP session send error: {e}")
                    return response.json(
                        {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": body.get("id")},
                        status=500,
                    )

        # 尝试 Redis 转发
        redis_client = _get_redis_client(request)
        if redis_client:
            try:
                key = f"{MCP_REDIS_KEY_PREFIX}{sid_hex}"
                exists = await redis_client.exists(key)
                if exists:
                    channel = f"{MCP_REDIS_CHAN_PREFIX}{sid_hex}"
                    await redis_client.publish(channel, body_raw)
                    return response.text("Accepted", status=202)
            except Exception as e:
                logger.error(f"MCP Redis publish: {e}")
            finally:
                await redis_client.close()

        return response.json(
            {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Session not found"}, "id": body.get("id")},
            status=404,
        )

    # 无状态模式：为每个请求创建临时服务器实例处理
    read_stream_writer, read_stream = anyio.create_memory_object_stream(10)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(10)

    try:
        # 解析并发送请求消息
        message = JSONRPCMessage.model_validate(body)
        session_message = SessionMessage(message, metadata=None)
        await read_stream_writer.send(session_message)
        await read_stream_writer.aclose()

        # 创建服务器并处理
        server = create_mcp_server(tenant_id)
        responses = []

        async with anyio.create_task_group() as tg:

            async def run_server():
                try:
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                        stateless=True,
                    )
                except Exception as e:
                    logger.error(f"Streamable HTTP server error: {e}")
                finally:
                    await write_stream.aclose()

            tg.start_soon(run_server)

            # 收集响应（带超时）
            async def collect_responses():
                try:
                    with anyio.move_on_after(30):  # 30秒超时
                        async for msg in write_stream_reader:
                            if isinstance(msg, Exception):
                                logger.error(f"Streamable HTTP response error: {msg}")
                                break
                            responses.append(msg.message.model_dump(by_alias=True, exclude_none=True))
                            # 对于单个请求，收到一个响应即可
                            if body.get("id") is not None:
                                break
                except Exception as e:
                    logger.error(f"Streamable HTTP collect error: {e}")
                finally:
                    tg.cancel_scope.cancel()

            tg.start_soon(collect_responses)

        if not responses:
            # 无响应（可能是通知类型的请求）
            return response.text("Accepted", status=202)

        # 返回第一个响应（单请求模式）
        return response.json(responses[0], status=200)

    except Exception as e:
        logger.error(f"Streamable HTTP error: {e}")
        return response.json(
            {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": body.get("id")},
            status=500,
        )


async def _handle_sse_mode(request: Request):
    """
    SSE 模式：建立长连接，通过 Server-Sent Events 推送消息。
    """
    session_id = uuid4()
    sid_hex = session_id.hex

    read_stream_writer, read_stream = anyio.create_memory_object_stream(100)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(100)

    _write_stream_writers[session_id] = {"writer": read_stream_writer, "closed_at": None}

    redis_client = _get_redis_client(request)
    if redis_client:
        try:
            await redis_client.set(
                f"{MCP_REDIS_KEY_PREFIX}{sid_hex}",
                "1",
                ex=MCP_SESSION_GRACE_SECONDS + 3600,
            )
        except Exception as e:
            logger.warning(f"MCP Redis set session key failed: {e}")
        finally:
            await redis_client.close()

    endpoint_url = f"/api/mcp/messages?session_id={sid_hex}"

    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.send(f"event: endpoint\ndata: {endpoint_url}\n\n")

    tenant_id = request.ctx.session.get("tenant_id")

    async with anyio.create_task_group() as tg:

        async def run_server():
            server = create_mcp_server(tenant_id)
            try:
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                    stateless=True,
                )
            except Exception as e:
                logger.error(f"MCP Server Error: {e}")
                try:
                    await resp.eof()
                except Exception:
                    pass

        tg.start_soon(run_server)

        async def redis_subscriber():
            if not redis_client:
                return
            channel = f"{MCP_REDIS_CHAN_PREFIX}{sid_hex}"
            try:
                async with redis_client.pubsub() as pubsub:
                    await pubsub.subscribe(channel)
                    async for message in pubsub.listen():
                        if message.get("type") != "message":
                            continue
                        data = message.get("data")
                        if data is None:
                            continue
                        raw = data.decode("utf-8") if isinstance(data, bytes) else data
                        try:
                            body = orjson.loads(raw)
                            msg = JSONRPCMessage.model_validate(body)
                            session_message = SessionMessage(msg, metadata=None)
                            await read_stream_writer.send(session_message)
                        except Exception as e:
                            logger.error(f"MCP Redis subscriber parse/send: {e}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"MCP Redis subscriber error: {e}")

        tg.start_soon(redis_subscriber)
        await anyio.sleep(0.5)

        try:
            async for message in write_stream_reader:
                if isinstance(message, Exception):
                    logger.error(f"MCP Error in stream: {message}")
                    break
                data = message.message.model_dump_json(by_alias=True, exclude_none=True)
                await resp.send(f"event: message\ndata: {data}\n\n")
        except Exception as e:
            logger.error(f"SSE Loop Error: {e}")
        finally:
            logger.info(f"Closing MCP SSE session {session_id}")
            entry = _write_stream_writers.get(session_id)
            if entry:
                entry["closed_at"] = time.monotonic()
                request.app.add_task(_remove_session_after_grace(session_id, MCP_SESSION_GRACE_SECONDS))
            if redis_client:
                try:
                    await redis_client.expire(
                        f"{MCP_REDIS_KEY_PREFIX}{sid_hex}",
                        MCP_SESSION_GRACE_SECONDS,
                    )
                except Exception as e:
                    logger.warning(f"MCP Redis expire session key: {e}")
            tg.cancel_scope.cancel()
            await resp.eof()


@mcp_bp.post("/messages")
async def handle_messages(request: Request):
    """
    通过POST处理客户端消息(JSON-RPC)。
    多进程：先查本进程 writer；若无则查 Redis 键，存在则 publish 到 channel，由持有 SSE 的 worker 消费。
    支持宽限期：Redis 键在 SSE 断开后保留 MCP_SESSION_GRACE_SECONDS，减少 404。
    """
    session_id_str = request.args.get("session_id")
    if not session_id_str:
        return response.text("Missing session_id", status=400)

    try:
        session_id = UUID(hex=session_id_str)
    except ValueError:
        return response.text("Invalid session_id", status=400)

    sid_hex = session_id.hex
    body = request.json
    body_raw = body if isinstance(body, str) else (orjson.dumps(body).decode("utf-8") if body is not None else "{}")

    entry = _write_stream_writers.get(session_id)
    if entry:
        closed_at = entry.get("closed_at")
        if closed_at is not None and (time.monotonic() - closed_at) > MCP_SESSION_GRACE_SECONDS:
            del _write_stream_writers[session_id]
        else:
            try:
                body_obj = orjson.loads(body_raw) if isinstance(body_raw, str) else body_raw
                message = JSONRPCMessage.model_validate(body_obj)
                session_message = SessionMessage(message, metadata=None)
                await entry["writer"].send(session_message)
                return response.text("Accepted", status=202)
            except Exception as e:
                logger.error(f"消息处理错误: {e}, body_raw={body_raw}, traceback={traceback.format_exc()}")
                return response.text(f"Error: {str(e)}", status=500)

    redis_client = _get_redis_client(request)
    if redis_client:
        try:
            key = f"{MCP_REDIS_KEY_PREFIX}{sid_hex}"
            exists = await redis_client.exists(key)
            if exists:
                channel = f"{MCP_REDIS_CHAN_PREFIX}{sid_hex}"
                await redis_client.publish(channel, body_raw)
                return response.text("Accepted", status=202)
        except Exception as e:
            logger.error(f"MCP Redis publish: {e}")
        finally:
            await redis_client.close()

    return response.text("Session not found", status=404)
