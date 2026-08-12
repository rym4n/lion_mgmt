# coding=utf-8

import hmac
import time

import jwt
import pyotp
from sanic.request import Request

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models import Account
from settings import SECRET, SESSION_ENCODING_ALGORITHM

SENSITIVE_MFA_PROOF_HEADER = "X-AIOE-MFA-Proof"
SENSITIVE_MFA_PROOF_PURPOSE = "sensitive-operation"
SENSITIVE_MFA_PROOF_TTL = 5 * 60


def session_requires_mfa(request: Request) -> bool:
    session = request.ctx.session
    account = session.get("account_info")
    return session.get("mfa_enable") is True or bool(
        account and getattr(account, "mfa_enable", False)
    )


async def verify_session_mfa_code(request: Request, code: object) -> Account:
    account = await Account.get_or_none(
        id=request.ctx.session.get("sub"), deleted=False
    )
    if not session_requires_mfa(request) and not (
        account and account.mfa_enable
    ):
        raise CustomError(ErrorCode.NO_NEED_MFA)

    normalized_code = str(code or "").strip()
    if (
        not account
        or not account.mfa_key
        or not normalized_code
        or not pyotp.TOTP(account.mfa_key).verify(normalized_code)
    ):
        raise CustomError(ErrorCode.MFA_ERROR)
    return account


def issue_sensitive_mfa_proof(request: Request) -> tuple[str, int]:
    session = request.ctx.session
    now = int(time.time())
    proof = jwt.encode(
        {
            "purpose": SENSITIVE_MFA_PROOF_PURPOSE,
            "sub": str(session.get("sub") or ""),
            "sid": str(session.get("id") or ""),
            "tenant_id": str(session.get("tenant_id") or ""),
            "iat": now,
            "exp": now + SENSITIVE_MFA_PROOF_TTL,
        },
        SECRET,
        SESSION_ENCODING_ALGORITHM,
    )
    return proof, SENSITIVE_MFA_PROOF_TTL


async def require_sensitive_mfa_proof(request: Request) -> None:
    if not session_requires_mfa(request):
        account = await Account.get_or_none(
            id=request.ctx.session.get("sub"), deleted=False
        )
        if not account or not account.mfa_enable:
            return

    proof = request.headers.get(SENSITIVE_MFA_PROOF_HEADER)
    try:
        payload = jwt.decode(
            proof,
            SECRET,
            algorithms=[SESSION_ENCODING_ALGORITHM],
            options={"require": ["purpose", "sub", "sid", "iat", "exp"]},
        )
        session = request.ctx.session
        expected = {
            "purpose": SENSITIVE_MFA_PROOF_PURPOSE,
            "sub": str(session.get("sub") or ""),
            "sid": str(session.get("id") or ""),
            "tenant_id": str(session.get("tenant_id") or ""),
        }
        if any(
            not hmac.compare_digest(str(payload.get(key, "")), value)
            for key, value in expected.items()
        ):
            raise ValueError("MFA proof is not bound to the active session")
    except Exception:
        raise CustomError(ErrorCode.SENSITIVE_MFA_REQUIRED) from None
