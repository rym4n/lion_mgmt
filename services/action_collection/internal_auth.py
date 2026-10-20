"""Session and caller checks for collection HTTP adapters."""
from __future__ import annotations

from core.maintenance import DEFAULT_TENANT_ID
from services.action_collection.errors import CollectionError


def reject_tenant_fields(value) -> None:
    if isinstance(value, dict):
        if "tenant" in value or "tenant_id" in value:
            raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
        for child in value.values():
            reject_tenant_fields(child)
    elif isinstance(value, list):
        for child in value:
            reject_tenant_fields(child)


def session(request) -> dict:
    value = getattr(getattr(request, "ctx", None), "session", None)
    if not isinstance(value, dict):
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    return value


def session_tenant_id(request) -> str:
    tenant_id = session(request).get("tenant_id")
    if not tenant_id:
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    return str(tenant_id)


def actor_id(request) -> str:
    current = session(request)
    account = current.get("account_info")
    actor = current.get("sub") or getattr(account, "id", None)
    if not actor:
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    return str(actor)


def require_internal_caller(request, data: dict) -> None:
    if str(session(request).get("tenant_id") or "") != DEFAULT_TENANT_ID:
        raise CollectionError("COLLECTION_CALLER_FORBIDDEN", 403)
    reject_tenant_fields(data)
