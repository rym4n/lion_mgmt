# coding=utf-8

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import libs.utils as utils
import models
from libs.error_code.exception import CustomError
from services.command_center import views


class _FakeRunlogModel:
    all_logs = []

    @classmethod
    def filter(cls, *args, **kwargs):
        return _FakeRunlogQuery(cls.all_logs, kwargs)


class _FakeRunlogQuery:
    def __init__(self, logs, filters=None):
        self.logs = list(logs)
        self.filters = filters or {}
        self._offset = 0
        self._limit = None
        self._values_fields = None

    def annotate(self, **_kwargs):
        return self

    def group_by(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def offset(self, value):
        self._offset = value
        return self

    def limit(self, value):
        self._limit = value
        return self

    def values(self, *fields):
        self._values_fields = fields
        return self

    async def all(self):
        return self._apply_filters()

    def __await__(self):
        async def _result():
            logs = self._apply_filters()
            if self._values_fields == ("run_id",):
                seen = []
                for log in logs:
                    if log.run_id not in seen:
                        seen.append(log.run_id)
                start = self._offset
                end = None if self._limit is None else start + self._limit
                return [{"run_id": run_id} for run_id in seen[start:end]]
            if self._values_fields == ("total",):
                return [{"total": len({log.run_id for log in logs})}]
            return logs

        return _result().__await__()

    def _apply_filters(self):
        logs = self.logs
        if run_ids := self.filters.get("run_id__in"):
            logs = [log for log in logs if log.run_id in run_ids]
        return logs


def _log(run_id, node_id, node_name):
    return SimpleNamespace(
        id=f"{run_id}-{node_id}-{node_name}",
        ctime="2026-07-09 10:00:00",
        utime="2026-07-09 10:00:00",
        run_id=run_id,
        timer_id="timer-1",
        event_id=None,
        node_id=node_id,
        node_name=node_name,
        node_type="action-node",
        tool_name="tool",
        tool_version="1",
        tool_display_name="工具",
        action_name="action",
        action_display_name="动作",
        status=0,
        args={},
        result="ok",
        start_time="2026-07-09 10:00:00",
        end_time="2026-07-09 10:00:01",
        executor=None,
        resource_name=None,
        playbook_name="剧本",
        json={
            "run_id": run_id,
            "node_id": node_id,
            "node_name": node_name,
            "node_type": "action-node",
            "status": 0,
            "result": "ok",
            "creator": None,
        },
    )


@pytest.mark.asyncio
async def test_paginated_runlog_groups_keeps_nodes_from_same_run_together(monkeypatch):
    async def fake_get_or_none(**_kwargs):
        return None

    monkeypatch.setattr(models, "Account", SimpleNamespace(get_or_none=fake_get_or_none), raising=False)
    _FakeRunlogModel.all_logs = [
        _log("run-2", "node-1", "动作1"),
        _log("run-2", "node-2", "动作2"),
        _log("run-1", "node-1", "动作1"),
    ]

    total, run_logs = await utils.paginated_runlog_groups(_FakeRunlogModel, object(), 1, 1)

    assert total == 2
    assert len(run_logs) == 1
    assert {
        item["node_id"]
        for node_group in run_logs[0]
        for item in node_group
    } == {"node-1", "node-2"}


def test_command_center_runlog_limits_are_bounded():
    assert views._parse_bounded_limit(None, default=10, maximum=50) == 10
    assert views._parse_bounded_limit(9999, default=10, maximum=50) == 50
    with pytest.raises(CustomError):
        views._parse_bounded_limit(0, default=10, maximum=50)
    with pytest.raises(CustomError):
        views._parse_bounded_limit("invalid", default=10, maximum=50)


def test_command_center_runlog_cursor_is_bound_to_task_and_run():
    cursor = views._encode_runlog_cursor(
        "node",
        task_id="task-1",
        run_id="run-1",
        ctime="2026-08-03T10:00:00+08:00",
        id="log-1",
    )

    payload = views._decode_runlog_cursor(cursor, "node", task_id="task-1", run_id="run-1")

    assert payload["id"] == "log-1"
    with pytest.raises(CustomError):
        views._decode_runlog_cursor(cursor, "node", task_id="task-2", run_id="run-1")
    with pytest.raises(CustomError):
        views._decode_runlog_cursor(cursor, "batch", task_id="task-1")


@pytest.mark.parametrize("cursor", ["%%%", "_w"])
def test_command_center_runlog_cursor_rejects_invalid_encoded_content(cursor):
    with pytest.raises(CustomError):
        views._decode_runlog_cursor(cursor, "batch", task_id="task-1")


def test_command_center_node_summary_never_includes_args_or_result():
    summary = views._runlog_node_summary_json(
        {
            "id": "log-1",
            "run_id": "run-1",
            "node_id": "node-1",
            "node_name": "查询主机【第12次循环】（2次重试）",
            "node_type": "action-node",
            "status": 0,
            "ctime": "2026-08-03T10:00:00+08:00",
            "args": {"secret": "must-not-leak"},
            "result": "must-not-leak",
        }
    )

    assert "args" not in summary
    assert "result" not in summary
    assert "must-not-leak" not in str(summary)
    assert summary["loop_index"] == 12
    assert summary["retry_index"] == 2


@pytest.mark.asyncio
async def test_task_run_ownership_is_scoped_to_task_tenant_and_linked_run(monkeypatch):
    task_filters = []
    run_filters = []
    task = SimpleNamespace(id="task-1")

    class FakeTask:
        @classmethod
        async def get_or_none(cls, **kwargs):
            task_filters.append(kwargs)
            return task

    class FakeRunQuery:
        async def exists(self):
            return True

    class FakeTaskRun:
        @classmethod
        def filter(cls, **kwargs):
            run_filters.append(kwargs)
            return FakeRunQuery()

    monkeypatch.setattr(views, "CommandTask", FakeTask)
    monkeypatch.setattr(views, "CommandTaskRun", FakeTaskRun)

    resolved = await views._require_task_run("task-1", "run-1", "tenant-1")

    assert resolved is task
    assert task_filters == [{"id": "task-1", "tenant_id": "tenant-1", "deleted": False}]
    assert run_filters == [
        {
            "task_id": "task-1",
            "tenant_id": "tenant-1",
            "deleted": False,
            "metadata__contains": {"linked_run_ids": ["run-1"]},
        }
    ]


def test_command_center_runlog_routes_keep_read_permission_and_detail_scope():
    for handler in (views.task_runlogs, views.task_runlog_nodes, views.task_runlog_detail):
        assert 'requires_permission("command_center:r")' in inspect.getsource(handler)
    detail_source = inspect.getsource(views.task_runlog_detail)
    for condition in ("id=runlog_id", "run_id=run_id", "tenant_id=tenant_id", "deleted=False"):
        assert condition in detail_source


def test_batch_linkage_query_is_database_bounded_and_cursor_stable():
    source = inspect.getsource(views._linked_task_run_page)

    assert "jsonb_array_elements_text" in source
    assert "WITH ORDINALITY" in source
    assert "LIMIT $3" in source
    assert "limit + 1" in source
    assert "task_run_ctime DESC, task_run_id DESC, ordinal DESC, run_id DESC" in source
    assert ".all()" not in source


def test_runlog_cursor_index_model_and_migration_contract():
    root = Path(__file__).resolve().parents[1]
    model_source = (root / "models/runlog.py").read_text(encoding="utf-8")
    migration_source = (
        root / "migrations/models/98_20260803120000_runlog_cursor_index.py"
    ).read_text(encoding="utf-8")

    assert '("tenant_id", "run_id", "ctime", "id")' in model_source
    assert '"idx_runlog_tenant__0d09ad"' in migration_source
    assert 'ON "runlog" ("tenant_id", "run_id", "ctime", "id")' in migration_source
