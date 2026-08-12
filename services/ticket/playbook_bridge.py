# coding=utf-8
"""剧本与工单之间的边界适配。

剧本消息格式在不同版本中使用过 ``ticket``、``ticket_config`` 和直接字段；
本模块把这些兼容细节收敛在这里，工单引擎只接收标准 create_ticket payload。
"""

from datetime import datetime
from typing import Any

from libs.kafka import aproduce
from models import Account
from models.ticket import (
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    SOURCE_PLAYBOOK,
    TICKET_STATUS_COMPLETED,
    TicketNodeInstance,
)
from services.ticket.engine import change_node_approvers
from services.ticket.manager import create_ticket
from settings import TASK_TOPIC, TZ


CALLBACK_FIELDS = (
    "ticket_id", "ticket_no", "status", "result", "operator",
    "completed_at", "title", "creator", "approval_result", "metadata",
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [
            item.strip()
            for item in value.replace(";", ",").split(",")
            if item.strip()
        ]
    if isinstance(value, (tuple, set, list)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def parse_approver_config(config: Any) -> dict[str, list[str]]:
    """解析审批人配置，保留用户和角色两个命名空间。"""
    if isinstance(config, str):
        return {"users": _as_list(config), "roles": []}
    if isinstance(config, (list, tuple, set)):
        return {"users": _as_list(config), "roles": []}
    if not isinstance(config, dict):
        return {"users": [], "roles": []}
    nested = config.get("approvers")
    if nested is not None and nested is not config:
        parsed = parse_approver_config(nested)
    else:
        parsed = {"users": [], "roles": []}
    parsed["users"] = list(dict.fromkeys(
        parsed["users"] + _as_list(config.get("users", config.get("user_ids")))
    ))
    parsed["roles"] = list(dict.fromkeys(
        parsed["roles"] + _as_list(config.get("roles", config.get("role_ids")))
    ))
    return parsed


def parse_approvers(config: Any) -> list[str]:
    """返回配置中审批人的扁平去重列表（兼容旧调用方）。"""
    parsed = parse_approver_config(config)
    return list(dict.fromkeys(parsed["users"] + parsed["roles"]))


def _ticket_config(log: dict, node: dict | None = None) -> dict:
    props = (node or {}).get("properties") or {}
    config = (
        log.get("ticket_config") or log.get("ticket")
        or props.get("ticket") or props.get("ticket_config")
    )
    if isinstance(config, dict):
        merged = dict(config)
        for source in (props, log):
            for key in (
                "ticket_type_id", "ticket_flow_id", "flow_version_id",
                "title", "priority", "form_data",
                "approvers", "users", "roles",
            ):
                if key in source and key not in merged:
                    merged[key] = source[key]
        return merged
    merged = {}
    for source in (props, log):
        for key in (
            "ticket_type_id", "ticket_flow_id", "flow_version_id",
            "title", "priority", "form_data",
            "approvers", "users", "roles",
        ):
            if key in source:
                merged[key] = source[key]
    return merged


def _params_dict(params: Any) -> dict:
    if isinstance(params, dict):
        return dict(params)
    if isinstance(params, list):
        return {
            str(item["name"]): item.get("value")
            for item in params
            if isinstance(item, dict) and item.get("name")
        }
    return {}


async def create_ticket_from_playbook(
    tenant_id: str, creator_id: str | None, log: dict, node: dict | None = None,
):
    """由剧本等待节点建立来源工单；没有工单配置时返回 None。"""
    config = _ticket_config(log, node)
    ticket_type_id = config.get("ticket_type_id")
    ticket_flow_id = config.get("ticket_flow_id")
    flow_version_id = config.get("flow_version_id")
    if not ticket_type_id and not ticket_flow_id and not flow_version_id:
        return None
    params = _params_dict(log.get("params"))
    payload = {
        "title": (
            config.get("title") or log.get("node_name")
            or log.get("content") or "剧本工单"
        ),
        "priority": config.get("priority", 1),
        "form_data": config.get("form_data") or params,
        "source": SOURCE_PLAYBOOK,
        "origin_run_id": log.get("run_id"),
        "origin_node_id": log.get("audit_app"),
        "origin_flow_id": log.get("flow_id"),
        "origin_params": params,
        "callback_token": log.get("callback_token"),
    }
    if ticket_type_id:
        payload["ticket_type_id"] = ticket_type_id
    if ticket_flow_id:
        payload["ticket_flow_id"] = ticket_flow_id
    if flow_version_id:
        payload["flow_version_id"] = flow_version_id
    return await create_ticket(
        tenant_id,
        creator_id,
        payload,
        allow_historical_version=True,
    )


async def add_approvers(ticket, node, config: Any, connection=None) -> list[str]:
    """将解析出的审批人加入当前节点，并通过引擎记录变更活动。"""
    parsed = parse_approver_config(config)
    accounts = []
    if parsed["users"]:
        accounts.extend(await Account.filter(
            id__in=parsed["users"], tenant_id=ticket.tenant_id,
            deleted=False, disabled=False,
        ).all())
    if parsed["roles"]:
        accounts.extend(await Account.filter(
            roles__id__in=parsed["roles"], tenant_id=ticket.tenant_id,
            deleted=False, disabled=False,
        ).all())
    approver_ids = list(dict.fromkeys(account.id for account in accounts))
    if not approver_ids:
        return []
    return await change_node_approvers(
        ticket, node, "add_approver", approver_ids,
        actor_id=None, connection=connection,
    )


async def add_approvers_to_current_node(ticket, config: Any, connection=None) -> list[str]:
    node = await TicketNodeInstance.filter(
        ticket_id=ticket.id, tenant_id=ticket.tenant_id, deleted=False,
        status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
    ).order_by("ctime").first()
    if node is None:
        return []
    return await add_approvers(ticket, node, config, connection=connection)


def build_callback_payload(
    ticket, operator: str | None = None, result: str | None = None,
    approval_result: dict | None = None, metadata: dict | None = None,
) -> dict:
    completed_at = getattr(ticket, "closed_at", None) or datetime.now(TZ)
    try:
        creator = getattr(ticket, "creator", None)
    except Exception:
        creator = None
    creator_name = (
        getattr(creator, "display_name", None)
        or getattr(creator, "username", None)
        or getattr(ticket, "creator_id", None)
        or ""
    )
    payload = {
        "ticket_id": str(getattr(ticket, "id", "")),
        "ticket_no": str(getattr(ticket, "ticket_no", "")),
        "status": int(getattr(ticket, "status", 0)),
        "result": str(result if result is not None else {
            1: "approved", 2: "rejected", 3: "timeout",
            4: "withdrawn", 5: "force_closed",
        }.get(getattr(ticket, "status", 0), "unknown")),
        "operator": str(operator or ""),
        "completed_at": (
            completed_at.isoformat()
            if hasattr(completed_at, "isoformat") else str(completed_at)
        ),
        "title": str(getattr(ticket, "title", "") or ""),
        "creator": str(creator_name),
        "approval_result": approval_result if isinstance(approval_result, dict) else {},
        "metadata": metadata if isinstance(metadata, dict) else {},
    }
    return {key: payload[key] for key in CALLBACK_FIELDS}


async def callback_playbook(
    ticket, operator: str | None = None, result: str | None = None,
    approval_result: dict | None = None, metadata: dict | None = None,
    approval_status: int | None = None, connection=None,
) -> dict:
    """发布终态回传，保留 lion_action 兼容的恢复参数。"""
    if approval_result is None:
        query = TicketNodeInstance.filter(
            ticket_id=ticket.id, tenant_id=ticket.tenant_id, deleted=False,
        )
        if connection is not None:
            query = query.using_db(connection)
        nodes = await query.order_by("ctime").all()
        approval_result = {
            str(node.node_id): dict(node.results or {})
            for node in nodes if node.results
        }
    if metadata is None:
        metadata = {
            "run_id": getattr(ticket, "origin_run_id", None),
            "flow_id": getattr(ticket, "origin_flow_id", None),
            "node_id": getattr(ticket, "origin_node_id", None),
        }
    payload = build_callback_payload(ticket, operator, result, approval_result, metadata)
    flow_input = dict(getattr(ticket, "origin_params", None) or {})
    flow_input["approval_result"] = payload
    if approval_status is None:
        if result in ("approve", "approved"):
            approval_status = 1
        elif result in ("reject", "rejected"):
            approval_status = 2
        else:
            approval_status = 1 if payload["status"] == TICKET_STATUS_COMPLETED else 2
    await aproduce(TASK_TOPIC, {
        "run_type": "flow",
        "run_id": getattr(ticket, "origin_run_id", None),
        "run_args": {
            "flow_id": getattr(ticket, "origin_flow_id", None),
            "flow_input": flow_input,
            "approval_node_id": getattr(ticket, "origin_node_id", None),
            "approval_status": approval_status,
            "executor": operator,
        },
        "ticket_result": payload,
        "tenant_id": getattr(ticket, "tenant_id", None),
    })
    return payload


# Naming aliases used by integrations and tests.
create_from_playbook = create_ticket_from_playbook
parse_approvers_from_config = parse_approver_config
callback = callback_playbook
