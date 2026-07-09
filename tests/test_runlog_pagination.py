# coding=utf-8

from types import SimpleNamespace

import pytest

import libs.utils as utils
import models


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
