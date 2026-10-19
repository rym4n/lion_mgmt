"""Exercise the real timer-lock adapter with asyncpg's connection contract."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tortoise.backends.asyncpg.client import AsyncpgDBClient

from services.network_topology.collection import plans
from settings import TORTOISE


@pytest.fixture
def timer_connection(monkeypatch):
    credentials = {
        **TORTOISE["connections"]["default"]["credentials"],
        "host": "database.example",
        "port": 5432,
        "user": "test-user",
        "password": "test-password",
        "database": "test-database",
        "server_settings": {"application_name": "topology-timer"},
        "command_timeout": 12,
        "ssl": object(),
    }
    client = AsyncpgDBClient(connection_name="default", **credentials)
    connection = AsyncMock()
    signature = inspect.signature(plans.asyncpg.connect)

    async def connect(**kwargs):
        # AsyncMock alone accepts invalid driver arguments and hid this defect.
        signature.bind(**kwargs)
        return connection

    connector = AsyncMock(side_effect=connect)
    monkeypatch.setattr(plans.connections, "get", lambda name: client)
    monkeypatch.setattr(plans.asyncpg, "connect", connector)
    return client, connection, connector


@pytest.mark.asyncio
async def test_timer_lock_accepts_configured_database_options(timer_connection):
    client, connection, connector = timer_connection
    plan = SimpleNamespace(tenant_id="tenant", id="plan")

    async with plans._timer_serialization(plan):
        connection.execute.assert_awaited_once()
        assert connection.execute.await_args.args[0] == "SELECT pg_advisory_lock($1)"
        connection.close.assert_not_awaited()

    connection.close.assert_awaited_once()
    options = connector.await_args.kwargs
    assert options["host"] == client.host
    assert options["server_settings"] == client.server_settings
    assert options["command_timeout"] == 12
    assert options["ssl"] is client.extra["ssl"]
    assert client.extra["max_inactive_connection_lifetime"] == 60


@pytest.mark.asyncio
async def test_timer_lock_excludes_all_pool_only_options(timer_connection):
    client, connection, connector = timer_connection
    pool_options = {
        "min_size": 1,
        "max_size": 5,
        "max_queries": 1000,
        "max_inactive_connection_lifetime": 60,
        "connect": object(),
        "setup": object(),
        "init": object(),
        "reset": object(),
    }
    client.extra.update(pool_options)
    original = client.extra.copy()
    plan = SimpleNamespace(tenant_id="tenant", id="plan")

    async with plans._timer_serialization(plan):
        pass

    assert not pool_options.keys() & connector.await_args.kwargs.keys()
    assert client.extra == original
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("lock failed"), asyncio.CancelledError()])
async def test_timer_lock_closes_dedicated_connection_on_failure(timer_connection, failure):
    _, connection, _ = timer_connection
    connection.execute.side_effect = failure
    plan = SimpleNamespace(tenant_id="tenant", id="plan")

    with pytest.raises(type(failure)):
        async with plans._timer_serialization(plan):
            pytest.fail("a failed lock must not enter reconciliation")

    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_timer_lock_closes_dedicated_connection_on_reconciliation_error(timer_connection):
    _, connection, _ = timer_connection
    plan = SimpleNamespace(tenant_id="tenant", id="plan")

    with pytest.raises(RuntimeError, match="reconciliation failed"):
        async with plans._timer_serialization(plan):
            raise RuntimeError("reconciliation failed")

    connection.close.assert_awaited_once()
