from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.maintenance import DEFAULT_TENANT_ID
from services.action_collection import timer
from services.action_collection.errors import CollectionError


class TimerRows:
    row = None
    filters = []

    @classmethod
    def filter(cls, **kwargs):
        cls.filters.append(kwargs)
        return SimpleNamespace(first=AsyncMock(side_effect=lambda: cls.row))

    @classmethod
    async def create(cls, **kwargs):
        cls.row = SimpleNamespace(id="timer", **kwargs, save=AsyncMock())
        return cls.row


@asynccontextmanager
async def same_plan(plan):
    yield plan


def make_plan(**updates):
    values = dict(
        id="plan-1",
        tenant_id="tenant-1",
        enabled=True,
        schedule_cron="0 0 3 * * *",
        updater="account-1",
        timed_id=None,
        save=AsyncMock(),
    )
    values.update(updates)
    return SimpleNamespace(**values)


def playbook_lookup():
    return AsyncMock(return_value=(SimpleNamespace(id="playbook-1"),
                                   SimpleNamespace(id="version-id", version="v1")))


@pytest.fixture(autouse=True)
def reset_timer_rows():
    TimerRows.row = None
    TimerRows.filters = []


@pytest.mark.asyncio
async def test_reconcile_creates_prefixed_internal_timer_and_enables_after_publish():
    produce = AsyncMock()
    target = make_plan()

    await timer.reconcile_timer_locked(
        target,
        timer_prefix="network_policy_collection_",
        locked_plan=same_plan,
        playbook_lookup=playbook_lookup(),
        timed_model=TimerRows,
        produce=produce,
    )

    row = TimerRows.row
    assert row.name == "network_policy_collection_plan-1"
    assert row.tenant_id == DEFAULT_TENANT_ID and row.internal is True
    assert row.playbook_input == {
        "flow_id": "version-id",
        "flow_input": {"plan_id": "plan-1", "collection_run_id": ""},
        "executor": "account-1",
    }
    message = produce.await_args.args[1]
    assert message["timer"] == "cron" and message["tenant_id"] == DEFAULT_TENANT_ID
    assert row.enable == 1 and target.timed_id == "timer"
    target.save.assert_awaited_with(update_fields=["timed_id"])


@pytest.mark.asyncio
async def test_disabled_plan_publishes_remove_and_keeps_timer_disabled():
    TimerRows.row = SimpleNamespace(
        id="timer", playbook_input={"flow_id": "old"}, enable=1, save=AsyncMock()
    )
    produce = AsyncMock()
    target = make_plan(enabled=False, timed_id="timer")

    await timer.reconcile_timer_locked(
        target,
        timer_prefix="network_policy_collection_",
        locked_plan=same_plan,
        playbook_lookup=AsyncMock(),
        timed_model=TimerRows,
        produce=produce,
    )

    assert produce.await_args.args[1]["timer"] == "remove"
    assert TimerRows.row.enable == 0
    assert TimerRows.row.playbook_input == {"flow_id": "old"}


@pytest.mark.asyncio
async def test_missing_playbook_fails_before_timer_name_requires_plan_id():
    target = SimpleNamespace(enabled=True)

    with pytest.raises(CollectionError) as exc:
        await timer.reconcile_timer_locked(
            target,
            timer_prefix="x_",
            locked_plan=same_plan,
            playbook_lookup=AsyncMock(return_value=(None, None)),
            timed_model=TimerRows,
            produce=AsyncMock(),
        )

    assert (exc.value.code, exc.value.status) == ("COLLECTION_PLAYBOOK_UNAVAILABLE", 409)
    assert TimerRows.filters == []


def test_lock_key_uses_namespace_tenant_and_plan_bytes():
    expected = int.from_bytes(
        sha256(b"network_topology_collection:tenant-1:plan-1").digest()[:8],
        "big",
        signed=True,
    )
    assert timer.lock_key("network_topology_collection", "tenant-1", "plan-1") == expected


@pytest.mark.asyncio
async def test_advisory_lock_filters_pool_options_and_closes_connection(monkeypatch):
    client = SimpleNamespace(
        host="db.example",
        port=5432,
        user="db-user",
        password="secret",
        database="db-name",
        server_settings={"application_name": "collection-timer"},
        extra={
            "command_timeout": 12,
            "ssl": object(),
            "min_size": 1,
            "max_size": 4,
            "max_queries": 100,
            "max_inactive_connection_lifetime": 60,
            "connect": object(),
            "setup": object(),
            "init": object(),
            "reset": object(),
        },
    )
    connection = AsyncMock()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(timer.connections, "get", lambda name: client)
    monkeypatch.setattr(timer.asyncpg, "connect", connect)

    async with timer.advisory_lock("ns", "tenant-1", "plan-1"):
        connection.execute.assert_awaited_once()

    kwargs = connect.await_args.kwargs
    assert kwargs["command_timeout"] == 12 and kwargs["ssl"] is client.extra["ssl"]
    assert not {
        "min_size", "max_size", "max_queries", "max_inactive_connection_lifetime",
        "connect", "setup", "init", "reset",
    } & kwargs.keys()
    connection.close.assert_awaited_once()


def test_topology_keeps_timer_patch_points_and_entry_points():
    from services.network_topology.collection import plans

    for name in (
        "Timed", "aproduce", "builtin_playbook", "_locked_plan", "_timer_serialization",
        "sync_timer", "connections", "asyncpg", "DEFAULT_TENANT_ID", "TIMER_END",
    ):
        assert hasattr(plans, name), name


@pytest.mark.asyncio
async def test_disable_and_remove_timer_updates_plan_before_publishing(monkeypatch):
    import services.action_collection.timer as timer_module

    target = make_plan()
    TimerRows.row = SimpleNamespace(
        id="timer", playbook_input={"flow_id": "old"}, enable=1, save=AsyncMock()
    )
    monkeypatch.setattr(timer_module, "Timed", TimerRows)
    monkeypatch.setattr(timer_module, "aproduce", AsyncMock())

    @asynccontextmanager
    async def unlocked(namespace, tenant_id, plan_id):
        assert (namespace, tenant_id, plan_id) == ("ns", "tenant-1", "plan-1")
        yield

    @asynccontextmanager
    async def transaction():
        yield object()

    class PlanModel:
        @classmethod
        def filter(cls, **kwargs):
            assert kwargs == {"id": "plan-1", "tenant_id": "tenant-1", "deleted": False}
            query = SimpleNamespace()
            query.using_db = lambda db: query
            query.select_for_update = lambda: query
            query.first = AsyncMock(return_value=target)
            return query

    monkeypatch.setattr(timer_module, "advisory_lock", unlocked)
    monkeypatch.setattr(timer_module, "in_transaction", transaction)
    removed = await timer.disable_and_remove_timer(
        target,
        plan_model=PlanModel,
        timer_prefix="network_policy_collection_",
        playbook_name="internal collection",
        lock_namespace="ns",
    )

    assert removed is True and target.enabled is False
    assert timer_module.aproduce.await_args.args[1]["timer"] == "remove"


@pytest.mark.asyncio
async def test_disable_and_remove_timer_returns_false_when_plan_is_missing(monkeypatch):
    @asynccontextmanager
    async def unlocked(*args):
        yield

    @asynccontextmanager
    async def transaction():
        yield object()

    class PlanModel:
        @classmethod
        def filter(cls, **kwargs):
            query = SimpleNamespace()
            query.using_db = lambda db: query
            query.select_for_update = lambda: query
            query.first = AsyncMock(return_value=None)
            return query

    monkeypatch.setattr(timer, "advisory_lock", unlocked)
    monkeypatch.setattr(timer, "in_transaction", transaction)
    assert await timer.disable_and_remove_timer(
        make_plan(), plan_model=PlanModel, timer_prefix="x_", playbook_name="pb", lock_namespace="ns"
    ) is False
