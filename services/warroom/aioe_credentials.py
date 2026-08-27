# coding=utf-8
import uuid

from libs.security.authentication import token_encode
from settings import AIOE_INTERNAL_URL


WARROOM_API_TOKEN_EXPIRATION_SECONDS = 5 * 60


def build_warroom_aioe_credentials(account, tenant_id: str) -> dict[str, str]:
    """Delegate the authenticated user's existing RBAC scope to lion_agent."""
    if (
        not tenant_id
        or not getattr(account, "id", None)
        or getattr(account, "deleted", False)
        or getattr(account, "disabled", False)
    ):
        raise ValueError("warroom AIOE delegation requires an active account")

    token = token_encode(
        account_id=account.id,
        username=account.username,
        display_name=account.display_name,
        mfa_enable=bool(getattr(account, "mfa_enable", False)),
        tenant_id=str(tenant_id),
        token_id=str(uuid.uuid4()),
        expiration=WARROOM_API_TOKEN_EXPIRATION_SECONDS,
        # The websocket session has already completed its interactive MFA check.
        mfa_auth=True,
    )
    return {
        "base_url": AIOE_INTERNAL_URL,
        "token": f"{account.id}:{token}",
        "tenant_id": str(tenant_id),
    }
