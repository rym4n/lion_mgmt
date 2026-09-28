# coding=utf-8

DEFAULT_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"


def can_enable_maintenance_mode(username, tenant_id):
    return username == "admin" and str(tenant_id or "") == DEFAULT_TENANT_ID


def should_clear_maintenance_mode(tenant_id):
    return str(tenant_id or "") != DEFAULT_TENANT_ID


def can_switch_tenant(maintenance_mode):
    return not bool(maintenance_mode)


def is_api_account(account) -> bool:
    """Return whether the request account is an API token account."""
    account_type = (
        account.get("type")
        if isinstance(account, dict)
        else getattr(account, "type", None)
    )
    return str(account_type or "").strip().lower() == "api"


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def effective_maintenance_mode(account, cached_value) -> bool:
    """Apply the server-owned maintenance scope to one authenticated account."""
    if is_api_account(account):
        return False
    return _as_bool(cached_value, False)


async def current_maintenance_mode(request) -> bool:
    """Read maintenance scope, forcing API accounts into ordinary mode."""
    session = request.ctx.session
    account = session.get("account_info")
    if is_api_account(account):
        return False
    session_id = session.get("id")
    if not session_id:
        return False
    cached_value = await request.app.ctx.cache_client.get(session_id)
    return effective_maintenance_mode(account, cached_value)
