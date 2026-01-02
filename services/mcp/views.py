from uuid import uuid4, UUID

import anyio
import orjson
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage
from sanic import Blueprint, Request, response
from sanic.log import logger

from .server import mcp_server

mcp_bp = Blueprint("mcp", url_prefix="/mcp")

# Store active sessions
# session_id -> MemoryObjectSendStream
_write_stream_writers = {}


@mcp_bp.get("/sse")
async def handle_sse(request: Request):
    """
    Handle SSE connection for MCP.
    """
    session_id = uuid4()

    # Create streams
    # read_stream: server reads from this (client sends to this via POST)
    read_stream_writer, read_stream = anyio.create_memory_object_stream(100)

    # write_stream: server writes to this (sent to client via SSE)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(100)

    _write_stream_writers[session_id] = read_stream_writer

    # Construct endpoint URL for POST messages
    # Assuming the server is reachable at the same host
    # We need the full URL or absolute path?
    # MCP spec says: `endpoint` event with the URI to POST to.
    # relative path is fine if client supports it, but full path is safer?
    # Let's use relative path to /mcp/messages
    endpoint_url = f"/mcp/messages?session_id={session_id.hex}"

    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    # 1. Send endpoint event
    await resp.send(f"event: endpoint\ndata: {endpoint_url}\n\n")

    # 2. Start MCP Server in background
    # We need to run mcp_server.run(...)
    # It handles the protocol logic.

    async with anyio.create_task_group() as tg:

        async def run_server():
            try:
                await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())
            except Exception as e:
                logger.error(f"MCP Server Error: {e}")
                # Close streams?
                try:
                    await resp.eof()
                except:
                    pass

        tg.start_soon(run_server)

        # 3. Loop over write_stream and send to SSE
        try:
            async for message in write_stream_reader:
                if isinstance(message, Exception):
                    logger.error(f"MCP Error in stream: {message}")
                    break

                # message is SessionMessage
                # We need to serialize it
                data = message.message.model_dump_json(by_alias=True, exclude_none=True)
                await resp.send(f"event: message\ndata: {data}\n\n")
        except Exception as e:
            logger.error(f"SSE Loop Error: {e}")
        finally:
            logger.info(f"Closing session {session_id}")
            if session_id in _write_stream_writers:
                del _write_stream_writers[session_id]
            # Cancel server task? tg will wait/cancel
            tg.cancel_scope.cancel()

    return resp


@mcp_bp.post("/messages")
async def handle_messages(request: Request):
    """
    Handle client messages (JSON-RPC) via POST.
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
        # Parse body
        body = request.json
        # Convert to MCP JSONRPCMessage
        # Using model_validate (pydantic v2) or parse_obj (v1)?
        # mcp library uses pydantic.
        if isinstance(body, str):
            body = orjson.loads(body)

        message = JSONRPCMessage.model_validate(body)

        # Wrap in SessionMessage?
        # mcp.server.Server.run expects stream of SessionMessage | Exception
        # ServerMessageMetadata is needed?
        # Not strictly required by type hint in run?
        # Actually run expects `MemoryObjectReceiveStream[SessionMessage | Exception]`

        # We need to construct SessionMessage
        # metadata = ServerMessageMetadata(request_context=request) # Request is not serializable/standard?
        # Let's pass None for metadata if allowed, or simple dict

        session_message = SessionMessage(message, metadata=None)

        await writer.send(session_message)

        return response.text("Accepted", status=202)

    except Exception as e:
        logger.error(f"Message handling error: {e}")
        return response.text(f"Error: {str(e)}", status=500)
