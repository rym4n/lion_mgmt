# coding=utf-8

from datetime import datetime, timezone
from types import SimpleNamespace

import asyncio
import inspect
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

from services.workflow.views import (
    _is_completed_flow_end_log,
    _link_runlog_to_command_run,
    _should_stream_runlog,
)


def test_should_stream_runlog_resends_updated_terminal_state():
    sent_versions = {}
    log = SimpleNamespace(
        id="log-1",
        utime=datetime(2026, 7, 2, tzinfo=timezone.utc),
        status=1,
        result="执行中",
        end_time=None,
    )

    assert _should_stream_runlog(log, sent_versions) is True
    assert _should_stream_runlog(log, sent_versions) is False

    log.status = 0
    log.result = "执行成功"
    log.end_time = datetime(2026, 7, 2, 0, 0, 1, tzinfo=timezone.utc)

    assert _should_stream_runlog(log, sent_versions) is True


def test_completed_flow_end_log_requires_terminal_state():
    running_end_log = SimpleNamespace(
        node_type="end-node",
        playbook_version_id="flow-1",
        status=1,
        result="执行中",
    )
    finished_end_log = SimpleNamespace(
        node_type="end-node",
        playbook_version_id="flow-1",
        status=0,
        result="执行成功",
    )

    assert _is_completed_flow_end_log(running_end_log, "flow-1") is False
    assert _is_completed_flow_end_log(finished_end_log, "flow-1") is True
    assert _is_completed_flow_end_log(finished_end_log, "flow-2") is False


def test_debug_request_publishes_one_task_message():
    from services.workflow import views

    source = inspect.getsource(views.execute_action)

    assert source.count("await aproduce(TASK_TOPIC, msg)") == 1


def _patch_command_task_run(monkeypatch, run_model):
    fake_module = ModuleType("models.command_center")
    fake_module.CommandTaskRun = run_model
    monkeypatch.setitem(sys.modules, "models.command_center", fake_module)


def test_link_runlog_to_command_run_appends_dedup(monkeypatch):
    """_link_runlog_to_command_run 把 debug run_id 去重追加到 CommandTaskRun.metadata。"""

    class FakeRun:
        def __init__(self):
            self.metadata = {"linked_run_ids": ["existing"]}

        async def save(self, update_fields=None):
            self.saved_fields = update_fields

    run = FakeRun()

    class RunModel:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs["tenant_id"] == "t1"
            assert kwargs["run_id"] == "agent-run"
            assert kwargs["deleted"] is False
            return run

    _patch_command_task_run(monkeypatch, RunModel)

    asyncio.run(_link_runlog_to_command_run("agent-run", "debug-1", "t1"))
    assert run.metadata["linked_run_ids"] == ["existing", "debug-1"]
    # 再次关联同一 debug run_id 不重复追加
    asyncio.run(_link_runlog_to_command_run("agent-run", "debug-1", "t1"))
    assert run.metadata["linked_run_ids"] == ["existing", "debug-1"]


def test_link_runlog_to_command_run_records_root_playbook(monkeypatch):
    class FakeRun:
        def __init__(self):
            self.metadata = {}

        async def save(self, update_fields=None):
            self.saved_fields = update_fields

    run = FakeRun()

    class RunModel:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return run

    _patch_command_task_run(monkeypatch, RunModel)

    asyncio.run(
        _link_runlog_to_command_run(
            "agent-run",
            "debug-1",
            "t1",
            "root-flow-v1",
        )
    )

    assert run.metadata == {
        "linked_run_ids": ["debug-1"],
        "linked_run_roots": {"debug-1": "root-flow-v1"},
    }


def test_link_runlog_noop_when_run_missing(monkeypatch):
    class RunModel:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return None

    _patch_command_task_run(monkeypatch, RunModel)
    # 找不到对应运行时应静默返回，不抛错
    asyncio.run(_link_runlog_to_command_run("missing", "debug-1", "t1"))


@pytest.mark.asyncio
async def test_edit_published_workflow_preserves_nondefault_flow_timeout(monkeypatch):
    """发布版本被编辑时，新草稿必须沿用其 1800 秒的流程超时。"""
    from services.workflow import views

    class PublishedVersion:
        status = 1
        flow_json = {"nodes": []}
        playbook = "playbook-1"
        flow_timeout = 1800

        async def prefetch_related(self, *_relations):
            return self

    published = PublishedVersion()
    created = SimpleNamespace(id="draft-1", flow_json={"nodes": []}, json={"id": "draft-1"})

    class VersionModel:
        @classmethod
        def get(cls, **kwargs):
            assert kwargs == {"id": "published-1", "tenant_id": "tenant-1"}
            return published

        @classmethod
        async def create(cls, **kwargs):
            cls.create_kwargs = kwargs
            return created

    async def empty_statistics(*_args):
        return []

    async def noop_tool_action(*_args):
        return None

    request = SimpleNamespace(
        json={"flow_id": "published-1", "flow_json": {"nodes": []}},
        ctx=SimpleNamespace(
            session={"account_info": "editor-1", "tenant_id": "tenant-1"}
        ),
    )
    monkeypatch.setattr(views, "PlaybookVersion", VersionModel)
    monkeypatch.setattr(views, "statistics_data", empty_statistics)
    monkeypatch.setattr(views, "tool_action_playbook_delete", noop_tool_action)
    monkeypatch.setattr(views, "tool_action_update", noop_tool_action)

    await inspect.unwrap(views.edit_workflow_flow)(request)

    assert VersionModel.create_kwargs["flow_timeout"] == 1800
