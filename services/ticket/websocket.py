# coding=utf-8
"""工单待办 WebSocket 推送。

连接与作战室完全独立，但复用作战室已经建立的 Redis pub/sub 分发通道，
这样任意 worker 提交工单事务后，都能通知持有目标用户连接的 worker。
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable, Iterable

import orjson
from sanic import Blueprint, Request, Websocket
from sanic.exceptions import WebsocketClosed
from sanic.log import logger
from sanic_ext import openapi
from websockets import ConnectionClosed

from libs.security.authorization import requires_permission
from models.ticket import (
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    TICKET_STATUS_PROCESSING,
    Ticket,
    TicketNodeInstance,
)
from settings import WR_TOPIC

ticket_ws_bp = Blueprint("ticket_pending_ws")


async def get_pending_snapshot(
    tenant_id: str,
    user_id: str,
    limit: int = 20,
    *,
    ticket_model=None,
    node_model=None,
) -> dict:
    """返回用户当前待办快照；计数不受列表展示上限影响。"""
    ticket_model = ticket_model or Ticket
    node_model = node_model or TicketNodeInstance
    ticket_ids = await node_model.filter(
        tenant_id=tenant_id,
        deleted=False,
        status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
        approvers__id=user_id,
    ).values_list("ticket_id", flat=True)
    unique_ticket_ids = list(dict.fromkeys(ticket_ids))
    if not unique_ticket_ids:
        return {"type": "pending_update", "count": 0, "items": []}

    query = ticket_model.filter(
        id__in=unique_ticket_ids,
        tenant_id=tenant_id,
        deleted=False,
        status=TICKET_STATUS_PROCESSING,
    )
    count = await query.count()
    tickets = await query.order_by("sla_resolve_at").limit(limit).all()
    return {
        "type": "pending_update",
        "count": count,
        "items": [
            {
                "id": ticket.id,
                "ticket_no": ticket.ticket_no,
                "title": ticket.title,
            }
            for ticket in tickets
        ],
    }


async def active_approver_ids(
    tenant_id: str, ticket_id: str, connection=None
) -> set[str]:
    """查询工单提交后的活跃审批人集合。"""
    query = TicketNodeInstance.filter(
        ticket_id=ticket_id,
        tenant_id=tenant_id,
        deleted=False,
        status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
    )
    if connection is not None:
        query = query.using_db(connection)
    user_ids = await query.values_list("approvers__id", flat=True)
    return {str(user_id) for user_id in user_ids if user_id}


class AfterCommitHooks:
    """收集事务后回调；调用方只在事务上下文正常退出后执行 ``run``。"""

    def __init__(self) -> None:
        self._callbacks: list[tuple[Callable, tuple, dict]] = []

    def add(self, callback: Callable, *args, **kwargs) -> None:
        self._callbacks.append((callback, args, kwargs))

    async def run(self) -> None:
        callbacks, self._callbacks = self._callbacks, []
        for callback, args, kwargs in callbacks:
            try:
                result = callback(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # 数据已提交，推送异常不能把成功的业务请求改成失败。
                logger.exception("工单待办 after_commit 推送失败")


class TicketPendingWebSocketManager:
    """按 tenant_id / user_id / session_id 管理待办连接。"""

    def __init__(self) -> None:
        self.active_connections: dict[
            str, dict[str, dict[str, Websocket]]
        ] = defaultdict(lambda: defaultdict(dict))

    async def connect(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        ws: Websocket,
    ) -> None:
        self.active_connections[tenant_id][user_id][session_id] = ws
        await self._send_snapshot(tenant_id, user_id)

    async def disconnect(self, tenant_id: str, user_id: str, session_id: str) -> None:
        tenant_connections = self.active_connections.get(tenant_id)
        if not tenant_connections:
            return
        user_connections = tenant_connections.get(user_id)
        if user_connections is not None:
            user_connections.pop(session_id, None)
            if not user_connections:
                tenant_connections.pop(user_id, None)
        if not tenant_connections:
            self.active_connections.pop(tenant_id, None)

    async def push_users(self, tenant_id: str, user_ids: Iterable[str]) -> None:
        """仅给当前 worker 中该租户的目标用户发送个性化快照。"""
        connected_users = self.active_connections.get(tenant_id, {})
        for user_id in {str(item) for item in user_ids if item}:
            if user_id in connected_users:
                await self._send_snapshot(tenant_id, user_id)

    async def _send_snapshot(self, tenant_id: str, user_id: str) -> None:
        user_connections = self.active_connections.get(tenant_id, {}).get(user_id)
        if not user_connections:
            return
        payload = orjson.dumps(await get_pending_snapshot(tenant_id, user_id)).decode()
        dead_sessions = []
        for session_id, ws in list(user_connections.items()):
            try:
                await ws.send(payload)
            except (WebsocketClosed, ConnectionClosed):
                dead_sessions.append(session_id)
            except Exception:
                logger.exception(
                    "工单待办 WebSocket 发送失败: tenant_id=%s user_id=%s",
                    tenant_id,
                    user_id,
                )
                dead_sessions.append(session_id)
        for session_id in dead_sessions:
            await self.disconnect(tenant_id, user_id, session_id)


ticket_pending_manager = TicketPendingWebSocketManager()


async def publish_ticket_pending_update(
    tenant_id: str,
    ticket_id: str,
    previous_user_ids: Iterable[str] = (),
) -> None:
    """事务提交后发布待办变化，覆盖变更前与变更后的审批人。"""
    recipients = {str(user_id) for user_id in previous_user_ids if user_id}
    recipients.update(await active_approver_ids(tenant_id, ticket_id))
    if not recipients:
        return

    from libs.chat import chat_manager

    await chat_manager.msg_pub.publish(
        WR_TOPIC,
        orjson.dumps(
            {
                "ticket_pending": {
                    "tenant_id": tenant_id,
                    "user_ids": sorted(recipients),
                }
            }
        ).decode(),
    )


@ticket_ws_bp.websocket("/ws/ticket/pending")
@openapi.exclude()
@requires_permission("ticket:r")
async def ticket_pending_ws(request: Request, ws: Websocket):
    session = request.ctx.session
    account = session.get("account_info")
    tenant_id = session.get("tenant_id")
    user_id = session.get("sub") or getattr(account, "id", None)
    session_id = f"{session.get('id') or 'session'}:{id(ws)}"
    if not account or not tenant_id or not user_id:
        await ws.close()
        return

    tenant_id = str(tenant_id)
    user_id = str(user_id)
    try:
        await ticket_pending_manager.connect(tenant_id, user_id, session_id, ws)
        async for raw_message in ws:
            try:
                message = orjson.loads(raw_message)
            except orjson.JSONDecodeError:
                continue
            if message.get("type") == "ping":
                await ws.send(orjson.dumps({"type": "pong"}).decode())
    except (WebsocketClosed, ConnectionClosed):
        pass
    except Exception:
        logger.exception(
            "工单待办 WebSocket 异常: tenant_id=%s user_id=%s",
            tenant_id,
            user_id,
        )
    finally:
        await ticket_pending_manager.disconnect(tenant_id, user_id, session_id)
        try:
            await ws.close()
        except (WebsocketClosed, ConnectionClosed):
            pass


__all__ = [
    "AfterCommitHooks",
    "active_approver_ids",
    "get_pending_snapshot",
    "publish_ticket_pending_update",
    "ticket_pending_manager",
    "ticket_ws_bp",
]
