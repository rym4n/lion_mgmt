import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from services.network_topology.collection import dispatch, targets as targets_module, manifest_issue
from services.network_topology.collection.dispatch import ExecResult
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.targets import Target
from tests.network_topology.auto_collection_fakes import FakeTargetRepository, plan, run_row


def target(resource_id, item_id="i"):
    return Target(item_id, "huawei_switch", "v", "main.py", "collect", "采集", {}, resource_id, resource_id, "10.0.0.1", None)


def item(**kw):
    values = dict(id="i", app_name="huawei_switch", app_version=None, action_func="collect", action_input={},
                  resources=[{"resource_id": "r1", "asset_instance_id": "a1"}], include_new_resources=False, enabled=True)
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_dispatch_respects_concurrency_and_records_failures():
    active = peak = 0
    async def executor(tenant_id, target, *, timeout, executor_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return ExecResult(-1 if target.resource_id == "bad" else 0,
                          '{"code":500,"msg":"login failed"}' if target.resource_id == "bad" else '{"code":200,"data":{"records":[]}}', 10)
    outcomes = await dispatch.run_dispatch(plan("p", concurrency=2), run_row("run"),
        [target(f"r{n}") for n in range(5)] + [target("bad")], executor=executor, persist=False)
    assert 1 < peak <= 2
    assert [(o.resource_id, o.error_code) for o in outcomes if o.status == "failed"] == [("bad", "COLLECTION_ACTION_FAILED")]


@pytest.mark.asyncio
async def test_freeze_does_not_shrink_missing_resource_scope():
    repo = FakeTargetRepository(resources={}, tools={"huawei_switch": ("v", "main.py")}, actions={("huawei_switch", "v"): [{"func":"collect", "name":"采集"}]})
    with pytest.raises(CollectionError) as exc:
        await targets_module.freeze_targets(plan("p"), [item()], repository=repo)
    assert exc.value.code == "RESOURCE_NOT_FOUND"
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_freeze_latest_tool_expansion_dedup_and_host():
    repo = FakeTargetRepository(resources={"r1":{"id":"r1", "name":"one", "resources_json":{"host":{"value":"10.0.0.1"}}}, "r2":{"id":"r2", "name":"two", "resources_json":{"ip":{"value":"10.0.0.2"}}}}, tools={"huawei_switch":("v", "main.py")}, actions={("huawei_switch","v"):[{"func":"collect","name":"采集"}]})
    frozen = await targets_module.freeze_targets(plan("p"), [item(include_new_resources=True), item(enabled=False)], repository=repo)
    assert [(t.resource_id, t.host, t.asset_instance_id) for t in frozen] == [("r1","10.0.0.1","a1"), ("r2","10.0.0.2",None)]
    assert all(t.app_version == "v" and t.main == "main.py" for t in frozen)


@pytest.mark.asyncio
@pytest.mark.parametrize("text,code", [('not json','COLLECTION_OUTPUT_NOT_JSON'), ('{"code":500,"msg":"bad"}','COLLECTION_ACTION_FAILED')])
async def test_dispatch_output_failures(text, code):
    async def executor(*args, **kwargs):
        return ExecResult(0, text, 1)
    outcomes = await dispatch.run_dispatch(plan("p", concurrency=1), run_row("run"), [target("r")], executor=executor, persist=False)
    assert outcomes[0].error_code == code

@pytest.mark.asyncio
async def test_executor_failure_status_does_not_parse_invalid_output():
    async def executor(*args, **kwargs):
        return ExecResult(-1, None, 1)
    outcomes = await dispatch.run_dispatch(plan("p", concurrency=1), run_row("run"), [target("r")], executor=executor, persist=False)
    assert outcomes[0].error_code == "COLLECTION_ACTION_FAILED"

@pytest.mark.asyncio
async def test_background_start_only_dispatches_once():
    row = run_row("run")
    row.save_calls = 0
    async def save(**kwargs):
        row.save_calls += 1
    row.save = save
    calls = 0
    async def collect(*args, **kwargs):
        nonlocal calls
        calls += 1
    monkey = dispatch.collect_and_store
    dispatch.collect_and_store = collect
    try:
        await asyncio.gather(dispatch.start_background(plan("p"), row), dispatch.start_background(plan("p"), row))
        await asyncio.sleep(0)
        assert calls == 1
    finally:
        dispatch.collect_and_store = monkey


@pytest.mark.asyncio
async def test_dispatch_persists_summary_without_output_and_counts_devices():
    class Repo:
        async def save_run(self, row):
            self.saved = row
    repo = Repo()
    row = run_row("run")
    async def executor(*args, **kwargs):
        return ExecResult(0, '{"code":200,"password":"secret"}', 1)
    outcomes = await dispatch.run_dispatch(plan("p", concurrency=2), row, [target("r", "i1"), target("r", "i2")], executor=executor, repository=repo)
    assert row.device_succeeded == 1
    assert row.state == "COLLECTING"
    assert all("output" not in x for x in row.item_results)
    assert "secret" not in str(outcomes)
