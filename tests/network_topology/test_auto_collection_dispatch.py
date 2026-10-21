import asyncio
from types import SimpleNamespace
from datetime import UTC, datetime

import pytest

from services.network_topology.collection import dispatch, targets as targets_module
from services.network_topology.collection.dispatch import ExecResult
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.targets import Target
from tests.network_topology.auto_collection_fakes import FakeTargetRepository, plan, run_row


def target(resource_id, item_id="i"):
    return Target(item_id, "huawei_switch", "v", "main.py", "collect", "采集", {}, resource_id, resource_id, "10.0.0.1", None)


def test_collection_window_start_never_precedes_manifest_issue_time():
    run_started = datetime(2026, 10, 20, 23, 29, 57, 728958, tzinfo=UTC)

    started = dispatch._collection_window_start(
        run_started, "2026-10-20T23:29:58Z"
    )

    assert started == datetime(2026, 10, 20, 23, 29, 58, tzinfo=UTC)


def test_collection_window_start_preserves_run_time_when_manifest_is_earlier():
    run_started = datetime(2026, 10, 20, 23, 29, 58, 728958, tzinfo=UTC)

    started = dispatch._collection_window_start(
        run_started, "2026-10-20T23:29:58Z"
    )

    assert started == run_started


@pytest.mark.asyncio
async def test_collect_and_store_uses_manifest_issue_time_for_window_start(monkeypatch):
    from dataclasses import asdict
    from types import SimpleNamespace

    run = run_row(
        "run",
        started_at=datetime(2026, 10, 20, 23, 29, 57, 728958, tzinfo=UTC),
        frozen_targets=[asdict(target("r1"))],
        manifest_id="manifest-1",
        device_total=1,
        item_results=[],
    )
    manifest_row = SimpleNamespace(
        manifest_id="manifest-1",
        manifest_hash="sha256:" + "a" * 64,
        collection_sequence=1,
        base_graph_revision=0,
        base_publication_revision=0,
        profile_revision=1,
        issued_at=datetime(2026, 10, 20, 23, 29, 58, tzinfo=UTC),
        expires_at=datetime(2026, 10, 20, 23, 59, 58, tzinfo=UTC),
        expected_resources_json='[{"resource_id":"r1","required_kinds":[]}]',
    )

    class Query:
        async def first(self):
            return manifest_row

    class Repository:
        async def save_run(self, row):
            self.saved = row

    captured = {}

    async def fake_dispatch(*args, **kwargs):
        run.state = "COLLECTING"
        return [SimpleNamespace(status="success")]

    def fake_build_snapshot(**kwargs):
        captured.update(kwargs)
        return {"snapshot": "draft"}

    monkeypatch.setattr(dispatch, "run_dispatch", fake_dispatch)
    monkeypatch.setattr(dispatch.TopologyCollectionManifest, "filter", lambda **_: Query())
    monkeypatch.setattr(dispatch, "build_snapshot", fake_build_snapshot)

    await dispatch.collect_and_store(plan("p"), run, repository=Repository())

    assert captured["started_at"] == "2026-10-20T23:29:58Z"


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
async def test_freeze_uses_only_selected_resources_even_for_legacy_items():
    repo = FakeTargetRepository(resources={"r1":{"id":"r1", "name":"one", "resources_json":{"host":{"value":"10.0.0.1"}}}, "r2":{"id":"r2", "name":"two", "resources_json":{"ip":{"value":"10.0.0.2"}}}}, tools={"huawei_switch":("v", "main.py")}, actions={("huawei_switch","v"):[{"func":"collect","name":"采集"}]})
    frozen = await targets_module.freeze_targets(plan("p"), [item(include_new_resources=True, enabled=False)], repository=repo)
    assert [(t.resource_id, t.host, t.asset_instance_id) for t in frozen] == [("r1","10.0.0.1",None)]
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
    stale = run_row("run")
    class Repo:
        state = "MANIFEST_FROZEN"
        async def claim_collection_start(self, run):
            claimed = self.state == "MANIFEST_FROZEN"
            self.state = "COLLECTING"
            await asyncio.sleep(0)
            return claimed
        async def get_run(self, *args, **kwargs):
            return SimpleNamespace(state=self.state)
    repo = Repo()
    calls = 0
    async def collect(*args, **kwargs):
        nonlocal calls
        calls += 1
    monkey = dispatch.collect_and_store
    dispatch.collect_and_store = collect
    try:
        results = await asyncio.gather(dispatch.start_background(plan("p"), row, repository=repo),
                                       dispatch.start_background(plan("p"), stale, repository=repo))
        await asyncio.sleep(0)
        assert calls == 1
        assert all(result["run_state"] == "COLLECTING" for result in results)
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
