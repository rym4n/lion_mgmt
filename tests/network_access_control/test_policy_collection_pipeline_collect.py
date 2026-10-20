"""Frozen collection, dispatch CAS, and terminal-state side-effect boundaries."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import AsyncMock

import orjson
import pytest

from services.action_collection.errors import CollectionError
from services.action_collection.execute import ItemOutcome
from services.action_collection.executor import ExecResult
from services.action_collection.targets import Target
from services.network_access_control.collection import pipeline
from tests.network_access_control.policy_collection_fakes import FakePolicyRunRepository, item, plan, run_row


def target(rid="r1", asset=None):
    return Target("i1", "huawei_usg", "v1", "main.py", "collect_policy_observations", "采集策略", {},
                  rid, f"fw-{rid}", "10.0.0.1", asset)


def outcome(rid="r1", ok=True, output=None, action_run_id=None):
    return ItemOutcome("i1", "huawei_usg", "collect_policy_observations", "v1", rid,
                       "success" if ok else "failed", output if ok else None, "2026-10-20T00:00:00Z",
                       None if ok else "COLLECTION_ACTION_FAILED", None if ok else "login failed", 12,
                       action_run_id)


def collecting(repo, **values):
    values.setdefault("frozen_targets", [asdict(target())])
    row = run_row(state="COLLECTING", device_total=1, **values)
    repo.runs[row.id] = row
    return row


@pytest.fixture(autouse=True)
def no_notification(monkeypatch):
    monkeypatch.setattr(pipeline.runs, "notify_after_commit", AsyncMock())


@pytest.mark.asyncio
async def test_freeze_claim_persists_asset_binding_heartbeat_and_only_claims_once(monkeypatch):
    frozen = [target("r1", "a1"), target("r2")]
    freeze = AsyncMock(return_value=frozen)
    monkeypatch.setattr(pipeline, "freeze_targets", freeze)
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    row = run_row()
    repo.runs[row.id] = row
    target_repository = object()
    assert await pipeline.freeze_and_claim(plan(), row, target_repository=target_repository, repository=repo) is True
    assert row.state == "COLLECTING" and row.device_total == 2
    assert row.frozen_targets[0]["asset_instance_id"] == "a1"
    assert row.heartbeat_at.utcoffset().total_seconds() == 0
    assert row.frozen_targets[0]["app_version"] == "v1"
    freeze.assert_awaited_once_with(plan(), [item()], repository=target_repository)
    assert await pipeline.freeze_and_claim(plan(), row, repository=repo) is False
    frozen[0].action_input["changed"] = True
    assert row.frozen_targets[0]["action_input"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen,error,code,status", [
    (None, CollectionError("RESOURCE_NOT_FOUND", 409, "private-resource"), "RESOURCE_NOT_FOUND", 409),
    ([], None, "PLAN_EMPTY", 400),
])
async def test_freeze_errors_terminalize_with_stable_code(monkeypatch, frozen, error, code, status):
    monkeypatch.setattr(pipeline, "freeze_targets", AsyncMock(return_value=frozen, side_effect=error))
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    row = run_row()
    repo.runs[row.id] = row
    with pytest.raises(CollectionError) as exc:
        await pipeline.freeze_and_claim(plan(), row, repository=repo)
    assert (exc.value.code, exc.value.status, exc.value.message) == (code, status, code)
    assert (row.state, row.error_code) == ("FAILED", code)
    assert repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
async def test_concurrent_dispatch_only_starts_one_background_pipeline(monkeypatch):
    entered = 0
    both = asyncio.Event()

    async def freeze(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            both.set()
        await both.wait()
        return [target()]

    monkeypatch.setattr(pipeline, "freeze_targets", freeze)
    scheduled = []

    def track(coro, **kwargs):
        scheduled.append(kwargs)
        coro.close()

    monkeypatch.setattr(pipeline, "start_tracked", track)
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    row = run_row()
    repo.runs[row.id] = row
    first, second = await asyncio.gather(
        pipeline.start_pipeline(plan(), deepcopy(row), repository=repo),
        pipeline.start_pipeline(plan(), deepcopy(row), repository=repo),
    )
    assert first == second == {"collection_run_id": "run-1", "run_state": "COLLECTING"}
    assert len(scheduled) == 1
    assert scheduled[0]["run_model"] is pipeline.NetworkAccessPolicyCollectionRun
    assert scheduled[0]["run"].id == row.id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["CANCELLED", "FAILED", "SUCCEEDED", "PARTIAL", "COLLECTING"])
async def test_stale_pending_run_does_not_freeze_or_dispatch(monkeypatch, state):
    freeze = AsyncMock(side_effect=AssertionError("must not freeze"))
    monkeypatch.setattr(pipeline, "freeze_targets", freeze)
    repo = FakePolicyRunRepository(plans=[plan()])
    stale = run_row()
    repo.runs[stale.id] = run_row(state=state)
    assert await pipeline.start_pipeline(plan(), stale, repository=repo) == {
        "collection_run_id": "run-1", "run_state": state}
    freeze.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_during_freeze_does_not_claim_or_start(monkeypatch):
    repo = FakePolicyRunRepository(plans=[plan()], items=[item()])
    row = run_row()
    repo.runs[row.id] = row

    async def freeze(*args, **kwargs):
        await pipeline.runs.finish_run(deepcopy(row), "CANCELLED", repository=repo)
        return [target()]

    monkeypatch.setattr(pipeline, "freeze_targets", freeze)
    assert await pipeline.start_pipeline(plan(), row, repository=repo) == {
        "collection_run_id": "run-1", "run_state": "CANCELLED"}
    assert row.frozen_targets is None


@pytest.mark.asyncio
async def test_collect_scrubs_raw_outputs_and_maps_action_run_ids(monkeypatch):
    output = {"code": 200, "data": {"outputs": [{"source": "display security-policy rule all",
                                                 "content": "rule name a\n password=hunter2"}]}}
    monkeypatch.setattr(pipeline, "execute_targets", AsyncMock(return_value=[
        outcome(output=output, action_run_id="action-1"), outcome("r2", ok=False, action_run_id="action-2")]))
    repo = FakePolicyRunRepository(plans=[plan()])
    row = collecting(repo, frozen_targets=[asdict(target("r1", "a1")), asdict(target("r2"))])
    succeeded = await pipeline.collect_stage(plan(), row, repository=repo)
    assert [entry["resource_id"] for entry in succeeded] == ["r1"]
    assert (row.state, row.device_succeeded) == ("NORMALIZING", 1)
    raw = orjson.loads(row.raw_outputs)
    assert list(raw) == ["r1"] and raw["r1"][0]["app_version"] == "v1"
    assert raw["r1"][0]["item_id"] == "i1" and "hunter2" not in row.raw_outputs
    assert "hunter2" in output["data"]["outputs"][0]["content"]
    by_id = {entry["resource_id"]: entry for entry in row.item_results}
    assert by_id["r1"]["device_asset_id"] == "a1" and by_id["r1"]["stage"] == "collect"
    assert by_id["r1"]["lion_action_run_id"] == "action-1"
    assert by_id["r2"]["lion_action_run_id"] == "action-2"
    assert by_id["r2"]["status"] == "failed" and by_id["r2"]["error_code"] == "COLLECTION_ACTION_FAILED"
    assert by_id["r2"]["error_message"] == "login failed"
    assert by_id["r1"]["policy_count"] is None and by_id["r1"]["sync"] is None
    projected = pipeline.runs.public_devices(row.item_results)
    assert all("lion_action_run_id" not in entry and "error_message" not in entry for entry in projected)


@pytest.mark.asyncio
async def test_collect_uses_frozen_target_and_operator_with_real_execute():
    repo = FakePolicyRunRepository(plans=[plan()], items=[item(app_version="v99")])
    row = collecting(repo, triggered_by=None)
    calls = []

    async def executor(tenant_id, frozen, *, timeout, executor_id):
        calls.append((tenant_id, frozen.app_version, frozen.resource_id, timeout, executor_id))
        return ExecResult(0, '{"code":200,"data":{}}', 23)

    assert len(await pipeline.collect_stage(plan(), row, executor=executor, repository=repo)) == 1
    assert calls == [("t1", "v1", "r1", 300, pipeline.runs.AUTO_OPERATOR)]
    assert row.item_results[0]["lion_action_run_id"] is None
    assert row.item_results[0]["error_message"] is None and row.item_results[0]["duration_ms"] == 23


@pytest.mark.asyncio
async def test_collect_all_failed_is_incomplete(monkeypatch):
    monkeypatch.setattr(pipeline, "execute_targets", AsyncMock(return_value=[outcome(ok=False)]))
    repo = FakePolicyRunRepository(plans=[plan()])
    row = collecting(repo)
    assert await pipeline.collect_stage(plan(), row, repository=repo) == []
    assert (row.state, row.error_code, row.device_succeeded) == ("FAILED", "COLLECTION_INCOMPLETE", 0)
    assert row.item_results[0]["status"] == "failed" and row.raw_outputs is None
    assert repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["CANCELLED", "FAILED", "SUCCEEDED", "PARTIAL", "NORMALIZING"])
async def test_collect_stale_object_checks_live_state_before_execution(monkeypatch, state):
    execute = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(pipeline, "execute_targets", execute)
    repo = FakePolicyRunRepository(plans=[plan()])
    stale = collecting(repo)
    repo.runs[stale.id] = run_row(state=state)
    assert await pipeline.collect_stage(plan(), stale, repository=repo) == []
    assert repo.runs[stale.id].raw_outputs is None and stale.state == state
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
@pytest.mark.parametrize("terminal", ["CANCELLED", "FAILED"])
async def test_terminalization_during_execution_discards_outputs(monkeypatch, ok, terminal):
    repo = FakePolicyRunRepository(plans=[plan()])
    row = collecting(repo)
    stale = deepcopy(row)

    async def execute(*args, **kwargs):
        await pipeline.runs.finish_run(row, terminal, error_code="COLLECTION_WORKER_LOST" if terminal == "FAILED" else None,
                                       repository=repo)
        return [outcome(ok=ok, output={"code": 200})]

    monkeypatch.setattr(pipeline, "execute_targets", execute)
    assert await pipeline.collect_stage(plan(), stale, repository=repo) == []
    assert row.state == terminal and row.raw_outputs is None and row.item_results is None
    assert stale.state == terminal


@pytest.mark.asyncio
async def test_missing_or_other_tenant_run_never_executes(monkeypatch):
    execute = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(pipeline, "execute_targets", execute)
    repo = FakePolicyRunRepository(plans=[plan()])
    row = collecting(repo)
    row.tenant_id = "other-tenant"
    assert await pipeline.collect_stage(plan(), run_row(state="COLLECTING"), repository=repo) == []
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code", [
    (CollectionError("COLLECTION_OUTPUT_TOO_LARGE", message="private-output"), "COLLECTION_OUTPUT_TOO_LARGE"),
    (RuntimeError("password=private-output"), "COLLECTION_DISPATCH_FAILED"),
])
async def test_pipeline_exception_terminalizes_without_exception_text(monkeypatch, caplog, error, code):
    monkeypatch.setattr(pipeline, "execute_targets", AsyncMock(side_effect=error))
    repo = FakePolicyRunRepository(plans=[plan()])
    row = collecting(repo)
    await pipeline.run_pipeline(plan(), row, repository=repo)
    assert (row.state, row.error_code) == ("FAILED", code)
    assert row.error_message is None and "private-output" not in caplog.text


@pytest.mark.asyncio
async def test_pipeline_collect_only_does_not_invoke_future_normalizer(monkeypatch):
    monkeypatch.setattr(pipeline, "execute_targets", AsyncMock(return_value=[outcome(output={"code": 200})]))
    repo = FakePolicyRunRepository(plans=[plan()])
    row = collecting(repo)
    normalizer = AsyncMock(side_effect=AssertionError("Task 5 owns normalize"))
    await pipeline.run_pipeline(plan(), row, normalizer=normalizer, repository=repo)
    assert row.state == "NORMALIZING" and row.raw_outputs is not None
    normalizer.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_duplicate_freezer_cannot_terminalize_winner_at_terminal_lock_boundary(monkeypatch):
    """The source-state guard must live in the terminal transaction, not a precheck."""
    entered_terminal = asyncio.Event()
    resume_terminal = asyncio.Event()

    class GatedRepository(FakePolicyRunRepository):
        async def finish_terminal(self, *args, **kwargs):
            entered_terminal.set()
            await resume_terminal.wait()
            return await super().finish_terminal(*args, **kwargs)

    repo = GatedRepository(plans=[plan()], items=[item()])
    row = run_row()
    repo.runs[row.id] = row
    stale = deepcopy(row)
    monkeypatch.setattr(pipeline, 'freeze_targets', AsyncMock(
        side_effect=CollectionError('RESOURCE_NOT_FOUND', 409, 'private-resource')))
    loser = asyncio.create_task(pipeline.start_pipeline(plan(), stale, repository=repo))
    await asyncio.wait_for(entered_terminal.wait(), 1)
    monkeypatch.setattr(pipeline, 'freeze_targets', AsyncMock(return_value=[target()]))
    try:
        assert await pipeline.freeze_and_claim(plan(), deepcopy(row), repository=repo) is True
    finally:
        resume_terminal.set()
    result = await asyncio.wait_for(loser, 1)
    assert result == {'collection_run_id': 'run-1', 'run_state': 'COLLECTING'}
    assert (row.state, row.error_code, row.finished_at) == ('COLLECTING', None, None)
    assert row.device_total == 1 and row.frozen_targets[0]['resource_id'] == 'r1'
    assert repo.plans['p1'].consecutive_failures == 0 and repo.plans['p1'].last_run_id is None
    pipeline.runs.notify_after_commit.assert_not_awaited()
