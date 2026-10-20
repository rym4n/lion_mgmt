"""One-time agent job tokens: only the sha256 is ever persisted."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import settings
from services.action_collection.errors import CollectionError
from services.action_collection.internal_auth import reject_tenant_fields


def issue_job_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


async def authenticate_job(headers: Any, body: dict, *, job_model, timeout_seconds: int = 1800):
    """Authenticate an internal caller and bind its one-time job token."""
    supplied = headers.get("X-Internal-Token", "") if headers else ""
    configured = getattr(settings, "LION_AGENT_INTERNAL_TOKEN", "")
    if (not isinstance(supplied, str) or not configured or not supplied
            or not hmac.compare_digest(supplied.encode(), configured.encode())):
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    if not isinstance(body, dict):
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    try:
        reject_tenant_fields(body)
    except CollectionError:
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403) from None
    job_id = body.get("job_id")
    token = body.get("agent_job_token")
    if not isinstance(job_id, str) or not job_id or not isinstance(token, str) or not token:
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    job = await job_model.filter(id=job_id, deleted=False).first()
    if job is None or job.state != "RUNNING":
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    started_at = job.started_at
    now = datetime.now(timezone.utc)
    if (started_at is None or started_at.tzinfo is None or started_at.utcoffset() is None
            or started_at > now
            or started_at + timedelta(seconds=timeout_seconds) <= now):
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), job.job_token_hash):
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    return job
