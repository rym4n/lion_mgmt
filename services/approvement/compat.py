# coding=utf-8

from datetime import datetime
from typing import Any

from tortoise.transactions import in_transaction

from libs.utils import strip_timezone_to_seconds
from models import Account, Approvement
from settings import TZ


def _as_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _has_ticket_config(log: dict, node: dict | None) -> bool:
    properties = (node or {}).get("properties") or {}
    config = (
        log.get("ticket_config")
        or log.get("ticket")
        or properties.get("ticket_config")
        or properties.get("ticket")
    )
    if isinstance(config, dict) and any(
        config.get(key)
        for key in ("ticket_type_id", "ticket_flow_id", "flow_version_id")
    ):
        return True
    return any(
        source.get(key)
        for source in (properties, log)
        for key in ("ticket_type_id", "ticket_flow_id", "flow_version_id")
    )


def is_legacy_approval_event(log: dict, node: dict | None) -> bool:
    """Identify only stored manual nodes; malformed ticket nodes stay tickets."""

    node_type = (node or {}).get("type")
    if node_type:
        return node_type == "approval-node"
    return not _has_ticket_config(log, node) and bool(log.get("users") or log.get("roles"))


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        parsed = datetime.now()
    if parsed.tzinfo is not None:
        return parsed.astimezone(TZ).replace(tzinfo=None)
    return parsed


async def _resolve_approvers(
    tenant_id: str,
    log: dict,
    node: dict | None,
    connection,
) -> list[Account]:
    properties = (node or {}).get("properties") or {}
    user_ids = _as_ids(
        properties.get("users")
        or properties.get("approvers")
        or properties.get("approver_ids")
        or log.get("users")
    )
    role_ids = _as_ids(properties.get("roles") or log.get("roles"))
    accounts: dict[str, Account] = {}
    if user_ids:
        records = await Account.filter(
            id__in=user_ids,
            tenant_id=tenant_id,
            deleted=False,
            disabled=False,
        ).using_db(connection).all()
        accounts.update({record.id: record for record in records})
    if role_ids:
        records = await Account.filter(
            roles__id__in=role_ids,
            roles__tenant_id=tenant_id,
            roles__deleted=False,
            tenant_id=tenant_id,
            deleted=False,
            disabled=False,
        ).using_db(connection).distinct().all()
        accounts.update({record.id: record for record in records})
    return list(accounts.values())


async def ensure_legacy_approvement(
    log: dict,
    node: dict | None,
) -> tuple[Approvement, bool, list[Account]]:
    """Create or reuse one approval record for a legacy waiting node."""

    tenant_id = str(log.get("tenant_id") or "")
    run_id = str(log.get("run_id") or "")
    audit_node = str(log.get("audit_app") or "")
    flow_id = str(log.get("flow_id") or "")
    if not tenant_id or not run_id or not audit_node or not flow_id:
        raise ValueError("legacy approval event is missing identity fields")

    async with in_transaction() as connection:
        await connection.execute_query(
            "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
            [tenant_id, f"{run_id}:{audit_node}"],
        )
        approve = await Approvement.filter(
            tenant_id=tenant_id,
            run_id=run_id,
            audit_node=audit_node,
            deleted=False,
        ).using_db(connection).order_by("-ctime").first()
        created = approve is None
        if approve is None:
            approve = await Approvement.create(
                using_db=connection,
                title=str(log.get("node_name") or "人工审批")[:50],
                flow_id=flow_id,
                run_id=run_id,
                content=str(log.get("content") or ""),
                params=log.get("params"),
                status=0,
                expire_time=_parse_datetime(log.get("expire_time")),
                created_at=_parse_datetime(log.get("created_at")),
                audit_node=audit_node,
                approve_type=2 if str(log.get("approve_type")) == "2" else 1,
                callback_token=str(log.get("callback_token") or "")[:64],
                tenant_id=tenant_id,
            )

        accounts = await _resolve_approvers(tenant_id, log, node, connection)
        existing_rows = await connection.execute_query_dict(
            "SELECT account_id FROM approve_accounts WHERE approvement_id = $1",
            [approve.id],
        )
        existing_ids = {row["account_id"] for row in existing_rows}
        for account in accounts:
            if account.id in existing_ids:
                continue
            await connection.execute_query(
                """
                INSERT INTO approve_accounts (approvement_id, account_id)
                VALUES ($1, $2)
                ON CONFLICT (approvement_id, account_id) DO NOTHING
                """,
                [approve.id, account.id],
            )

    return approve, created, accounts


def build_legacy_approval_notice(
    approve: Approvement,
    *,
    sender_id: str | None,
    sender_name: str | None,
) -> dict:
    return {
        "type": "approval-info",
        "message_id": str(approve.id),
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_display_name": sender_name,
        "content": {
            "title": approve.title,
            "approve_type": approve.approve_type,
            "content": approve.content,
            "created_at": strip_timezone_to_seconds(approve.created_at),
            "expire_time": strip_timezone_to_seconds(approve.expire_time),
        },
        "image_content": "",
        "timestamp": "",
        "room_id": "",
    }
