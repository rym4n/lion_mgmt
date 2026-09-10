# coding=utf-8

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import libs.chat as chat
from libs.chat import ChatManager


def _runlog(*, status, output_params):
    return SimpleNamespace(
        id="log-1",
        tenant_id="tenant-1",
        run_id="run-1",
        event_id=None,
        node_id="end-1",
        node_name="结束",
        node_type="end-node",
        tool_name=None,
        tool_version=None,
        tool_display_name=None,
        action_name=None,
        action_display_name=None,
        status=status,
        args=None,
        output_params=output_params,
        result="",
        start_time=None,
        end_time=None,
        executor=None,
        resource_name="",
        playbook_name="demo",
        playbook_version_id="flow-1",
        timer_id=None,
    )


@pytest.mark.asyncio
async def test_success_terminal_upsert_persists_end_node_output_after_in_progress(monkeypatch):
    connection = SimpleNamespace(execute_query=AsyncMock())

    class Transaction:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(chat, "in_transaction", lambda: Transaction())
    monkeypatch.setattr(chat, "refresh_timed_run_summaries", AsyncMock())
    manager = ChatManager.__new__(ChatManager)
    in_progress = _runlog(status=1, output_params=None)
    terminal = _runlog(status=0, output_params={"host": "h1"})

    await manager._bulk_upsert_runlogs(manager._dedupe_runlogs([in_progress, terminal]))

    sql, params = connection.execute_query.await_args.args
    assert '"output_params"' in sql
    assert '"output_params" = EXCLUDED."output_params"' in sql
    assert 'CAST($15 AS TEXT)::JSONB' in sql
    assert 'WHERE "runlog"."status" != 0 OR EXCLUDED."status" = 0' in sql
    assert '{"host":"h1"}' in params


@pytest.mark.asyncio
async def test_failed_terminal_upsert_persists_end_node_output_directly(monkeypatch):
    connection = SimpleNamespace(execute_query=AsyncMock())

    class Transaction:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(chat, "in_transaction", lambda: Transaction())
    monkeypatch.setattr(chat, "refresh_timed_run_summaries", AsyncMock())
    terminal = _runlog(status=-1, output_params={"reason": "failed"})

    await ChatManager.__new__(ChatManager)._bulk_upsert_runlogs([terminal])

    _sql, params = connection.execute_query.await_args.args
    assert '{"reason":"failed"}' in params
