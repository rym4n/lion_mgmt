# coding=utf-8
"""工单 API（计划 2：完整 18 个端点 + 三层权限）。"""

from copy import deepcopy
from datetime import datetime, timedelta

from sanic import Blueprint, Request
from sanic.log import logger
from sanic.response import raw
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import (
    check_ticket_flow_permission,
    requires_permission,
    ticket_flow_permission,
)
from models.ticket import (
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    SOURCE_AGENT,
    SOURCE_MANUAL,
    SOURCE_PLAYBOOK,
    TICKET_STATUS_FORCE_CLOSED,
    TICKET_STATUS_PROCESSING,
    TICKET_TERMINAL_STATUSES,
    Ticket,
    TicketActivity,
    TicketFlow,
    TicketFlowVersion,
    TicketNodeInstance,
    TicketType,
)
from models.user import Account
from services.digital_employee.lion_agent_adapter import chat_with_agent
from services.ticket.engine import (
    advance_ticket,
    change_node_approvers,
    record_activity,
)
from services.ticket.flow_package import (
    active_snapshot,
    build_flow_archive,
    parse_flow_archive,
)
from services.ticket.manager import create_ticket
from services.ticket.permissions import (
    compute_available_actions,
    is_current_approver,
    is_ticket_admin,
)
from services.ticket.websocket import (
    AfterCommitHooks,
    get_pending_snapshot,
    publish_ticket_pending_update,
)
from settings import TZ

ticket_bp = Blueprint("ticket", url_prefix="/ticket")


def _session_user_id(session: dict):
    account = session.get("account_info")
    return session.get("sub") or getattr(account, "id", None)


def _datetime_text(value):
    return value.isoformat() if value else None


def _approver_ids(nodes) -> set[str]:
    user_ids = set()
    for node in nodes:
        approvers = getattr(node, "approvers", ())
        if hasattr(approvers, "members"):
            approvers = approvers.members
        for approver in approvers:
            if getattr(approver, "id", None):
                user_ids.add(str(approver.id))
    return user_ids


async def _push_pending_after_commit(
    tenant_id: str, ticket_id: str, previous_user_ids=()
) -> None:
    hooks = AfterCommitHooks()
    hooks.add(
        publish_ticket_pending_update,
        tenant_id,
        ticket_id,
        previous_user_ids,
    )
    await hooks.run()


def _flow_json_for_ticket(ticket) -> dict:
    """Return the ticket's pinned flow definition, including test doubles."""
    version = getattr(ticket, "flow_version", None) or getattr(ticket, "_flow_version", None)
    flow_json = getattr(version, "flow_json", None)
    if not isinstance(flow_json, dict) or not flow_json:
        flow_json = getattr(ticket, "_flow_json", None)
    return flow_json if isinstance(flow_json, dict) else {}


def _digital_employee_config(ticket) -> dict | None:
    """Read the opt-in digital partner section from a flow definition.

    Both the current nested shape and the early boolean/flat shape are accepted
    so published flows remain compatible while the editor evolves.
    """
    flow_json = _flow_json_for_ticket(ticket)
    settings = flow_json.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    config = flow_json.get("digital_employee", settings.get("digital_employee"))
    if isinstance(config, dict):
        enabled = config.get("enabled")
        if enabled is True:
            return config
        return None
    enabled = (
        config is True
        or flow_json.get("digital_employee_enabled") is True
        or flow_json.get("enable_digital_employee") is True
        or settings.get("digital_employee_enabled") is True
    )
    if enabled:
        return {
            "enabled": True,
            "employee_id": (
                flow_json.get("digital_employee_id")
                or flow_json.get("employee_id")
                or settings.get("digital_employee_id")
                or settings.get("employee_id")
            ),
        }
    return None


def _approval_notification(ticket, action: str, approver: str, comment: str, at: datetime) -> str:
    result = "通过" if action == "approve" else "拒绝"
    lines = [
        "工单审批结果通知",
        f"工单：{ticket.ticket_no}（{ticket.title}）",
        f"审批结果：{result}",
        f"审批人：{approver}",
        f"时间：{at.isoformat()}",
    ]
    if action == "reject":
        lines.insert(3, f"拒绝原因：{comment or '未填写'}")
    return "\n".join(lines)


async def _notify_digital_employee(ticket, action: str, session: dict, comment: str) -> None:
    config = _digital_employee_config(ticket)
    if config is None:
        return
    account = session.get("account_info")
    approver = (
        getattr(account, "display_name", None)
        or getattr(account, "username", None)
        or session.get("sub")
        or "未知审批人"
    )
    message = _approval_notification(ticket, action, str(approver), comment, datetime.now(TZ))
    try:
        await chat_with_agent(
            message,
            employee_id=config.get("employee_id") or config.get("agent_id"),
            tenant_id=ticket.tenant_id,
            session_id=f"ticket:{ticket.id}:approval",
        )
    except Exception as exc:
        # Approval state is already durable; a transient agent outage must not
        # turn a successful approval into an apparent failed request.
        logger.warning("工单审批结果通知数字伙伴失败: ticket_id=%s, error=%s", ticket.id, exc)


async def _ticket_list_relation_names(records, tenant_id):
    type_ids = {ticket.ticket_type_id for ticket in records if ticket.ticket_type_id}
    version_ids = {
        ticket.flow_version_id for ticket in records if ticket.flow_version_id
    }
    creator_ids = {ticket.creator_id for ticket in records if ticket.creator_id}

    type_names = {}
    if type_ids:
        type_names = dict(await TicketType.filter(
            id__in=list(type_ids), tenant_id=tenant_id, deleted=False
        ).values_list("id", "name"))

    version_flow_ids = {}
    if version_ids:
        version_flow_ids = dict(await TicketFlowVersion.filter(
            id__in=list(version_ids), tenant_id=tenant_id, deleted=False
        ).values_list("id", "ticket_flow_id"))

    flow_names = {}
    flow_ids = {flow_id for flow_id in version_flow_ids.values() if flow_id}
    if flow_ids:
        names_by_flow_id = dict(await TicketFlow.filter(
            id__in=list(flow_ids), tenant_id=tenant_id, deleted=False
        ).values_list("id", "name"))
        flow_names = {
            version_id: names_by_flow_id.get(flow_id, "")
            for version_id, flow_id in version_flow_ids.items()
        }

    creator_names = {}
    if creator_ids:
        creator_names = dict(await Account.filter(
            id__in=list(creator_ids), tenant_id=tenant_id, deleted=False
        ).values_list("id", "display_name"))

    return type_names, flow_names, creator_names


@ticket_bp.post("/create")
@openapi.definition(summary="创建工单", description="需要 ticket:c 权限")
@requires_permission("ticket:c")
async def create_view(request: Request):
    session = request.ctx.session
    tenant_id = session.get("tenant_id")
    account = session.get("account_info")
    payload = dict(request.json or {})
    from libs.security.authorization import ticket_flow_ids_for_user

    flow_id = payload.get("ticket_flow_id")
    if not flow_id and payload.get("flow_version_id"):
        version = await TicketFlowVersion.filter(
            id=payload["flow_version_id"],
            tenant_id=tenant_id,
            status=1,
            deleted=False,
        ).first()
        if version is None:
            raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
        flow_id = version.ticket_flow_id
    if not flow_id and payload.get("ticket_type_id"):
        ticket_type = await TicketType.filter(
            id=payload["ticket_type_id"], tenant_id=tenant_id, deleted=False
        ).first()
        if ticket_type is None:
            raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)
        if not ticket_type.default_flow_id:
            raise CustomError(ErrorCode.TICKET_TYPE_NO_FLOW)
        flow_id = ticket_type.default_flow_id
    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if flow_id not in await ticket_flow_ids_for_user(account, tenant_id, "s"):
        raise CustomError(ErrorCode.TICKET_FLOW_PERM_DENIED)
    payload["source"] = SOURCE_MANUAL
    ticket = await create_ticket(
        tenant_id,
        session.get("sub"),  # JWT payload 的 sub 是 account_id
        payload,
    )
    await _push_pending_after_commit(ticket.tenant_id, ticket.id)
    return response_ok({"id": ticket.id, "ticket_no": ticket.ticket_no})


@ticket_bp.get("/detail/<ticket_id>")
@openapi.definition(summary="工单详情", description="需要 ticket:r 权限")
@requires_permission("ticket:r")
async def detail_view(request: Request, ticket_id: str):
    tenant_id = request.ctx.session.get("tenant_id")
    user_id = request.ctx.session.get("sub")
    session = request.ctx.session

    ticket = await Ticket.filter(
        id=ticket_id, tenant_id=tenant_id, deleted=False
    ).prefetch_related("flow_version").first()
    if ticket is None:
        raise CustomError(ErrorCode.TICKET_NOT_FOUND)

    type_names, _, creator_names = await _ticket_list_relation_names(
        [ticket], tenant_id
    )

    nodes = await TicketNodeInstance.filter(
        ticket_id=ticket.id, tenant_id=tenant_id, deleted=False
    ).prefetch_related("approvers").order_by("ctime").all()

    # 解析审批人集合（SPEC §7.1 三层权限）
    active_approver_ids = set()
    all_approver_ids = set()
    for node in nodes:
        # nodes 已 prefetch_related("approvers")，可直接访问
        approver_ids = {a.id for a in node.approvers}
        all_approver_ids |= approver_ids
        if node.status in (NODE_STATUS_PENDING, NODE_STATUS_RUNNING):
            active_approver_ids |= approver_ids

    # 计算 available_actions（SPEC §7.1 三层权限）
    available = compute_available_actions(
        ticket, active_approver_ids, all_approver_ids, user_id, session
    )

    activities = (
        await TicketActivity.filter(
            ticket_id=ticket.id, tenant_id=tenant_id, deleted=False
        )
        .order_by("-ctime")
        .limit(50)
        .all()
    )
    version = getattr(ticket, "flow_version", None)
    form_schema = getattr(version, "form_schema", None)

    return response_ok(
        {
            **ticket.json,
            "ticket_type_name": type_names.get(ticket.ticket_type_id, ""),
            "creator_name": creator_names.get(ticket.creator_id, ""),
            "flow_json": _flow_json_for_ticket(ticket),
            "form_schema": form_schema if isinstance(form_schema, (dict, list)) else {},
            "nodes": [n.json for n in nodes],
            "activities": [
                {
                    "id": a.id,
                    "action": a.action,
                    "content": a.content,
                    "actor_id": a.actor_id,
                    "node_instance_id": a.node_instance_id,
                    "payload": a.payload or {},
                    "ctime": a.ctime.isoformat() if a.ctime else None,
                }
                for a in activities
            ],
            "available_actions": available,
        }
    )


@ticket_bp.get("/list")
@openapi.definition(
    summary="工单列表（四 tab）",
    description="按 view 参数区分：my_todo / my_apply / my_history / all",
    parameter=[
        {"name": "view", "in": "query", "required": True, "schema": {"type": "string"}},
        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
        {"name": "size", "in": "query", "schema": {"type": "integer", "default": 20}},
        {"name": "keyword", "in": "query", "schema": {"type": "string"}},
        {"name": "status", "in": "query", "schema": {"type": "integer"}},
        {"name": "ticket_no", "in": "query", "schema": {"type": "string"}},
        {"name": "title", "in": "query", "schema": {"type": "string"}},
        {"name": "ticket_type_id", "in": "query", "schema": {"type": "string"}},
        {"name": "flow_id", "in": "query", "schema": {"type": "string"}},
        {"name": "creator_id", "in": "query", "schema": {"type": "string"}},
        {"name": "ctime_start", "in": "query", "schema": {"type": "string"}},
        {"name": "ctime_end", "in": "query", "schema": {"type": "string"}},
    ],
)
@requires_permission("ticket:r")
async def list_view(request: Request):
    session = request.ctx.session
    tenant_id = session.get("tenant_id")
    user_id = _session_user_id(session)

    view = request.args.get("view")
    try:
        page = int(request.args.get("page", 1))
        size = min(int(request.args.get("size", 20)), 100)
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if view not in ("my_todo", "my_apply", "my_history", "all"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if not tenant_id or not user_id or page < 1 or size < 1:
        raise CustomError(ErrorCode.PARAM_ERROR)

    keyword = (request.args.get("keyword") or "").strip()
    status_filter = request.args.get("status")

    query = Ticket.filter(tenant_id=tenant_id, deleted=False)

    if view == "my_todo":
        # 我的待办：当前用户是活跃节点审批人的工单
        node_ids = await TicketNodeInstance.filter(
            tenant_id=tenant_id,
            deleted=False,
            status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
            approvers__id=user_id,
        ).values_list("ticket_id", flat=True)
        query = query.filter(
            id__in=list(dict.fromkeys(node_ids)),
            status=TICKET_STATUS_PROCESSING,
        )
        # 按 SLA 剩余秒数升序（先超时的排前面）
        query = query.order_by("sla_resolve_at")

    elif view == "my_apply":
        # 我的申请：我创建的全部工单
        query = query.filter(creator_id=user_id)
        if status_filter is not None and status_filter != "":
            try:
                query = query.filter(status=int(status_filter))
            except (TypeError, ValueError):
                raise CustomError(ErrorCode.PARAM_ERROR)
        query = query.order_by("-ctime")

    elif view == "my_history":
        # 我的历史：我处理过且已到终态的工单
        activity_ticket_ids = await TicketActivity.filter(
            tenant_id=tenant_id,
            deleted=False,
            actor_id=user_id,
            action__in=["approve", "reject", "transfer", "return"],
        ).values_list("ticket_id", flat=True)
        query = query.filter(
            id__in=list(dict.fromkeys(activity_ticket_ids)),
            status__in=list(TICKET_TERMINAL_STATUSES),
        ).order_by("-closed_at")

    elif view == "all":
        # 全部工单：需要 ticket:all 权限
        if not is_ticket_admin(session):
            raise CustomError(ErrorCode.TICKET_PERM_DENIED)
        query = query.order_by("-ctime")

    else:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 关键词搜索
    if keyword:
        query = query.filter(
            Q(ticket_no__icontains=keyword) | Q(title__icontains=keyword)
        )

    ticket_no = (request.args.get("ticket_no") or "").strip()
    title = (request.args.get("title") or "").strip()
    ticket_type_id = (request.args.get("ticket_type_id") or "").strip()
    flow_id = (request.args.get("flow_id") or "").strip()
    creator_id = (request.args.get("creator_id") or "").strip()
    if ticket_no:
        query = query.filter(ticket_no__icontains=ticket_no)
    if title:
        query = query.filter(title__icontains=title)
    if ticket_type_id:
        query = query.filter(ticket_type_id=ticket_type_id)
    if flow_id:
        version_ids = await TicketFlowVersion.filter(
            ticket_flow_id=flow_id,
            tenant_id=tenant_id,
            deleted=False,
        ).values_list("id", flat=True)
        query = query.filter(flow_version_id__in=list(version_ids))
    if creator_id:
        query = query.filter(creator_id=creator_id)

    for param_name, lookup in (
        ("ctime_start", "ctime__gte"),
        ("ctime_end", "ctime__lte"),
    ):
        raw_value = request.args.get(param_name)
        if not raw_value:
            continue
        try:
            parsed_value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_ERROR)
        query = query.filter(**{lookup: parsed_value})

    if status_filter not in (None, "") and view != "my_apply":
        try:
            query = query.filter(status=int(status_filter))
        except (TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_ERROR)

    total = await query.count()
    offset = (page - 1) * size
    records = await query.offset(offset).limit(size).all()

    type_names, flow_names, creator_names = await _ticket_list_relation_names(
        records, tenant_id
    )
    data = []
    for ticket in records:
        item = {
            "id": ticket.id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.title,
            "status": ticket.status,
            "ticket_type_id": ticket.ticket_type_id,
            "creator_id": ticket.creator_id,
            "ticket_type_name": type_names.get(ticket.ticket_type_id, ""),
            "flow_name": flow_names.get(ticket.flow_version_id, ""),
            "creator_name": creator_names.get(ticket.creator_id, ""),
            "ctime": _datetime_text(ticket.ctime),
            "sla_resolve_at": _datetime_text(ticket.sla_resolve_at),
        }
        if view == "my_history":
            item["is_creator"] = ticket.creator_id == user_id
        data.append(item)

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": data,
    })


@ticket_bp.get("/todolist")
@openapi.definition(
    summary="待办列表（供通知中心调用）",
    description="返回数组，空列表时 code=0",
)
@requires_permission("ticket:r")
async def todolist_view(request: Request):
    try:
        session = request.ctx.session
        tenant_id = session.get("tenant_id")
        user_id = _session_user_id(session)
        if not tenant_id or not user_id:
            return response_ok([])

        snapshot = await get_pending_snapshot(
            tenant_id,
            user_id,
            ticket_model=Ticket,
            node_model=TicketNodeInstance,
        )
        return response_ok(snapshot["items"])
    except Exception as exc:
        logger.warning("todolist error: %s", exc)
        return response_ok([])


@ticket_bp.post("/action")
@openapi.definition(
    summary="工单操作统一入口",
    description="七种操作：approve/reject/transfer/return/add_approver/urge/force_close",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["ticket_id", "action"],
                "properties": {
                    "ticket_id": {"type": "string"},
                    "action": {"type": "string"},
                    "node_instance_id": {"type": "string"},
                    "form_data": {"type": "object"},
                    "comment": {"type": "string"},
                    "target_user_ids": {"type": "array", "items": {"type": "string"}},
                    "return_target": {"type": "string"},
                },
            }
        }
    },
)
@requires_permission("ticket:u")
async def action_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    user_id = request.ctx.session.get("sub")
    session = request.ctx.session

    payload = request.json or {}
    ticket_id = payload.get("ticket_id")
    action = payload.get("action")
    node_instance_id = payload.get("node_instance_id")
    comment = payload.get("comment", "").strip()

    if not ticket_id or not action:
        raise CustomError(ErrorCode.PARAM_ERROR)

    ticket = await Ticket.filter(
        id=ticket_id, tenant_id=tenant_id, deleted=False
    ).prefetch_related("flow_version").first()
    if ticket is None:
        raise CustomError(ErrorCode.TICKET_NOT_FOUND)

    # 终态工单不可操作
    if ticket.status in TICKET_TERMINAL_STATUSES:
        raise CustomError(ErrorCode.TICKET_ALREADY_CLOSED)

    # 统一查一次活跃节点（后续 return/transfer 等都用它）
    active_nodes = await TicketNodeInstance.filter(
        ticket_id=ticket.id, tenant_id=tenant_id, deleted=False,
        status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
    ).prefetch_related("approvers").all()
    previous_user_ids = _approver_ids(active_nodes)

    # approve / reject：推进状态机
    if action in ("approve", "reject"):
        if not node_instance_id:
            raise CustomError(ErrorCode.PARAM_ERROR)
        node = await TicketNodeInstance.filter(
            id=node_instance_id, tenant_id=tenant_id, deleted=False
        ).prefetch_related("approvers").first()
        if node is None:
            raise CustomError(ErrorCode.TICKET_NODE_NOT_ACTIVE)

        # 检查是否当前审批人
        if not await is_current_approver(node, user_id):
            raise CustomError(ErrorCode.TICKET_NOT_APPROVER)

        # 调用引擎推进
        await advance_ticket(
            ticket_id=ticket.id,
            tenant_id=tenant_id,
            trigger="operate",
            actor_id=user_id,
            node_instance_id=node.id,
            decision=action,
            node_form_data=payload.get("form_data"),
        )
        await _push_pending_after_commit(
            tenant_id, ticket.id, previous_user_ids
        )

        await _notify_digital_employee(ticket, action, session, comment)

        # 数字伙伴来源：调 decide_approval（SPEC §7.1 分流）
        if ticket.source == SOURCE_AGENT:
            from services.command_center.approval_service import decide_approval
            await decide_approval(
                ticket.origin_run_id,
                "agree" if action == "approve" else "disagree",
                comment or "",
            )
        return response_ok({"message": "操作成功"})

    # transfer / add_approver：变更审批人
    if action in ("transfer", "add_approver"):
        target_user_ids = payload.get("target_user_ids") or []
        if not target_user_ids or not node_instance_id:
            raise CustomError(ErrorCode.PARAM_ERROR)

        node = await TicketNodeInstance.filter(
            id=node_instance_id, tenant_id=tenant_id, deleted=False
        ).prefetch_related("approvers").first()
        if node is None:
            raise CustomError(ErrorCode.TICKET_NODE_NOT_ACTIVE)

        from tortoise.transactions import in_transaction

        async with in_transaction() as conn:
            await change_node_approvers(
                ticket, node, action, target_user_ids, actor_id=user_id, connection=conn
            )
        await _push_pending_after_commit(
            tenant_id,
            ticket.id,
            previous_user_ids | _approver_ids([node]),
        )
        return response_ok({"message": "操作成功"})

    # return：退回指定节点
    if action == "return":
        return_target = payload.get("return_target")  # "creator" 或 node_id
        if not return_target:
            raise CustomError(ErrorCode.PARAM_ERROR)

        # 校验：当前审批人
        if not any(await is_current_approver(node, user_id) for node in active_nodes):
            raise CustomError(ErrorCode.TICKET_PERM_DENIED)

        from services.ticket.engine import return_to_node
        from tortoise.transactions import in_transaction

        async with in_transaction() as conn:
            await return_to_node(
                ticket, return_target, actor_id=user_id, connection=conn
            )
        await _push_pending_after_commit(
            tenant_id, ticket.id, previous_user_ids
        )
        return response_ok({"message": "退回成功"})

    # urge：催办
    if action == "urge":
        # 校验：提单人
        if ticket.creator_id != user_id:
            raise CustomError(ErrorCode.TICKET_PERM_DENIED)
        if ticket.status != TICKET_STATUS_PROCESSING:
            raise CustomError(ErrorCode.TICKET_ALREADY_CLOSED)

        # 限流：同一工单 1 小时内只能催一次
        one_hour_ago = datetime.now(TZ) - timedelta(hours=1)
        recent_urge = await TicketActivity.filter(
            ticket_id=ticket_id,
            action="urge",
            actor_id=user_id,
            ctime__gte=one_hour_ago,
            deleted=False,
        ).first()
        if recent_urge:
            raise CustomError(ErrorCode.MAX_CHALLENGE)

        # 记录催办活动
        from tortoise.transactions import in_transaction
        async with in_transaction() as conn:
            await record_activity(
                ticket, "urge", actor_id=user_id, connection=conn
            )

        return response_ok({"message": "催办成功"})

    # force_close：强制关闭
    if action == "force_close":
        # 校验：ticket:all 权限
        if not is_ticket_admin(session):
            raise CustomError(ErrorCode.TICKET_ADMIN_ONLY)

        callback_decision = payload.get("callback_decision")  # "approve" / "reject" / None
        comment = payload.get("comment", "")

        # 剧本触发的需要选择回传方式
        if ticket.source == SOURCE_PLAYBOOK and not callback_decision:
            raise CustomError(ErrorCode.PARAM_ERROR)

        from tortoise.transactions import in_transaction
        async with in_transaction() as conn:
            ticket.status = TICKET_STATUS_FORCE_CLOSED
            ticket.closed_at = datetime.now(TZ)
            await ticket.save(using_db=conn)
            await record_activity(
                ticket, "force_close", actor_id=user_id, content=comment, connection=conn
            )

        await _push_pending_after_commit(
            tenant_id, ticket.id, previous_user_ids
        )

        # 强关绕过状态机，显式触发终态回传。
        if ticket.source == SOURCE_PLAYBOOK and callback_decision and callback_decision != "none":
            from services.ticket.playbook_bridge import callback_playbook
            await callback_playbook(
                ticket, operator=user_id, result=callback_decision,
                approval_result={"decision": callback_decision, "comment": comment},
            )

        return response_ok({"message": "关闭成功"})

    # 未知操作
    raise CustomError(ErrorCode.PARAM_ERROR)


async def _callback_playbook(ticket, approval_status: int, executor: str | None, results: dict) -> None:
    """兼容旧调用方；实际消息构造统一由 playbook_bridge 负责。"""
    if approval_status not in (1, 2):
        raise CustomError(ErrorCode.PARAM_ERROR)
    from services.ticket.playbook_bridge import callback_playbook
    await callback_playbook(
        ticket, operator=executor,
        result="approved" if approval_status == 1 else "rejected",
        approval_result=results,
        approval_status=approval_status,
    )


# ============================================================================
# 工单类型管理
# ============================================================================

@ticket_bp.get("/type/list")
@openapi.definition(summary="工单类型列表")
@requires_permission("ticket:r")
async def type_list_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    types = (
        await TicketType.filter(tenant_id=tenant_id, deleted=False)
        .order_by("ctime")
        .all()
    )
    type_data = [dict(ticket_type.json) for ticket_type in types]
    flow_ids = {
        ticket_type.default_flow_id
        for ticket_type in types
        if ticket_type.default_flow_id
    }
    published_by_flow = {}
    if flow_ids:
        published_versions = await TicketFlowVersion.filter(
            ticket_flow_id__in=flow_ids,
            tenant_id=tenant_id,
            status=1,
            deleted=False,
        ).order_by("-ctime").all()
        for version in published_versions:
            published_by_flow.setdefault(version.ticket_flow_id, version)

    for ticket_type, item in zip(types, type_data, strict=True):
        flow_id = ticket_type.default_flow_id
        if not flow_id:
            item["default_flow"] = None
            continue
        published = published_by_flow.get(flow_id)
        item["default_flow"] = {
            "id": flow_id,
            "published_version": (
                {
                    "id": published.id,
                    "version": published.version,
                    "status": published.status,
                    "form_schema": published.form_schema or {},
                }
                if published
                else None
            ),
        }
    return response_ok(type_data)


@ticket_bp.post("/type/save")
@openapi.definition(summary="保存工单类型（新增与更新合一）")
@requires_permission("ticket_flow:u")
async def type_save_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    from models.ticket import TicketType

    payload = request.json or {}
    type_id = payload.get("id")
    name = payload.get("name", "").strip()
    code = payload.get("code", "").strip()

    if not name or not code:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 检查 code 唯一性
    type_query = TicketType.filter(tenant_id=tenant_id, code=code, deleted=False)
    if type_id:
        type_query = type_query.exclude(id=type_id)
    existing = await type_query.first()
    if existing:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if type_id:
        # 更新
        ticket_type = await TicketType.filter(
            id=type_id, tenant_id=tenant_id, deleted=False
        ).first()
        if ticket_type is None:
            raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)
        ticket_type.name = name
        ticket_type.code = code
        ticket_type.description = payload.get("description")
        ticket_type.icon = payload.get("icon")
        ticket_type.default_flow_id = payload.get("default_flow_id")
        ticket_type.sla_config = payload.get("sla_config") or {}
        await ticket_type.save()
    else:
        # 新增
        ticket_type = await TicketType.create(
            tenant_id=tenant_id,
            name=name,
            code=code,
            description=payload.get("description"),
            icon=payload.get("icon"),
            default_flow_id=payload.get("default_flow_id"),
            sla_config=payload.get("sla_config") or {},
            internal=False,
        )

    return response_ok(ticket_type.json)


@ticket_bp.post("/type/delete")
@openapi.definition(summary="删除工单类型（软删除）")
@requires_permission("ticket:u")
async def type_delete_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    from models.ticket import TicketType

    payload = request.json or {}
    type_id = payload.get("id")
    if not type_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    ticket_type = await TicketType.filter(
        id=type_id, tenant_id=tenant_id, deleted=False
    ).first()
    if ticket_type is None:
        raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)

    ticket_type.deleted = True
    await ticket_type.save()
    return response_ok({"message": "删除成功"})


# ============================================================================
# 工单流程管理
# ============================================================================

@ticket_bp.get("/flow/list")
@openapi.definition(summary="流程列表（过滤用户有权限的流程）")
@requires_permission("ticket_flow:r")
async def flow_list_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    from models.ticket import TicketFlow
    from libs.security.authorization import ticket_flow_ids_for_user

    # 获取用户有 r 权限的流程
    flow_ids = await ticket_flow_ids_for_user(account, tenant_id, "r")
    flows = await TicketFlow.filter(
        id__in=flow_ids, tenant_id=tenant_id, deleted=False
    ).order_by("-utime").all()
    versions = await TicketFlowVersion.filter(
        ticket_flow_id__in=[flow.id for flow in flows],
        tenant_id=tenant_id,
        deleted=False,
    ).order_by("-ctime").all()
    versions_by_flow = {}
    for version in versions:
        versions_by_flow.setdefault(version.ticket_flow_id, []).append(version)

    return response_ok([
        {
            **flow.json,
            "version": (
                active_snapshot(versions_by_flow.get(flow.id, [])).json
                if versions_by_flow.get(flow.id)
                else None
            ),
            "version_count": len(versions_by_flow.get(flow.id, [])),
        }
        for flow in flows
    ])


@ticket_bp.post("/flow/export")
@openapi.definition(summary="导出流程包（含全部版本）")
@requires_permission("ticket_flow:r")
async def flow_export_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    payload = request.json or {}
    requested_ids = [
        str(flow_id) for flow_id in payload.get("flow_ids", []) if flow_id
    ]
    if not requested_ids:
        raise CustomError(ErrorCode.PARAM_ERROR)

    from libs.security.authorization import ticket_flow_ids_for_user

    readable_ids = set(await ticket_flow_ids_for_user(account, tenant_id, "r"))
    if not set(requested_ids).issubset(readable_ids):
        raise CustomError(ErrorCode.UNAUTH)

    flows = await TicketFlow.filter(
        id__in=requested_ids, tenant_id=tenant_id, deleted=False
    ).order_by("name").all()
    if len(flows) != len(set(requested_ids)):
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    ticket_types = await TicketType.filter(
        id__in=[flow.ticket_type_id for flow in flows],
        tenant_id=tenant_id,
        deleted=False,
    ).all()
    ticket_type_by_id = {ticket_type.id: ticket_type for ticket_type in ticket_types}
    versions = await TicketFlowVersion.filter(
        ticket_flow_id__in=[flow.id for flow in flows],
        tenant_id=tenant_id,
        deleted=False,
    ).order_by("-ctime").all()
    versions_by_flow = {}
    for version in versions:
        versions_by_flow.setdefault(version.ticket_flow_id, []).append(version)

    archive_flows = []
    for flow in flows:
        ticket_type = ticket_type_by_id.get(flow.ticket_type_id)
        if ticket_type is None:
            raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)
        archive_flows.append({
            "name": flow.name,
            "remark": flow.remark,
            "ticket_type": {
                "name": ticket_type.name,
                "code": ticket_type.code,
                "description": ticket_type.description,
                "icon": ticket_type.icon,
                "sla_config": ticket_type.sla_config or {},
            },
            "versions": [
                {
                    "version": version.version,
                    "status": version.status,
                    "change_desc": version.change_desc,
                    "ctime": _datetime_text(version.ctime),
                    "utime": _datetime_text(version.utime),
                    "flow_json": version.flow_json or {},
                    "form_schema": version.form_schema or {},
                }
                for version in versions_by_flow.get(flow.id, [])
            ],
        })

    archive = build_flow_archive(
        archive_flows,
        datetime.now(TZ).isoformat(),
    )
    return raw(
        archive,
        content_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="ticket-flow-export.zip"'
        },
    )


@ticket_bp.post("/flow/import")
@openapi.definition(summary="导入流程包为可编辑草稿")
@requires_permission("ticket_flow:u")
async def flow_import_view(request: Request):
    file_obj = request.files.get("file")
    if not file_obj:
        raise CustomError(ErrorCode.PARAM_ERROR)
    try:
        package = parse_flow_archive(file_obj.body)
    except ValueError as exc:
        logger.warning("Invalid ticket-flow package: %s", exc)
        raise CustomError(ErrorCode.PARAM_ERROR) from exc

    tenant_id = request.ctx.session.get("tenant_id")
    user_id = request.ctx.session.get("sub")
    from libs.security.authorization import (
        TENANT_ADMIN_ROLE_NAME,
        get_superadmin_role,
    )
    from models import Role
    from models.ticket import TicketFlowPermission

    superadmin_role = await get_superadmin_role()
    tenant_admin_role = await Role.filter(
        tenant_id=tenant_id,
        name=TENANT_ADMIN_ROLE_NAME,
        deleted=False,
    ).first()

    imported = 0
    async with in_transaction() as conn:
        for item in package["flows"]:
            type_payload = item["ticket_type"]
            ticket_type = await TicketType.filter(
                tenant_id=tenant_id,
                code=type_payload["code"].strip()[:32],
                deleted=False,
            ).using_db(conn).first()
            if ticket_type is None:
                ticket_type = await TicketType.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    name=type_payload["name"].strip()[:64],
                    code=type_payload["code"].strip()[:32],
                    description=(type_payload.get("description") or "")[:512] or None,
                    icon=(type_payload.get("icon") or "")[:128] or None,
                    sla_config=type_payload.get("sla_config") or {},
                    internal=False,
                )

            flow = await TicketFlow.create(
                using_db=conn,
                tenant_id=tenant_id,
                name=item["name"].strip()[:128],
                remark=(item.get("remark") or "")[:512] or None,
                ticket_type_id=ticket_type.id,
                creator_id=user_id,
                internal=False,
            )
            permissions = ["r", "u", "d", "s"]
            await TicketFlowPermission.create(
                using_db=conn,
                tenant_id=tenant_id,
                flow_id=flow.id,
                users_id=user_id,
                permissions=permissions,
            )
            if superadmin_role:
                await TicketFlowPermission.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    flow_id=flow.id,
                    roles_id=superadmin_role.id,
                    permissions=permissions,
                )
            if tenant_admin_role:
                await TicketFlowPermission.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    flow_id=flow.id,
                    roles_id=tenant_admin_role.id,
                    permissions=permissions,
                )

            active = active_snapshot(item["versions"])
            for snapshot in item["versions"]:
                is_active = snapshot is active
                await TicketFlowVersion.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    ticket_flow_id=flow.id,
                    version="draft" if is_active else str(
                        snapshot.get("version") or "history"
                    )[:32],
                    change_desc=snapshot.get("change_desc"),
                    status=0 if is_active else 2,
                    flow_json=snapshot.get("flow_json") or {},
                    form_schema=snapshot.get("form_schema") or {},
                    creator_id=user_id,
                    updator_id=user_id,
                )
            imported += 1

    return response_ok({"imported": imported})


@ticket_bp.get("/flow/submit-options")
@openapi.definition(summary="可提单流程及已发布版本")
@requires_permission("ticket:r")
async def flow_submit_options_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    from libs.security.authorization import ticket_flow_ids_for_user

    flow_ids = await ticket_flow_ids_for_user(account, tenant_id, "s")
    flows = await TicketFlow.filter(
        id__in=flow_ids, tenant_id=tenant_id, deleted=False
    ).order_by("name").all()
    versions = await TicketFlowVersion.filter(
        ticket_flow_id__in=[flow.id for flow in flows],
        tenant_id=tenant_id,
        status=1,
        deleted=False,
    ).order_by("-ctime").all()
    versions_by_flow = {}
    for version in versions:
        versions_by_flow.setdefault(version.ticket_flow_id, []).append(version)
    return response_ok([
        {
            **flow.json,
            "versions": [version.json for version in versions_by_flow.get(flow.id, [])],
        }
        for flow in flows
        if versions_by_flow.get(flow.id)
    ])


@ticket_bp.get("/flow/config-options")
@openapi.definition(summary="剧本工单节点可选流程及版本")
@requires_permission("ticket_flow:r")
async def flow_config_options_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    from libs.security.authorization import ticket_flow_ids_for_user

    flow_ids = await ticket_flow_ids_for_user(account, tenant_id, "r")
    flows = await TicketFlow.filter(
        id__in=flow_ids, tenant_id=tenant_id, deleted=False
    ).order_by("name").all()
    versions = await TicketFlowVersion.filter(
        ticket_flow_id__in=[flow.id for flow in flows],
        tenant_id=tenant_id,
        status__in=[1, 2],
        deleted=False,
    ).order_by("-ctime").all()
    versions_by_flow = {}
    for version in versions:
        versions_by_flow.setdefault(version.ticket_flow_id, []).append(version)
    return response_ok([
        {
            **flow.json,
            "versions": [version.json for version in versions_by_flow.get(flow.id, [])],
        }
        for flow in flows
        if versions_by_flow.get(flow.id)
    ])


@ticket_bp.get("/flow/detail/<flow_id>")
@openapi.definition(summary="流程详情（含最新草稿或已发布版本）")
@requires_permission("ticket_flow:r")
@ticket_flow_permission("r")
async def flow_detail_view(request: Request, flow_id: str):
    tenant_id = request.ctx.session.get("tenant_id")
    from models.ticket import TicketFlow, TicketFlowVersion

    flow = await TicketFlow.filter(
        id=flow_id, tenant_id=tenant_id, deleted=False
    ).first()
    if flow is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    versions = await TicketFlowVersion.filter(
        ticket_flow_id=flow_id, tenant_id=tenant_id, deleted=False
    ).order_by("-ctime").all()
    version = active_snapshot(versions)

    return response_ok({
        **flow.json,
        "version": version.json if version else None,
    })


@ticket_bp.post("/flow/basic-info")
@openapi.definition(summary="修改流程基础信息")
@requires_permission("ticket_flow:u")
@ticket_flow_permission("u")
async def flow_basic_info_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    payload = request.json or {}
    flow_id = payload.get("flow_id")
    name = payload.get("name", "").strip()
    if not flow_id or not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    flow = await TicketFlow.filter(
        id=flow_id, tenant_id=tenant_id, deleted=False
    ).first()
    if flow is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    flow.name = name
    flow.remark = payload.get("remark")
    await flow.save()
    return response_ok(flow.json)


@ticket_bp.post("/flow/clone")
@openapi.definition(summary="按指定版本克隆流程")
@requires_permission("ticket_flow:u")
@ticket_flow_permission("r")
async def flow_clone_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    user_id = request.ctx.session.get("sub")
    payload = request.json or {}
    source_flow_id = payload.get("flow_id")
    source_version_id = payload.get("version_id")
    name = payload.get("name", "").strip()
    if not source_flow_id or not source_version_id or not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    source_flow = await TicketFlow.filter(
        id=source_flow_id, tenant_id=tenant_id, deleted=False
    ).first()
    if source_flow is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)
    source_version = await TicketFlowVersion.filter(
        id=source_version_id,
        ticket_flow_id=source_flow_id,
        tenant_id=tenant_id,
        deleted=False,
    ).first()
    if source_version is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    from libs.security.authorization import (
        TENANT_ADMIN_ROLE_NAME,
        get_superadmin_role,
    )
    from models import Role
    from models.ticket import TicketFlowPermission

    superadmin_role = await get_superadmin_role()
    tenant_admin_role = await Role.filter(
        tenant_id=tenant_id,
        name=TENANT_ADMIN_ROLE_NAME,
        deleted=False,
    ).first()

    async with in_transaction() as conn:
        flow = await TicketFlow.create(
            using_db=conn,
            tenant_id=tenant_id,
            name=name,
            remark=source_flow.remark,
            ticket_type_id=source_flow.ticket_type_id,
            creator_id=user_id,
            internal=False,
        )
        version = await TicketFlowVersion.create(
            using_db=conn,
            tenant_id=tenant_id,
            ticket_flow_id=flow.id,
            version="draft",
            flow_json=deepcopy(source_version.flow_json or {}),
            form_schema=deepcopy(source_version.form_schema or {}),
            status=0,
            creator_id=user_id,
            updator_id=user_id,
        )

        permissions = ["r", "u", "d", "s"]
        await TicketFlowPermission.create(
            using_db=conn,
            tenant_id=tenant_id,
            flow_id=flow.id,
            users_id=user_id,
            permissions=permissions,
        )
        if superadmin_role:
            await TicketFlowPermission.create(
                using_db=conn,
                tenant_id=tenant_id,
                flow_id=flow.id,
                roles_id=superadmin_role.id,
                permissions=permissions,
            )
        if tenant_admin_role:
            await TicketFlowPermission.create(
                using_db=conn,
                tenant_id=tenant_id,
                flow_id=flow.id,
                roles_id=tenant_admin_role.id,
                permissions=permissions,
            )

    return response_ok({**flow.json, "version": version.json})


@ticket_bp.post("/flow/save")
@openapi.definition(summary="保存流程草稿")
@requires_permission("ticket_flow:u")
async def flow_save_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    user_id = request.ctx.session.get("sub")
    from models.ticket import TicketFlow, TicketFlowVersion, TicketFlowPermission
    from libs.security.authorization import (
        TENANT_ADMIN_ROLE_NAME,
        get_superadmin_role,
    )
    from models import Role

    payload = request.json or {}
    flow_id = payload.get("id")
    name = payload.get("name", "").strip()
    flow_json = payload.get("flow_json") or {}
    form_schema = payload.get("form_schema") or {}

    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if flow_id:
        account = request.ctx.session.get("account_info")
        if account is None:
            raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
        if not await check_ticket_flow_permission(
            request, account, flow_id, ("u",)
        ):
            raise CustomError(ErrorCode.TICKET_FLOW_PERM_DENIED)

        async with in_transaction() as conn:
            # 流程元数据与草稿内容必须一起成功或一起回滚。
            flow = await TicketFlow.filter(
                id=flow_id, tenant_id=tenant_id, deleted=False
            ).using_db(conn).first()
            if flow is None:
                raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)
            flow.name = name
            flow.remark = payload.get("remark")
            await flow.save(using_db=conn)

            version = await TicketFlowVersion.filter(
                ticket_flow_id=flow_id,
                tenant_id=tenant_id,
                status=0,
                deleted=False,
            ).using_db(conn).first()
            if version:
                version.flow_json = flow_json
                version.form_schema = form_schema
                version.updator_id = user_id
                await version.save(using_db=conn)
            else:
                version = await TicketFlowVersion.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    ticket_flow_id=flow_id,
                    version="draft",
                    flow_json=flow_json,
                    form_schema=form_schema,
                    status=0,
                    creator_id=user_id,
                )
    else:
        # 新建流程
        ticket_type_id = payload.get("ticket_type_id")
        if not ticket_type_id:
            raise CustomError(ErrorCode.PARAM_ERROR)

        ticket_type = await TicketType.filter(
            id=ticket_type_id, tenant_id=tenant_id, deleted=False
        ).first()
        if ticket_type is None:
            raise CustomError(ErrorCode.TICKET_TYPE_NOT_FOUND)

        superadmin_role = await get_superadmin_role()
        tenant_admin_role = await Role.filter(
            tenant_id=tenant_id,
            name=TENANT_ADMIN_ROLE_NAME,
            deleted=False,
        ).first()

        async with in_transaction() as conn:
            flow = await TicketFlow.create(
                using_db=conn,
                tenant_id=tenant_id,
                name=name,
                remark=payload.get("remark"),
                ticket_type_id=ticket_type_id,
                creator_id=user_id,
                internal=False,
            )
            version = await TicketFlowVersion.create(
                using_db=conn,
                tenant_id=tenant_id,
                ticket_flow_id=flow.id,
                version="draft",
                flow_json=flow_json,
                form_schema=form_schema,
                status=0,
                creator_id=user_id,
            )

            # 创建人、超管、租户管理员的授权与流程创建原子提交。
            perms = ["r", "u", "d", "s"]
            await TicketFlowPermission.create(
                using_db=conn,
                tenant_id=tenant_id,
                flow_id=flow.id,
                users_id=user_id,
                permissions=perms,
            )
            if superadmin_role:
                await TicketFlowPermission.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    flow_id=flow.id,
                    roles_id=superadmin_role.id,
                    permissions=perms,
                )
            if tenant_admin_role:
                await TicketFlowPermission.create(
                    using_db=conn,
                    tenant_id=tenant_id,
                    flow_id=flow.id,
                    roles_id=tenant_admin_role.id,
                    permissions=perms,
                )

    return response_ok({**flow.json, "version": version.json})


@ticket_bp.post("/flow/publish")
@openapi.definition(summary="发布流程（含完整性校验）")
@requires_permission("ticket_flow:u")
@ticket_flow_permission("u")
async def flow_publish_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    user_id = request.ctx.session.get("sub")
    from models.ticket import TicketFlowVersion
    from services.ticket.flow_validator import (
        validate_flow_before_publish,
        validate_flow_structure,
    )
    from datetime import datetime
    from settings import TZ

    payload = request.json or {}
    flow_id = payload.get("flow_id")
    change_desc = payload.get("change_desc", "").strip()

    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 获取草稿版本
    draft = await TicketFlowVersion.filter(
        ticket_flow_id=flow_id, tenant_id=tenant_id, status=0, deleted=False
    ).first()
    if draft is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    # 完整性校验（SPEC §4.2）
    structure_errors = validate_flow_structure(draft.flow_json or {})
    blocking_errors = [
        error for error in structure_errors if error.get("level") == "error"
    ]
    validation_input = {
        **(draft.flow_json or {}),
        "form_schema": draft.form_schema or {},
    }
    reference_errors = validate_flow_before_publish(validation_input)
    if blocking_errors or reference_errors:
        raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)

    # 发布：旧版本进入历史态（2），再将草稿切为已发布（1）。
    now = datetime.now(TZ)
    async with in_transaction() as conn:
        await TicketFlowVersion.filter(
            ticket_flow_id=flow_id,
            tenant_id=tenant_id,
            status=1,
            deleted=False,
        ).using_db(conn).update(status=2)
        draft.status = 1
        draft.version = now.strftime("v%Y%m%d%H%M%S")
        draft.change_desc = change_desc
        draft.updator_id = user_id
        await draft.save(using_db=conn)

    return response_ok(draft.json)


@ticket_bp.post("/flow/delete")
@openapi.definition(summary="删除流程（软删除）")
@requires_permission("ticket_flow:d")
@ticket_flow_permission("d")
async def flow_delete_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    from models.ticket import TicketFlow

    payload = request.json or {}
    flow_id = payload.get("flow_id")
    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    flow = await TicketFlow.filter(
        id=flow_id, tenant_id=tenant_id, deleted=False
    ).first()
    if flow is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    flow.deleted = True
    await flow.save()
    return response_ok({"message": "删除成功"})


@ticket_bp.get("/flow/versions/<flow_id>")
@openapi.definition(summary="流程版本历史")
@requires_permission("ticket_flow:r")
@ticket_flow_permission("r")
async def flow_versions_view(request: Request, flow_id: str):
    tenant_id = request.ctx.session.get("tenant_id")
    from models.ticket import TicketFlowVersion

    versions = await TicketFlowVersion.filter(
        ticket_flow_id=flow_id, tenant_id=tenant_id, deleted=False
    ).order_by("-ctime").all()

    return response_ok([v.json for v in versions])


@ticket_bp.get("/flow/permissions/get")
@openapi.definition(
    summary="获取流程权限配置",
    parameter=[
        {"name": "flow_id", "in": "query", "required": True, "schema": {"type": "string"}},
    ],
)
@requires_permission("ticket_flow:r")
@ticket_flow_permission("r")
async def flow_permissions_get_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    flow_id = request.args.get("flow_id")
    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    from models.ticket import TicketFlowPermission

    permissions = await TicketFlowPermission.filter(
        flow_id=flow_id, tenant_id=tenant_id, deleted=False
    ).all()

    return response_ok([p.json for p in permissions])


@ticket_bp.post("/flow/permissions/save")
@openapi.definition(summary="保存流程权限配置")
@requires_permission("ticket_flow:u")
@ticket_flow_permission("u")
async def flow_permissions_save_view(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    from models.ticket import TicketFlowPermission

    payload = request.json or {}
    flow_id = payload.get("flow_id")
    users = payload.get("users") or {}  # {user_id: ["r","u"]}
    roles = payload.get("roles") or {}  # {role_id: ["r","u"]}

    if not flow_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    flow = await TicketFlow.filter(
        id=flow_id, tenant_id=tenant_id, deleted=False
    ).first()
    if flow is None:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    async with in_transaction() as conn:
        # 只软删当前有效权限，保留历史记录。
        await TicketFlowPermission.filter(
            flow_id=flow_id, tenant_id=tenant_id, deleted=False
        ).using_db(conn).update(deleted=True)

        for permission_user_id, perms in users.items():
            await TicketFlowPermission.create(
                using_db=conn,
                tenant_id=tenant_id,
                flow_id=flow_id,
                users_id=permission_user_id,
                permissions=perms,
            )
        for role_id, perms in roles.items():
            await TicketFlowPermission.create(
                using_db=conn,
                tenant_id=tenant_id,
                flow_id=flow_id,
                roles_id=role_id,
                permissions=perms,
            )

    return response_ok({"message": "权限保存成功"})


@ticket_bp.route("/callback", methods=["GET", "POST"])
@openapi.definition(summary="外部回调推进工单")
async def callback_view(request: Request):
    """Consume a callback token exactly once, then advance its ticket."""
    payload = request.json if request.method == "POST" else {}
    payload = payload if isinstance(payload, dict) else {}
    token = request.args.get("token") or payload.get("token")
    if not token or not isinstance(token, str):
        raise CustomError(ErrorCode.TICKET_CALLBACK_TOKEN_INVALID)

    async with in_transaction() as conn:
        ticket = (
            await Ticket.filter(callback_token=token, deleted=False)
            .using_db(conn)
            .select_for_update()
            .first()
        )
        if ticket is None:
            raise CustomError(ErrorCode.TICKET_CALLBACK_TOKEN_INVALID)

        supplied_ticket_id = request.args.get("ticket_id") or payload.get("ticket_id")
        supplied_tenant_id = request.args.get("tenant_id") or payload.get("tenant_id")
        if (
            supplied_ticket_id is not None
            and supplied_ticket_id != ticket.id
        ) or (
            supplied_tenant_id is not None
            and supplied_tenant_id != ticket.tenant_id
        ):
            raise CustomError(ErrorCode.TICKET_CALLBACK_TOKEN_INVALID)

        ticket.callback_token = None
        await ticket.save(
            update_fields=["callback_token", "utime"], using_db=conn
        )
        await TicketActivity.create(
            using_db=conn,
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            actor_id=None,
            action="callback",
            content="外部回调推进工单",
            payload={
                "executor": "外部回调",
                "actor": {
                    "username": "callback",
                    "display_name": "外部回调",
                },
                "remote_addr": getattr(request, "remote_addr", None),
            },
        )

    previous_user_ids = set()
    try:
        from services.ticket.websocket import active_approver_ids

        previous_user_ids = await active_approver_ids(ticket.tenant_id, ticket.id)
    except Exception:
        logger.exception(
            "外部回调读取原审批人失败: ticket_id=%s", ticket.id
        )

    await advance_ticket(ticket, actor_id=None, executor="外部回调")
    await _push_pending_after_commit(
        ticket.tenant_id, ticket.id, previous_user_ids
    )
    return response_ok()
