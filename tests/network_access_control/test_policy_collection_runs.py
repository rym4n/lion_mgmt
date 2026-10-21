# tests/network_access_control/test_policy_collection_runs.py
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.action_collection.errors import CollectionError
from services.action_collection import timer as collection_timer
from services.action_collection.targets import Target
from services.network_access_control.collection import pipeline, runs
from tests.network_access_control.policy_collection_fakes import FakePolicyRunRepository, item, plan, run_row


@pytest.fixture(autouse=True)
def direct_runtime(monkeypatch):
    monkeypatch.setattr(collection_timer, "builtin_playbook", AsyncMock(return_value=(None, None)), raising=False)
    monkeypatch.setattr(runs, "aproduce", AsyncMock(), raising=False)
    monkeypatch.setattr(runs, "notify_after_commit", AsyncMock())
    monkeypatch.setattr(pipeline, "freeze_targets", AsyncMock(return_value=[
        Target("i1", "huawei_usg", "v1", "main.py", "collect_policy_observations", "采集策略", {},
               "r1", "fw-r1", "10.0.0.1", "a1")]))
    launched = []

    def track(coro, **kwargs):
        launched.append(kwargs["run"].id)
        coro.close()

    monkeypatch.setattr(pipeline, "start_tracked", track)
    return launched


@pytest.mark.asyncio
async def test_trigger_manual_launches_without_published_playbook_or_flow(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    result = await runs.trigger_manual("t1", account_id="acc", repository=repo)
    row = repo.runs[result["collection_run_id"]]
    assert result["state"] == "COLLECTING"
    assert (row.tenant_id, row.triggered_by, row.device_total) == ("t1", "acc", 1)
    assert row.flow_run_id is None
    assert direct_runtime == [row.id]
    runs.aproduce.assert_not_awaited()
    collection_timer.builtin_playbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_manual_reuses_active_run_without_relaunch(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    first = await runs.trigger_manual("t1", account_id="acc", repository=repo)
    second = await runs.trigger_manual("t1", account_id="acc", repository=repo)
    assert second == {**first, "reused": True}
    assert direct_runtime == [first["collection_run_id"]]


@pytest.mark.asyncio
async def test_trigger_manual_requires_items():
    repo = FakePolicyRunRepository(plans=[plan()], items=[])
    with pytest.raises(CollectionError) as exc:
        await runs.trigger_manual("t1", account_id="acc", repository=repo)
    assert exc.value.code == "PLAN_EMPTY"


@pytest.mark.asyncio
async def test_trigger_manual_setup_failure_terminalizes_with_safe_code(monkeypatch, direct_runtime):
    monkeypatch.setattr(pipeline, "freeze_targets", AsyncMock(side_effect=RuntimeError("password=secret")))
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    with pytest.raises(CollectionError) as exc:
        await runs.trigger_manual("t1", account_id="acc", repository=repo)
    (row,) = repo.runs.values()
    assert (row.state, row.error_code) == ("FAILED", "COLLECTION_DISPATCH_FAILED")
    assert (exc.value.code, exc.value.status) == ("COLLECTION_DISPATCH_FAILED", 500)
    assert row.error_message is None and "secret" not in str(exc.value)
    assert repo.plans["p1"].last_run_id == row.id and repo.plans["p1"].consecutive_failures == 1
    assert direct_runtime == []


@pytest.mark.asyncio
async def test_start_scheduled_launches_and_rejects_overlap_and_disabled(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    created = await runs.start_scheduled("p1", repository=repo)
    assert repo.runs[created["collection_run_id"]].trigger == "schedule"
    assert created["state"] == "COLLECTING" and direct_runtime == [created["collection_run_id"]]
    with pytest.raises(CollectionError) as exc:
        await runs.start_scheduled("p1", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_ACTIVE"
    repo2 = FakePolicyRunRepository(plans=[plan(enabled=False)])
    with pytest.raises(CollectionError) as exc:
        await runs.start_scheduled("p1", repository=repo2)
    assert exc.value.code == "COLLECTION_PLAN_DISABLED"


@pytest.mark.asyncio
async def test_scheduled_transaction_can_create_then_launch_same_run_once(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    created = await runs.start_scheduled("p1", repository=repo, launch=False)
    assert created["state"] == "PENDING" and direct_runtime == []
    started = await runs.launch_run("p1", created["collection_run_id"], repository=repo)
    repeated = await runs.launch_run("p1", created["collection_run_id"], repository=repo)
    assert started == repeated == {"collection_run_id": created["collection_run_id"], "state": "COLLECTING"}
    assert len(repo.runs) == 1 and direct_runtime == [created["collection_run_id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["CANCELLED", "FAILED", "SUCCEEDED", "PARTIAL", "NORMALIZING", "APPLYING"])
async def test_launch_existing_nonpending_run_is_idempotent(direct_runtime, state):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    repo.runs["run-1"] = run_row(state=state)
    assert await runs.launch_run("p1", "run-1", repository=repo) == {
        "collection_run_id": "run-1", "state": state}
    assert direct_runtime == []


@pytest.mark.asyncio
async def test_launch_existing_run_preserves_plan_and_tenant_binding(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    repo.runs["foreign"] = run_row("foreign", tenant_id="t2")
    repo.runs["other-plan"] = run_row("other-plan", plan_id="p2")
    for run_id in ("foreign", "other-plan"):
        with pytest.raises(CollectionError) as exc:
            await runs.launch_run("p1", run_id, repository=repo)
        assert exc.value.code == "COLLECTION_RUN_NOT_FOUND"
        assert repo.runs[run_id].state == "PENDING"
    assert direct_runtime == []


@pytest.mark.asyncio
async def test_launch_expired_pending_run_terminalizes_without_starting(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan(run_timeout_seconds=600)], items=[item()])
    repo.runs["run-1"] = run_row(started_at=datetime.now(timezone.utc) - timedelta(seconds=601))
    with pytest.raises(CollectionError) as exc:
        await runs.launch_run("p1", "run-1", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_CLOSED"
    assert repo.runs["run-1"].state == "FAILED"
    assert repo.runs["run-1"].error_code == "COLLECTION_RUN_TIMEOUT" and direct_runtime == []


@pytest.mark.asyncio
async def test_require_open_run_times_out():
    repo = FakePolicyRunRepository(plans=[plan(run_timeout_seconds=600)])
    repo.runs["run-1"] = run_row(started_at=datetime.now(timezone.utc) - timedelta(seconds=601))
    with pytest.raises(CollectionError) as exc:
        await runs.require_open_run("p1", "run-1", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_CLOSED"
    assert repo.runs["run-1"].error_code == "COLLECTION_RUN_TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [("SUCCEEDED", 0), ("PARTIAL", 0), ("FAILED", 3), ("CANCELLED", 2)])
async def test_finish_run_failure_accounting(state, expected):
    repo = FakePolicyRunRepository(plans=[plan(consecutive_failures=2)])
    repo.runs["run-1"] = run_row(state="APPLYING")
    await runs.finish_run(repo.runs["run-1"], state, repository=repo)
    assert repo.plans["p1"].consecutive_failures == expected
    assert repo.plans["p1"].last_run_id == "run-1"
    runs.notify_after_commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_finish_run_is_idempotent():
    repo = FakePolicyRunRepository(plans=[plan()])
    repo.runs["run-1"] = run_row(state="FAILED")
    await runs.finish_run(repo.runs["run-1"], "SUCCEEDED", repository=repo)
    assert repo.runs["run-1"].state == "FAILED"
    runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_and_detail_projection(monkeypatch):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    created = await runs.trigger_manual("t1", account_id="acc", repository=repo)
    repo.runs[created["collection_run_id"]].item_results = [
        {"resource_id": "r1", "resource_name": "fw1", "device_asset_id": "a1", "app_name": "huawei_usg",
         "action_func": "collect_policy_observations", "stage": "collect", "status": "failed",
         "error_code": "COLLECTION_ACTION_FAILED", "error_message": "password=secret", "policy_count": None,
         "sync": None, "duration_ms": 5, "lion_action_run_id": "x"}]
    cancelled = await runs.cancel_run("t1", account_id="acc", repository=repo)
    assert cancelled["state"] == "CANCELLED"
    from services.network_access_control.collection import agent_jobs
    monkeypatch.setattr(agent_jobs, "latest_job", AsyncMock(return_value=None))
    detail = await runs.run_detail("t1", created["collection_run_id"], repository=repo)
    assert detail["state"] == "CANCELLED" and detail["agent"] is None
    assert set(detail["devices"][0]) == set(runs.DEVICE_FIELDS)
    assert "error_message" not in detail["devices"][0]
    with pytest.raises(CollectionError) as exc:
        await runs.run_detail("t2", created["collection_run_id"], repository=repo)
    assert exc.value.code == "COLLECTION_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_detail_includes_public_agent_summary(monkeypatch):
    from services.network_access_control.collection import agent_jobs

    repo = FakePolicyRunRepository(plans=[plan()])
    repo.runs["run-1"] = run_row()
    agent = SimpleNamespace(id="j1", state="SUCCEEDED", resource_ids=["r1", "r2"], cache_hits=2,
                            cache_misses=0, attempt=1, iterations=0, token_usage={},
                            profile_version="policy-normalizer/1", error_code=None, error_message=None,
                            transcript='[{"type":"cache_hit"}]')
    latest = AsyncMock(return_value=agent)
    monkeypatch.setattr(agent_jobs, "latest_job", latest)
    detail = await runs.run_detail("t1", "run-1", maintenance=False, repository=repo)
    assert detail["agent"]["cache_hits"] == 2
    latest.assert_awaited_once_with("t1", "run-1")


def test_status_projection_terminal_flag():
    projection = runs.status_projection(run_row(state="PARTIAL", device_total=2, device_succeeded=1))
    assert projection["terminal"] is True and projection["run_state"] == "PARTIAL"
    assert set(projection) == {"run_state", "terminal", "device_total", "device_succeeded", "compare_run_id",
                               "error_code", "error_message", "devices"}


@pytest.mark.asyncio
async def test_trigger_conflict_reuses_race_winner_without_relaunch(direct_runtime):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    async def competing_create(**values):
        repo.runs['winner'] = run_row('winner')
        raise runs.ActiveRunConflict
    repo.create_run = competing_create
    result = await runs.trigger_manual('t1', account_id='acc', repository=repo)
    assert result == {'collection_run_id': 'winner', 'state': 'PENDING', 'reused': True}
    runs.aproduce.assert_not_awaited()
    assert direct_runtime == []


@pytest.mark.asyncio
async def test_manual_can_run_disabled_plan_without_published_playbook():
    repo = FakePolicyRunRepository(plans=[plan(enabled=False)], items=[item()])
    result = await runs.trigger_manual('t1', account_id='acc', repository=repo)
    assert result['state'] == 'COLLECTING'
    collection_timer.builtin_playbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_plan_binding_and_open_terminal_guard():
    repo = FakePolicyRunRepository(plans=[plan()])
    repo.runs['run-1'] = run_row(plan_id='other')
    with pytest.raises(CollectionError) as exc:
        await runs.get_run_for_plan('p1', 'run-1', repository=repo)
    assert exc.value.code == 'COLLECTION_RUN_NOT_FOUND'
    repo.runs['run-1'].plan_id = 'p1'
    repo.runs['run-1'].state = 'CANCELLED'
    with pytest.raises(CollectionError) as exc:
        await runs.require_open_run('p1', 'run-1', repository=repo)
    assert exc.value.code == 'COLLECTION_RUN_CLOSED'


@pytest.mark.asyncio
async def test_finish_rejects_nonterminal_without_mutation():
    repo = FakePolicyRunRepository(plans=[plan()])
    row = run_row()
    repo.runs[row.id] = row
    with pytest.raises(CollectionError) as exc:
        await runs.finish_run(row, 'APPLYING', repository=repo)
    assert exc.value.code == 'COLLECTION_RUN_STATE_INVALID'
    assert row.state == 'PENDING'


def test_public_devices_filters_nested_sync_and_output_credentials():
    source = {'stage': 'normalize', 'status': 'failed', 'raw_outputs': 'secret',
              'agent_job_token': 'token', 'output': {'password': 'secret'},
              'sync': {'added': 1, 'modified': 2, 'moved': 0, 'deleted': 0, 'unchanged': 3,
                       'output': 'secret', 'agent_job_token': 'token'}}
    projected = runs.public_devices([source, None])[0]
    assert set(projected) == set(runs.DEVICE_FIELDS)
    assert projected['stage'] == 'normalize'
    assert projected['sync'] == {'added': 1, 'modified': 2, 'moved': 0, 'deleted': 0, 'unchanged': 3}
    assert 'secret' not in str(projected) and 'token' not in str(projected)
    assert runs.public_devices(None) == []


@pytest.mark.asyncio
async def test_list_and_detail_summary_do_not_expose_private_fields():
    repo = FakePolicyRunRepository(plans=[plan()])
    repo.runs['run-1'] = run_row(raw_outputs='secret', agent_job_id='private')
    result = await runs.list_runs('t1', 1, 10, repository=repo)
    assert result['total'] == 1 and result['page_size'] == 10
    assert set(result['items'][0]) == {'collection_run_id', *runs.SUMMARY_FIELDS}
    assert 'secret' not in str(result)
    assert (await runs.list_runs('t2', 1, 10, repository=repo))['items'] == []
    assert runs.operator_for(run_row(triggered_by=None)) == runs.AUTO_OPERATOR


# A local query double models filtering and updates. No database is initialized.
class MemoryQuery:
    def __init__(self, rows, filters, calls):
        self.rows, self.filters, self.calls = rows, filters, calls
        self.start, self.size = 0, None

    def selected(self):
        def matches(row):
            for name, value in self.filters.items():
                field, _, op = name.partition('__')
                actual = getattr(row, field, None)
                if op == 'not_in':
                    good = actual not in value
                elif op == 'in':
                    good = actual in value
                elif op == 'lt':
                    good = actual is not None and actual < value
                elif op == 'isnull':
                    good = (actual is None) == value
                else:
                    good = actual == value
                if not good:
                    return False
            return True
        return [r for r in self.rows if matches(r)]

    async def first(self):
        rows = self.selected()
        return rows[0] if rows else None

    def order_by(self, *fields):
        self.calls.append(('order', fields))
        return self

    def using_db(self, db):
        self.calls.append(('db', db))
        return self

    def select_for_update(self):
        self.calls.append(('lock', self.filters))
        return self

    def offset(self, start):
        self.start = start
        return self

    def limit(self, size):
        self.size = size
        return self

    async def count(self):
        return len(self.selected())

    async def update(self, **values):
        rows = self.selected()
        for row in rows:
            for key, value in values.items():
                setattr(row, key, value)
        return len(rows)

    def __await__(self):
        async def result():
            rows = self.selected()[self.start:]
            return rows if self.size is None else rows[:self.size]
        return result().__await__()


@pytest.fixture
def orm(monkeypatch):
    from contextlib import asynccontextmanager
    rows, calls = {}, []
    @asynccontextmanager
    async def transaction():
        yield 'transaction'
    monkeypatch.setattr(runs, 'in_transaction', transaction)
    for name in ('NetworkAccessPolicyCollectionRun', 'NetworkAccessPolicyCollectionPlan',
                 'NetworkAccessPolicyCollectionItem', 'NetworkAccessActualPolicy'):
        rows[name] = []
        def filtered(_name=name, **filters):
            calls.append((_name, filters))
            return MemoryQuery(rows[_name], filters, calls)
        monkeypatch.setattr(getattr(runs, name), 'filter', filtered)
    return runs.OrmPolicyRunRepository(), rows, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'])
async def test_orm_stale_worker_cannot_revive_or_reaccount_terminal(orm, terminal):
    from copy import copy
    repo, rows, calls = orm
    current = run_row(state=terminal, deleted=False, save=AsyncMock())
    stale = copy(current)
    stale.state = 'APPLYING'
    rows['NetworkAccessPolicyCollectionRun'].append(current)
    assert await repo.transition(stale, 'APPLYING', 'NORMALIZING', {}) is False
    assert await repo.transition(current, terminal, 'NORMALIZING', {}) is False
    assert await repo.save_fields(stale, {'device_succeeded': 99}) is False
    assert await repo.finish_terminal(stale, 'FAILED', datetime.now(timezone.utc), {}) is None
    assert current.state == terminal and current.device_succeeded == 0
    current.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_orm_cas_and_accounting_use_live_row_once(orm):
    from copy import copy
    repo, rows, calls = orm
    current = run_row(deleted=False, save=AsyncMock())
    p = plan(consecutive_failures=2, save=AsyncMock())
    rows['NetworkAccessPolicyCollectionRun'].append(current)
    rows['NetworkAccessPolicyCollectionPlan'].append(p)
    stale = copy(current)
    assert await repo.transition(stale, 'PENDING', 'COLLECTING', {'device_total': 2}) is True
    assert current.state == stale.state == 'COLLECTING'
    assert await repo.transition(stale, 'PENDING', 'COLLECTING', {}) is False
    await runs.finish_run(stale, 'FAILED', error_code='COLLECTION_WORKER_LOST', repository=repo)
    await runs.finish_run(stale, 'SUCCEEDED', repository=repo)
    assert current.state == stale.state == 'FAILED'
    assert p.consecutive_failures == 3 and p.last_run_id == current.id
    runs.notify_after_commit.assert_awaited_once()
    assert len([c for c in calls if c[0] == 'lock']) == 3
    assert all(c[1].get('tenant_id') == 't1' and c[1].get('deleted') is False
               for c in calls if c[0].startswith('NetworkAccess'))


@pytest.mark.asyncio
async def test_orm_rejects_field_payload_bypassing_terminal_guards(orm):
    repo, rows, _ = orm
    row = run_row(deleted=False)
    rows['NetworkAccessPolicyCollectionRun'].append(row)
    for values in ({'state': 'SUCCEEDED'}, {'tenant_id': 't2'}, {'plan_id': 'other'}, {'deleted': True}):
        with pytest.raises(ValueError):
            await repo.save_fields(row, values)
        with pytest.raises(ValueError):
            await repo.transition(row, 'PENDING', 'COLLECTING', values)
        with pytest.raises(ValueError):
            await repo.finish_terminal(row, 'FAILED', datetime.now(timezone.utc), values)
    assert row.state == 'PENDING'


@pytest.mark.asyncio
async def test_orm_query_scopes_items_history_retention_and_counts(orm):
    repo, rows, calls = orm
    p = plan()
    rows['NetworkAccessPolicyCollectionPlan'].extend([p, plan('deleted', deleted=True)])
    rows['NetworkAccessPolicyCollectionItem'].extend([
        item(tenant_id='t1', plan_id='p1', deleted=False),
        item('other', tenant_id='t2', plan_id='p1', deleted=False),
        item('disabled', tenant_id='t1', plan_id='p1', deleted=False, enabled=False)])
    old = datetime.now(timezone.utc) - timedelta(days=8)
    good = run_row('good', state='SUCCEEDED', deleted=False, raw_outputs='secret', finished_at=old)
    active = run_row('active', deleted=False, raw_outputs='keep', finished_at=old)
    deleted = run_row('deleted', deleted=True, raw_outputs='keep', finished_at=old)
    foreign = run_row('foreign', tenant_id='t2', deleted=False)
    rows['NetworkAccessPolicyCollectionRun'].extend([good, active, deleted, foreign])
    rows['NetworkAccessActualPolicy'].extend([SimpleNamespace(tenant_id='t1', deleted=False, device_key='resource:r1'),
                                             SimpleNamespace(tenant_id='t2', deleted=False, device_key='resource:r1')])
    assert (await repo.get_plan('p1', tenant_id='t1')) is p
    assert await repo.get_plan('deleted') is None
    assert (await repo.get_plan_for_tenant('t1')) is p
    assert [r.id for r in await repo.get_items(p)] == ['i1']
    assert (await repo.active_run('t1')).id == 'active'
    assert await repo.get_run('foreign', tenant_id='t1') is None
    page, total = await repo.list_runs('t1', 2, 1)
    assert [r.id for r in page] == ['active'] and total == 2
    assert {r.id for r in await repo.open_runs()} == {'active', 'foreign'}
    assert await repo.count_policies('t1', 'resource:r1') == 1
    assert await repo.clear_expired_raw_outputs(datetime.now(timezone.utc) - timedelta(days=7)) == 1
    assert good.raw_outputs is None and active.raw_outputs == deleted.raw_outputs == 'keep'
    assert ('order', ('order', 'id')) in calls


@pytest.mark.asyncio
async def test_orm_unique_conflict_and_explicit_tenant(monkeypatch):
    create = AsyncMock(side_effect=runs.IntegrityError('duplicate active tenant'))
    monkeypatch.setattr(runs.NetworkAccessPolicyCollectionRun, 'create', create)
    with pytest.raises(runs.ActiveRunConflict):
        await runs.OrmPolicyRunRepository().create_run(tenant_id='t1')
    for values in ({}, {'tenant_id': None}):
        with pytest.raises(ValueError):
            await runs.OrmPolicyRunRepository().create_run(**values)


@pytest.mark.asyncio
async def test_plan_last_run_links_summary_with_tenant_filter(monkeypatch):
    from services.network_access_control.collection import plans
    row = run_row(raw_outputs='secret')
    observed = []
    def filtered(**filters):
        observed.append(filters)
        return MemoryQuery([row], {}, [])
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionRun, 'filter', filtered)
    result = await plans._last_run('t1', plan(last_run_id='run-1'))
    assert result == runs.run_summary(row)
    assert observed == [{'id': 'run-1', 'tenant_id': 't1', 'deleted': False}]
    assert 'secret' not in str(result)


@pytest.mark.asyncio
async def test_fake_repository_matches_deleted_tenant_and_terminal_guards():
    from copy import copy
    repo = FakePolicyRunRepository(plans=[plan()], items=[item(), item('foreign', tenant_id='t2'),
                                                           item('disabled', enabled=False)])
    assert [i.id for i in await repo.get_items(repo.plans['p1'])] == ['i1']
    repo.runs['run-1'] = run_row(state='CANCELLED')
    stale = copy(repo.runs['run-1'])
    stale.state = 'APPLYING'
    assert await repo.transition(repo.runs['run-1'], 'CANCELLED', 'APPLYING', {}) is False
    assert await repo.save_fields(stale, {'device_total': 10}) is False
    assert await repo.finish_terminal(stale, 'FAILED', datetime.now(timezone.utc), {}) is None
    assert stale.state == 'CANCELLED'
    repo.runs['run-1'].deleted = True
    assert await repo.get_run('run-1') is None
    repo.plans['p1'].deleted = True
    assert await repo.get_plan('p1') is None
    assert await repo.active_run('t1') is None
    assert item(resources=[]).resources == []


@pytest.mark.asyncio
async def test_finished_summary_dates_are_aware_isoformat():
    naive = datetime(2026, 10, 20, 3, 0)
    result = runs.run_summary(run_row(started_at=naive))
    assert result['started_at'] == '2026-10-20T03:00:00+00:00'


@pytest.mark.asyncio
async def test_cancel_racing_with_completion_reports_winning_terminal(orm):
    from copy import copy
    repo, rows, _ = orm
    current = run_row(state='SUCCEEDED', deleted=False)
    stale = copy(current)
    stale.state = 'APPLYING'
    rows['NetworkAccessPolicyCollectionRun'].append(current)
    async def stale_active(tenant_id):
        return stale
    repo.active_run = stale_active
    result = await runs.cancel_run('t1', account_id='acc', repository=repo)
    assert result['state'] == 'SUCCEEDED'
    runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['PENDING', 'COLLECTING', 'CANCELLED'])
async def test_orm_expected_source_guards_terminal_write_and_accounting_under_row_lock(orm, source):
    from copy import copy
    repo, rows, calls = orm
    current = run_row(state=source, save=AsyncMock())
    p = plan(consecutive_failures=2, save=AsyncMock())
    rows['NetworkAccessPolicyCollectionRun'].append(current)
    rows['NetworkAccessPolicyCollectionPlan'].append(p)
    stale = copy(current)
    stale.state = 'PENDING'
    committed = await runs.finish_run(stale, 'FAILED', expected_state='PENDING',
                                      error_code='RESOURCE_NOT_FOUND', repository=repo)
    if source == 'PENDING':
        assert committed is True
        assert current.state == stale.state == 'FAILED'
        assert p.consecutive_failures == 3 and p.last_run_id == current.id
        current.save.assert_awaited_once()
        p.save.assert_awaited_once()
        runs.notify_after_commit.assert_awaited_once()
    else:
        assert committed is False
        assert current.state == stale.state == source
        assert current.error_code is None and current.finished_at is None
        assert p.consecutive_failures == 2 and p.last_run_id is None
        current.save.assert_not_awaited()
        p.save.assert_not_awaited()
        runs.notify_after_commit.assert_not_awaited()
        assert not any(c[0] == 'NetworkAccessPolicyCollectionPlan' for c in calls)
    assert calls[:3] == [
        ('NetworkAccessPolicyCollectionRun', {'id': 'run-1', 'tenant_id': 't1', 'deleted': False}),
        ('db', 'transaction'),
        ('lock', {'id': 'run-1', 'tenant_id': 't1', 'deleted': False}),
    ]


@pytest.mark.asyncio
async def test_guarded_finish_without_live_row_does_not_create_fake_or_notify():
    repo = FakePolicyRunRepository(plans=[plan()])
    assert await runs.finish_run(run_row(), 'FAILED', expected_state='PENDING', repository=repo) is False
    assert repo.runs == {} and repo.plans['p1'].consecutive_failures == 0
    runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_guarded_orm_finish_can_commit_without_live_plan(orm):
    repo, rows, _ = orm
    current = run_row(save=AsyncMock())
    rows['NetworkAccessPolicyCollectionRun'].append(current)
    assert await runs.finish_run(current, 'FAILED', expected_state='PENDING', repository=repo) is True
    assert current.state == 'FAILED' and current.finished_at is not None
    current.save.assert_awaited_once()
    runs.notify_after_commit.assert_not_awaited()
