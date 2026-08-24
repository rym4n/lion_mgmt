# coding=utf-8
"""Reindex 后台任务单测（Task 4）。

覆盖 ``services/digital_employee/reindex_worker``：任务创建、后台逐个重建、
失败隔离、进度增量落库、异常兜底。

这里用轻量假模型替换 ORM，只为验证**编排逻辑**；真正的 workspace 同步
（``manager.sync_employee_workspace`` -> lion_agent ``/workspaces/<id>/sync``）
通过 ``_sync_workspace`` 这个接缝打桩，不在本文件里连真实服务。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.digital_employee import reindex_worker


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    async def all(self):
        return list(self._rows)

    async def first(self):
        return self._rows[0] if self._rows else None


class _FakeAgentManager:
    """最小化的 CommandAgent.filter 替身，只实现用到的几种过滤。"""

    def __init__(self, rows):
        self.rows = list(rows)
        self.filter_calls = []

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        rows = self.rows
        if "tenant_id" in kwargs:
            rows = [r for r in rows if r.tenant_id == kwargs["tenant_id"]]
        if "deleted" in kwargs:
            rows = [r for r in rows if r.deleted == kwargs["deleted"]]
        if "enabled" in kwargs:
            rows = [r for r in rows if r.enabled == kwargs["enabled"]]
        if "id__in" in kwargs:
            wanted = {str(i) for i in kwargs["id__in"]}
            rows = [r for r in rows if str(r.id) in wanted]
        return _FakeQuery(rows)


class _FakeTask:
    """行为贴近 Tortoise 实例：save 记录每次快照，便于断言进度是增量落库的。"""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", uuid.uuid4())
        self.config_id = kwargs.pop("config_id", "")
        self.tenant_id = kwargs.pop("tenant_id", "")
        self.status = kwargs.pop("status", self.STATUS_PENDING)
        self.total_workspaces = kwargs.pop("total_workspaces", 0)
        self.processed_workspaces = kwargs.pop("processed_workspaces", 0)
        self.failed_workspaces = kwargs.pop("failed_workspaces", 0)
        self.details = kwargs.pop("details", [])
        self.started_at = kwargs.pop("started_at", None)
        self.completed_at = kwargs.pop("completed_at", None)
        self.error = kwargs.pop("error", None)
        self.deleted = False
        self.saves = []

    async def save(self, update_fields=None):
        self.saves.append(
            {
                "update_fields": list(update_fields or []),
                "status": self.status,
                "processed": self.processed_workspaces,
                "failed": self.failed_workspaces,
                "details": [dict(d) for d in self.details],
                "error": self.error,
            }
        )


class _FakeTaskManager:
    def __init__(self):
        self.rows = []
        self.STATUS_PENDING = _FakeTask.STATUS_PENDING
        self.STATUS_RUNNING = _FakeTask.STATUS_RUNNING
        self.STATUS_COMPLETED = _FakeTask.STATUS_COMPLETED
        self.STATUS_FAILED = _FakeTask.STATUS_FAILED

    async def create(self, **kwargs):
        task = _FakeTask(**kwargs)
        self.rows.append(task)
        return task

    def filter(self, **kwargs):
        rows = self.rows
        if "id" in kwargs:
            rows = [r for r in rows if str(r.id) == str(kwargs["id"])]
        if "deleted" in kwargs:
            rows = [r for r in rows if r.deleted == kwargs["deleted"]]
        return _FakeQuery(rows)


def _agent(agent_id, tenant_id="t1", enabled=True, deleted=False):
    return SimpleNamespace(
        id=agent_id, tenant_id=tenant_id, enabled=enabled, deleted=deleted
    )


@pytest.fixture
def fake_orm(monkeypatch):
    tasks = _FakeTaskManager()
    agents = _FakeAgentManager([])
    monkeypatch.setattr(reindex_worker, "ReindexTask", tasks)
    monkeypatch.setattr(reindex_worker, "CommandAgent", agents)
    return SimpleNamespace(tasks=tasks, agents=agents)


class TestCreateReindexTask:
    @pytest.mark.asyncio
    async def test_creates_task_and_spawns_worker(self, fake_orm, monkeypatch):
        ids = [uuid.uuid4() for _ in range(3)]
        fake_orm.agents.rows = [_agent(i) for i in ids]
        spawned = {}

        def _spawn(task_id, workspace_ids):
            spawned["task_id"] = task_id
            spawned["workspace_ids"] = list(workspace_ids)

        monkeypatch.setattr(reindex_worker, "spawn_reindex", _spawn)

        task_id = await reindex_worker.create_reindex_task("cfg-1", "t1")

        assert len(fake_orm.tasks.rows) == 1
        task = fake_orm.tasks.rows[0]
        assert str(task.id) == task_id
        assert task.config_id == "cfg-1"
        assert task.tenant_id == "t1"
        assert task.total_workspaces == 3
        assert task.status == _FakeTask.STATUS_PENDING
        assert spawned["task_id"] == task_id
        assert spawned["workspace_ids"] == [str(i) for i in ids]

    @pytest.mark.asyncio
    async def test_filters_by_enabled_flag_not_status(self, fake_orm, monkeypatch):
        """CommandAgent.status 的取值是 idle/busy/offline/error，没有 'enabled'；
        误用 status='enabled' 会一个员工都查不到、reindex 永远空跑。"""
        keep, drop = uuid.uuid4(), uuid.uuid4()
        fake_orm.agents.rows = [
            _agent(keep, enabled=True),
            _agent(drop, enabled=False),
        ]
        monkeypatch.setattr(reindex_worker, "spawn_reindex", lambda *a, **kw: None)

        await reindex_worker.create_reindex_task("cfg-1", "t1")

        assert fake_orm.agents.filter_calls[0] == {
            "tenant_id": "t1",
            "deleted": False,
            "enabled": True,
        }
        assert fake_orm.tasks.rows[0].total_workspaces == 1

    @pytest.mark.asyncio
    async def test_isolates_other_tenants(self, fake_orm, monkeypatch):
        fake_orm.agents.rows = [
            _agent(uuid.uuid4(), tenant_id="t1"),
            _agent(uuid.uuid4(), tenant_id="t2"),
        ]
        monkeypatch.setattr(reindex_worker, "spawn_reindex", lambda *a, **kw: None)

        await reindex_worker.create_reindex_task("cfg-1", "t1")
        assert fake_orm.tasks.rows[0].total_workspaces == 1

    @pytest.mark.asyncio
    async def test_completes_immediately_without_employees(self, fake_orm, monkeypatch):
        def _spawn(*args, **kwargs):
            raise AssertionError("没有员工时不应拉起后台任务")

        monkeypatch.setattr(reindex_worker, "spawn_reindex", _spawn)

        task_id = await reindex_worker.create_reindex_task("cfg-1", "t1")

        task = fake_orm.tasks.rows[0]
        assert str(task.id) == task_id
        assert task.status == _FakeTask.STATUS_COMPLETED
        assert task.total_workspaces == 0
        assert task.started_at is not None
        assert task.completed_at is not None


class TestExecuteReindex:
    @pytest.mark.asyncio
    async def test_all_success_marks_completed(self, fake_orm, monkeypatch):
        ids = [uuid.uuid4() for _ in range(2)]
        fake_orm.agents.rows = [_agent(i) for i in ids]
        task = await fake_orm.tasks.create(
            tenant_id="t1", total_workspaces=2, details=[]
        )
        synced = []
        monkeypatch.setattr(
            reindex_worker,
            "_sync_workspace",
            lambda employee: _record(synced, employee),
        )

        await reindex_worker.execute_reindex(str(task.id), [str(i) for i in ids])

        assert [str(e.id) for e in synced] == [str(i) for i in ids]
        assert task.status == _FakeTask.STATUS_COMPLETED
        assert task.processed_workspaces == 2
        assert task.failed_workspaces == 0
        assert task.completed_at is not None
        assert task.details == [
            {"workspace_id": str(ids[0]), "status": "success", "error": None},
            {"workspace_id": str(ids[1]), "status": "success", "error": None},
        ]

    @pytest.mark.asyncio
    async def test_marks_running_before_processing(self, fake_orm, monkeypatch):
        agent_id = uuid.uuid4()
        fake_orm.agents.rows = [_agent(agent_id)]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)
        monkeypatch.setattr(
            reindex_worker, "_sync_workspace", lambda employee: _noop()
        )

        await reindex_worker.execute_reindex(str(task.id), [str(agent_id)])

        assert task.saves[0]["status"] == _FakeTask.STATUS_RUNNING
        assert task.started_at is not None

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_the_rest(self, fake_orm, monkeypatch):
        ok_a, bad, ok_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        fake_orm.agents.rows = [_agent(ok_a), _agent(bad), _agent(ok_b)]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=3)

        async def _sync(employee):
            if str(employee.id) == str(bad):
                raise RuntimeError("workspace sync failed")

        monkeypatch.setattr(reindex_worker, "_sync_workspace", _sync)

        await reindex_worker.execute_reindex(
            str(task.id), [str(ok_a), str(bad), str(ok_b)]
        )

        assert task.processed_workspaces == 3
        assert task.failed_workspaces == 1
        assert task.status == _FakeTask.STATUS_FAILED
        statuses = [d["status"] for d in task.details]
        assert statuses == ["success", "failed", "success"]
        assert "workspace sync failed" in task.details[1]["error"]

    @pytest.mark.asyncio
    async def test_missing_employee_counts_as_failure(self, fake_orm, monkeypatch):
        """调度之后员工被删：算失败但不能让整轮崩掉。"""
        gone = uuid.uuid4()
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)
        monkeypatch.setattr(
            reindex_worker, "_sync_workspace", lambda employee: _noop()
        )

        await reindex_worker.execute_reindex(str(task.id), [str(gone)])

        assert task.status == _FakeTask.STATUS_FAILED
        assert task.failed_workspaces == 1
        assert task.details[0]["error"] == "employee not found"

    @pytest.mark.asyncio
    async def test_progress_is_persisted_incrementally(self, fake_orm, monkeypatch):
        """前端靠轮询看进度：必须每完成一个就落库，不能攒到最后一次性写。"""
        ids = [uuid.uuid4() for _ in range(3)]
        fake_orm.agents.rows = [_agent(i) for i in ids]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=3)
        monkeypatch.setattr(
            reindex_worker, "_sync_workspace", lambda employee: _noop()
        )

        await reindex_worker.execute_reindex(str(task.id), [str(i) for i in ids])

        progress = [
            s["processed"] for s in task.saves if "processed_workspaces" in s["update_fields"]
        ]
        assert progress == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_truncates_long_error(self, fake_orm, monkeypatch):
        agent_id = uuid.uuid4()
        fake_orm.agents.rows = [_agent(agent_id)]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)

        async def _sync(employee):
            raise RuntimeError("x" * 5000)

        monkeypatch.setattr(reindex_worker, "_sync_workspace", _sync)

        await reindex_worker.execute_reindex(str(task.id), [str(agent_id)])

        assert len(task.details[0]["error"]) == reindex_worker.MAX_DETAIL_ERROR_LENGTH

    @pytest.mark.asyncio
    async def test_missing_task_returns_without_raising(self, fake_orm, monkeypatch):
        monkeypatch.setattr(
            reindex_worker, "_sync_workspace", lambda employee: _noop()
        )
        await reindex_worker.execute_reindex(str(uuid.uuid4()), [str(uuid.uuid4())])

    @pytest.mark.asyncio
    async def test_unexpected_error_marks_task_failed(self, fake_orm, monkeypatch):
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)

        async def _boom(tenant_id, workspace_ids):
            raise RuntimeError("database is gone")

        monkeypatch.setattr(reindex_worker, "_load_employees", _boom)

        # 后台协程没人 await，绝不能把异常抛出去
        await reindex_worker.execute_reindex(str(task.id), [str(uuid.uuid4())])

        assert task.status == _FakeTask.STATUS_FAILED
        assert "database is gone" in task.error
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_timestamps_are_timezone_aware_utc(self, fake_orm, monkeypatch):
        agent_id = uuid.uuid4()
        fake_orm.agents.rows = [_agent(agent_id)]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)
        monkeypatch.setattr(
            reindex_worker, "_sync_workspace", lambda employee: _noop()
        )

        await reindex_worker.execute_reindex(str(task.id), [str(agent_id)])

        for stamp in (task.started_at, task.completed_at):
            assert isinstance(stamp, datetime)
            assert stamp.tzinfo is not None
            assert stamp.utcoffset() == timezone.utc.utcoffset(None)

    @pytest.mark.asyncio
    async def test_only_reindexes_requested_tenant_employees(self, fake_orm, monkeypatch):
        mine, other = uuid.uuid4(), uuid.uuid4()
        fake_orm.agents.rows = [
            _agent(mine, tenant_id="t1"),
            _agent(other, tenant_id="t2"),
        ]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=2)
        synced = []
        monkeypatch.setattr(
            reindex_worker,
            "_sync_workspace",
            lambda employee: _record(synced, employee),
        )

        await reindex_worker.execute_reindex(str(task.id), [str(mine), str(other)])

        assert [str(e.id) for e in synced] == [str(mine)]
        assert task.failed_workspaces == 1


class TestSpawnReindex:
    @pytest.mark.asyncio
    async def test_runs_in_background_and_keeps_strong_reference(
        self, fake_orm, monkeypatch
    ):
        agent_id = uuid.uuid4()
        fake_orm.agents.rows = [_agent(agent_id)]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)
        monkeypatch.setattr(
            reindex_worker, "_sync_workspace", lambda employee: _noop()
        )

        background = reindex_worker.spawn_reindex(str(task.id), [str(agent_id)])
        # create_task 只持弱引用，模块必须自己留一份，否则可能被 GC
        assert background in reindex_worker._BACKGROUND_TASKS

        await background
        assert task.status == _FakeTask.STATUS_COMPLETED
        assert background not in reindex_worker._BACKGROUND_TASKS

    @pytest.mark.asyncio
    async def test_does_not_block_caller(self, fake_orm, monkeypatch):
        agent_id = uuid.uuid4()
        fake_orm.agents.rows = [_agent(agent_id)]
        task = await fake_orm.tasks.create(tenant_id="t1", total_workspaces=1)

        started = asyncio.Event()
        release = asyncio.Event()

        async def _sync(employee):
            started.set()
            await release.wait()

        monkeypatch.setattr(reindex_worker, "_sync_workspace", _sync)

        background = reindex_worker.spawn_reindex(str(task.id), [str(agent_id)])
        # 调用方立即拿回控制权，任务还卡在 sync 里
        assert not background.done()
        await started.wait()
        assert task.status == _FakeTask.STATUS_RUNNING

        release.set()
        await background
        assert task.status == _FakeTask.STATUS_COMPLETED


class TestScheduleReindexEntrypoint:
    @pytest.mark.asyncio
    async def test_openviking_delegates_to_worker(self, monkeypatch):
        """views_embedding 调的是 openviking.schedule_reindex，
        它必须真的转发到 worker，不能还留着 Task 3 的占位 return None。"""
        import services.digital_employee.reindex_worker as worker_module

        from services.digital_employee import openviking

        # 这里刻意在用例内部再解析一次模块，而不是复用文件顶部的 import：
        # 同目录多个用例会 pop/替换 sys.modules 里的 services.digital_employee，
        # 导致本模块在整目录跑时被重新导入，顶部绑定的对象成了旧副本，
        # 打在旧副本上的 monkeypatch 对 schedule_reindex 的延迟导入不生效。
        seen = {}

        async def _create(config_id, tenant_id):
            seen["args"] = (config_id, tenant_id)
            return "task-42"

        monkeypatch.setattr(worker_module, "create_reindex_task", _create)

        assert await openviking.schedule_reindex("cfg-9", "t1") == "task-42"
        assert seen["args"] == ("cfg-9", "t1")


async def _noop():
    return None


async def _record(bucket, employee):
    bucket.append(employee)
