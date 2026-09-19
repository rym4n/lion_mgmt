# coding=utf-8
"""工单建单与查询。完整 API（九种操作、四个 tab、流程 CRUD）在计划 2。"""

from datetime import datetime

from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    SOURCE_MANUAL,
    TICKET_STATUS_PROCESSING,
    Ticket,
    TicketFlow,
    TicketFlowVersion,
    TicketNodeInstance,
    TicketType,
)
from services.ticket.engine import activate_nodes, next_node_ids, record_activity
from services.ticket.sla_scheduler import calculate_sla
from services.ticket.ticket_no import generate_ticket_no
from settings import TZ


_MISSING = object()


def _validate_form(form_schema, form_data) -> None:
    """提单表单必填校验。字段级动态控制（可见/可编辑/联动）在计划 2。"""
    for field in (form_schema or {}).get("fields", []):
        key = field.get("key")
        value = (form_data or {}).get(key, _MISSING)
        if field.get("required") and (
            value is _MISSING or value is None or value == ""
        ):
            raise CustomError(ErrorCode.TICKET_FORM_INVALID)


async def create_ticket(
    tenant_id: str,
    creator_id: str | None,
    payload: dict,
    *,
    allow_historical_version: bool = False,
) -> Ticket:
    """建单：分号 → 算 SLA → 钉版本 → 激活起始节点后的第一批节点。

    单号分配必须在建单事务内（advisory lock 绑定事务），因此整段包在
    in_transaction 里，且把 connection 透传给 generate_ticket_no。
    """
    selected_flow_id = payload.get("ticket_flow_id")
    selected_version_id = payload.get("flow_version_id")
    ticket_type = None
    version = None

    if selected_version_id:
        version = await TicketFlowVersion.filter(
            id=selected_version_id, tenant_id=tenant_id, deleted=False,
        ).first()
        if version is None:
            raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
        if version.status != 1 and not (
            allow_historical_version and version.status == 2
        ):
            raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
        selected_flow_id = selected_flow_id or version.ticket_flow_id

    if selected_flow_id:
        flow = await TicketFlow.filter(
            id=selected_flow_id, tenant_id=tenant_id, deleted=False,
        ).first()
        if flow is None:
            raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)
        if version is not None and version.ticket_flow_id != flow.id:
            raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
        ticket_type = await TicketType.filter(
            id=flow.ticket_type_id, tenant_id=tenant_id, deleted=False,
        ).first()
        if version is None:
            version = await TicketFlowVersion.filter(
                ticket_flow_id=flow.id, tenant_id=tenant_id,
                status=1, deleted=False,
            ).order_by("-ctime").first()
    else:
        ticket_type = await TicketType.filter(
            id=payload.get("ticket_type_id"), tenant_id=tenant_id, deleted=False
        ).first()
        if ticket_type is None:
            raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)
        if not ticket_type.default_flow_id:
            raise CustomError(ErrorCode.TICKET_TYPE_NO_FLOW)
        version = await TicketFlowVersion.filter(
            ticket_flow_id=ticket_type.default_flow_id, tenant_id=tenant_id,
            status=1, deleted=False,
        ).order_by("-ctime").first()

    if ticket_type is None:
        raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)
    if version is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NO_PUBLISHED)

    form_data = payload.get("form_data") or {}
    _validate_form(version.form_schema, form_data)

    start_id = next(
        (n["id"] for n in version.flow_json.get("nodes", []) if n.get("type") == "start"),
        None,
    )
    if not start_id:
        raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
    targets = next_node_ids(version.flow_json, start_id, 1, form_data)
    if not targets:
        raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)

    now = datetime.now(TZ)
    respond_at, resolve_at = calculate_sla(
        now, ticket_type.sla_config,
        flow_deadline=payload.get("flow_deadline"),
    )

    async with in_transaction() as conn:
        ticket_no = await generate_ticket_no(
            tenant_id, ticket_type.code, connection=conn, now=now
        )
        ticket = await Ticket.create(
            using_db=conn,
            tenant_id=tenant_id,
            ticket_no=ticket_no,
            title=payload.get("title") or ticket_no,
            status=TICKET_STATUS_PROCESSING,
            ticket_type_id=ticket_type.id,
            flow_version_id=version.id,       # 钉住版本，流程改动不影响在途工单
            creator_id=creator_id,
            form_data=form_data,
            source=int(payload.get("source") or SOURCE_MANUAL),
            origin_run_id=payload.get("origin_run_id"),
            origin_node_id=payload.get("origin_node_id"),
            origin_flow_id=payload.get("origin_flow_id"),
            origin_params=payload.get("origin_params"),
            callback_token=payload.get("callback_token"),
            sla_respond_at=respond_at,
            sla_resolve_at=resolve_at,
        )
        await record_activity(
            ticket, "submit", content="工单已创建", actor_id=creator_id,
            payload={"ticket_no": ticket_no}, connection=conn,
        )
        await activate_nodes(ticket, targets, version.flow_json, connection=conn)
    return ticket


async def get_ticket_detail(tenant_id: str, ticket_id: str) -> dict:
    """详情。available_actions 的完整三层计算在计划 2（SPEC §7.1）。"""
    ticket = await Ticket.filter(
        id=ticket_id, tenant_id=tenant_id, deleted=False
    ).first()
    if ticket is None:
        raise CustomError(ErrorCode.TICKET_NOT_FOUND)

    nodes = await TicketNodeInstance.filter(
        ticket_id=ticket.id, tenant_id=tenant_id, deleted=False
    ).order_by("ctime").all()
    return {**ticket.json, "nodes": [n.json for n in nodes]}
