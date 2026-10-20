import hashlib
from datetime import datetime, timedelta, timezone, tzinfo
from types import SimpleNamespace

import pytest

import settings
from core.maintenance import DEFAULT_TENANT_ID
from services.action_collection.agent_tokens import authenticate_job, issue_job_token
from services.action_collection.errors import CollectionError
from services.action_collection.internal_auth import actor_id, reject_tenant_fields, require_internal_caller, session_tenant_id


def request(session):
    return SimpleNamespace(ctx=SimpleNamespace(session=session))


def test_reject_tenant_fields_recurses():
    reject_tenant_fields({"a": [{"b": 1}]})
    with pytest.raises(CollectionError) as exc:
        reject_tenant_fields({"items": [{"resources": [{"tenant_id": "x"}]}]})
    assert (exc.value.code, exc.value.status) == ("COLLECTION_CALLER_FORBIDDEN", 403)


def test_internal_caller_must_be_default_tenant():
    require_internal_caller(request({"tenant_id": DEFAULT_TENANT_ID}), {"plan_id": "p"})
    with pytest.raises(CollectionError):
        require_internal_caller(request({"tenant_id": "other"}), {"plan_id": "p"})
    with pytest.raises(CollectionError):
        require_internal_caller(request({"tenant_id": DEFAULT_TENANT_ID}), {"tenant": "x"})


def test_topology_views_keep_helper_names():
    from services.network_topology.collection import views
    for name in ("_body", "_error", "_page", "_required_string", "_reject_tenant_fields", "_actor_id"):
        assert callable(getattr(views, name)), name
    assert views._reject_tenant_fields is reject_tenant_fields
    assert views._actor_id is actor_id
    with pytest.raises(CollectionError) as exc:
        views._reject_tenant_fields({"tenant": "x"})
    assert (exc.value.code, exc.value.status) == ("COLLECTION_CALLER_FORBIDDEN", 403)


def test_actor_id_prefers_sub():
    assert actor_id(request({"sub": "acct-1"})) == "acct-1"
    with pytest.raises(CollectionError):
        actor_id(request(None))


class Jobs:
    row = None

    @classmethod
    def filter(cls, **kw):
        assert kw["deleted"] is False
        async def first():
            return cls.row if cls.row is not None and cls.row.id == kw["id"] else None
        return SimpleNamespace(first=first)


def job(token_hash, **kw):
    values = dict(id="j1", state="RUNNING", started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
                  job_token_hash=token_hash)
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_authenticate_job_accepts_matching_token(monkeypatch):
    monkeypatch.setattr(settings, "LION_AGENT_INTERNAL_TOKEN", "internal")
    token, digest = issue_job_token()
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    Jobs.row = job(digest)
    found = await authenticate_job({"X-Internal-Token": "internal"}, {"job_id": "j1", "agent_job_token": token},
                                   job_model=Jobs)
    assert found is Jobs.row


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no-config", "bad-internal", "tenant", "bad-token", "expired", "state"])
async def test_authenticate_job_fails_closed(monkeypatch, case):
    monkeypatch.setattr(settings, "LION_AGENT_INTERNAL_TOKEN", "" if case == "no-config" else "internal")
    token, digest = issue_job_token()
    Jobs.row = job(digest,
                   started_at=datetime.now(timezone.utc) - timedelta(seconds=120) if case == "expired"
                   else datetime.now(timezone.utc) - timedelta(seconds=5),
                   state="SUBMITTED" if case == "state" else "RUNNING")
    headers = {"X-Internal-Token": "wrong" if case == "bad-internal" else "internal"}
    body = {"job_id": "j1", "agent_job_token": "nope" if case == "bad-token" else token}
    if case == "tenant":
        body["tenant_id"] = "t"
    with pytest.raises(CollectionError) as exc:
        await authenticate_job(headers, body, job_model=Jobs, timeout_seconds=60)
    assert (exc.value.code, exc.value.status) == ("AGENT_JOB_FORBIDDEN", 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed, allowed", [(59, True), (60, False), (61, False)])
async def test_authenticate_enforces_exact_timeout_boundary(monkeypatch, elapsed, allowed):
    from services.action_collection import agent_tokens

    now = datetime(2026, 10, 20, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(agent_tokens, "datetime", Clock)
    monkeypatch.setattr(settings, "LION_AGENT_INTERNAL_TOKEN", "internal")
    token, digest = issue_job_token()
    Jobs.row = job(digest, started_at=now - timedelta(seconds=elapsed))
    headers = {"X-Internal-Token": "internal"}
    body = {"job_id": "j1", "agent_job_token": token}
    if allowed:
        assert await authenticate_job(headers, body, job_model=Jobs, timeout_seconds=60) is Jobs.row
    else:
        with pytest.raises(CollectionError) as exc:
            await authenticate_job(headers, body, job_model=Jobs, timeout_seconds=60)
        assert (exc.value.code, exc.value.status) == ("AGENT_JOB_FORBIDDEN", 403)


@pytest.mark.parametrize("session", [None, [], {}, {"tenant_id": ""}])
def test_session_tenant_requires_session_and_tenant(session):
    with pytest.raises(CollectionError) as exc:
        session_tenant_id(request(session))
    assert (exc.value.code, exc.value.status) == ("COLLECTION_CALLER_FORBIDDEN", 403)


def test_session_tenant_and_actor_fallback():
    assert session_tenant_id(request({"tenant_id": 12})) == "12"
    assert actor_id(request({"account_info": SimpleNamespace(id=23)})) == "23"
    assert actor_id(request({"sub": "preferred", "account_info": SimpleNamespace(id=23)})) == "preferred"
    with pytest.raises(CollectionError) as exc:
        actor_id(request({}))
    assert (exc.value.code, exc.value.status) == ("COLLECTION_CALLER_FORBIDDEN", 403)


class NullOffset(tzinfo):
    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no-job-id", "no-token", "empty-id", "missing-job", "missing-start",
                                  "naive-start", "future-start", "non-aware-start", "nested-tenant", "non-dict"])
async def test_authenticate_rejects_incomplete_or_invalid_job(monkeypatch, case):
    monkeypatch.setattr(settings, "LION_AGENT_INTERNAL_TOKEN", "internal")
    token, digest = issue_job_token()
    Jobs.row = job(digest)
    body = {"job_id": "j1", "agent_job_token": token}
    if case == "no-job-id":
        body.pop("job_id")
    elif case == "no-token":
        body.pop("agent_job_token")
    elif case == "empty-id":
        body["job_id"] = ""
    elif case == "missing-job":
        Jobs.row = None
    elif case == "missing-start":
        Jobs.row.started_at = None
    elif case == "naive-start":
        Jobs.row.started_at = datetime.now()
    elif case == "future-start":
        Jobs.row.started_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    elif case == "non-aware-start":
        Jobs.row.started_at = datetime.now().replace(tzinfo=NullOffset())
    elif case == "nested-tenant":
        body["items"] = [{"tenant": "other"}]
    elif case == "non-dict":
        body = []
    with pytest.raises(CollectionError) as exc:
        await authenticate_job({"X-Internal-Token": "internal"}, body, job_model=Jobs)
    assert (exc.value.code, exc.value.status) == ("AGENT_JOB_FORBIDDEN", 403)


@pytest.mark.asyncio
async def test_topology_authentication_resolves_model_and_shared_settings(monkeypatch):
    from services.network_topology import agent_jobs
    monkeypatch.setattr(agent_jobs.settings, "LION_AGENT_INTERNAL_TOKEN", "internal")
    monkeypatch.setattr(agent_jobs, "TopologyAgentJob", Jobs)
    monkeypatch.setattr(agent_jobs, "AGENT_TIMEOUT_SECONDS", 60)
    token, digest = issue_job_token()
    Jobs.row = job(digest)
    headers = {"X-Internal-Token": "internal"}
    body = {"job_id": "j1", "agent_job_token": token}
    assert await agent_jobs.authenticate(headers, body) is Jobs.row
    Jobs.row.started_at -= timedelta(seconds=120)
    with pytest.raises(CollectionError) as exc:
        await agent_jobs.authenticate(headers, body)
    assert (exc.value.code, exc.value.status) == ("AGENT_JOB_FORBIDDEN", 403)
