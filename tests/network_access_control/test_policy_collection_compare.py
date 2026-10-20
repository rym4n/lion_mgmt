# tests/network_access_control/test_policy_collection_compare.py
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from libs.error_code.exception import CustomError
from services.network_access_control import manager
from services.network_access_control.collection import pipeline
from tests.network_access_control.policy_collection_fakes import FakePolicyRunRepository, plan, run_row


class _Query:
    def __init__(self, store, condition):
        self.store, self.condition = store, condition

    async def all(self):
        self.store.conditions.append(self.condition)
        return []


class _Store:
    def __init__(self):
        self.conditions = []
        self.created = []


def _install(monkeypatch):
    store = _Store()
    monkeypatch.setattr(manager.NetworkAccessPresetPolicy, "filter", lambda cond: _Query(store, cond))
    monkeypatch.setattr(manager.NetworkAccessActualPolicy, "filter", lambda cond: _Query(store, cond))

    async def create_run(**kw):
        row = SimpleNamespace(id="cmp-1", json={"id": "cmp-1"}, save=AsyncMock(), **kw)
        store.created.append(row)
        return row

    monkeypatch.setattr(manager.NetworkAccessPolicyCompareRun, "create", create_run)
    return store


def _sql(condition) -> str:
    return repr(getattr(condition, "filters", {})) + "".join(_sql(child) for child in condition.children)


@pytest.mark.asyncio
async def test_compare_without_scope_is_unchanged(monkeypatch):
    store = _install(monkeypatch)
    await manager.run_policy_compare("t1", {}, "op")
    assert all("device_asset_id__in" not in _sql(c) for c in store.conditions)


@pytest.mark.asyncio
async def test_compare_scope_filters_both_sides(monkeypatch):
    store = _install(monkeypatch)
    await manager.run_policy_compare("t1", {"device_asset_ids": ["a1"], "collection_run_id": "run-1"}, "op")
    preset, actual = store.conditions
    assert "device_asset_id__in" in _sql(preset) and "device_asset_id__in" in _sql(actual)
    assert "collection_run_id" in _sql(actual)
    assert "collection_run_id" not in _sql(preset)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["a1", 1, {}, [""], [None], [1], ["a1", False]])
async def test_compare_rejects_bad_scope(monkeypatch, scope):
    _install(monkeypatch)
    with pytest.raises(CustomError):
        await manager.run_policy_compare("t1", {"device_asset_ids": scope}, "op")


@pytest.mark.asyncio
async def test_record_changes_false_writes_no_policy_change(monkeypatch):
    actual = SimpleNamespace(id="x", parse_status="parsed", rule_name="n", source_zone=None, source_address=None,
                             target_zone=None, target_address=None, protocol=None, port=None, action="permit",
                             accessory_project_id=None, device_asset_id="a1", raw_rule_id="n", json={})

    class Q2:
        def __init__(self, rows):
            self.rows = rows

        async def all(self):
            return self.rows

    monkeypatch.setattr(manager.NetworkAccessPresetPolicy, "filter", lambda cond: Q2([]))
    monkeypatch.setattr(manager.NetworkAccessActualPolicy, "filter", lambda cond: Q2([actual]))
    monkeypatch.setattr(manager.NetworkAccessPolicyCompareRun, "create",
                        AsyncMock(return_value=SimpleNamespace(id="cmp", save=AsyncMock(), json={"id": "cmp"})))
    monkeypatch.setattr(manager.NetworkAccessPolicyCompareItem, "create",
                        AsyncMock(return_value=SimpleNamespace(compare_type="added", json={})))
    change = AsyncMock()
    monkeypatch.setattr(manager, "create_policy_change_from_compare", change)
    await manager.run_policy_compare("t1", {"device_asset_ids": ["a1"]}, "op", record_changes=False)
    change.assert_not_awaited()
    await manager.run_policy_compare("t1", {"device_asset_ids": ["a1"]}, "op")
    change.assert_awaited_once()


@pytest.mark.asyncio
async def test_scoped_compare_keeps_unparsed_as_unparsed(monkeypatch):
    actual = SimpleNamespace(id="u1", parse_status="unparsed", rule_name="n", source_zone=None,
                             source_address=None, target_zone=None, target_address=None, protocol=None,
                             port=None, action="deny", accessory_project_id=None, device_asset_id="a1",
                             raw_rule_id="n", json={})

    class Q2:
        def __init__(self, rows):
            self.rows = rows

        async def all(self):
            return self.rows

    monkeypatch.setattr(manager.NetworkAccessPresetPolicy, "filter", lambda cond: Q2([]))
    monkeypatch.setattr(manager.NetworkAccessActualPolicy, "filter", lambda cond: Q2([actual]))
    monkeypatch.setattr(manager.NetworkAccessPolicyCompareRun, "create",
                        AsyncMock(return_value=SimpleNamespace(id="cmp", save=AsyncMock(), json={"id": "cmp"})))
    created = []

    async def create_item(**kw):
        created.append(kw)
        return SimpleNamespace(compare_type=kw["compare_type"], json={})

    monkeypatch.setattr(manager.NetworkAccessPolicyCompareItem, "create", create_item)
    change = AsyncMock()
    monkeypatch.setattr(manager, "create_policy_change_from_compare", change)
    await manager.run_policy_compare("t1", {"device_asset_ids": ["a1"]}, "op", record_changes=False)
    assert [c["compare_type"] for c in created] == ["unparsed"]
    change.assert_not_awaited()


@pytest.mark.asyncio
async def test_compare_scoped_to_applied_bound_devices(monkeypatch):
    compare = AsyncMock(return_value={"id": "cmp-9"})
    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    repo = FakePolicyRunRepository(plans=[plan()])
    run = run_row(state="APPLYING")
    repo.runs[run.id] = run
    applied = [{"resource_id": "r1", "device_asset_id": "a2"},
               {"resource_id": "r2", "device_asset_id": None},
               {"resource_id": "r3", "device_asset_id": "a1"},
               {"resource_id": "r4", "device_asset_id": "a2"}]
    await pipeline.compare_stage(plan(), run, applied, repository=repo)
    tenant, data, operator = compare.await_args.args
    assert tenant == "t1" and operator == "acc"
    assert data == {"device_asset_ids": ["a1", "a2"], "collection_run_id": run.id}
    assert compare.await_args.kwargs == {"record_changes": False}
    assert run.compare_run_id == "cmp-9"


@pytest.mark.asyncio
async def test_compare_skipped_without_bound_devices_and_failure_is_recorded(monkeypatch):
    compare = AsyncMock(side_effect=RuntimeError("db"))
    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    repo = FakePolicyRunRepository(plans=[plan()])
    run = run_row(state="APPLYING")
    repo.runs[run.id] = run
    await pipeline.compare_stage(plan(), run, [{"resource_id": "r2", "device_asset_id": None}], repository=repo)
    compare.assert_not_awaited()
    await pipeline.compare_stage(plan(), run, [{"resource_id": "r1", "device_asset_id": "a1"}], repository=repo)
    assert run.compare_error == "POLICY_COMPARE_FAILED" and run.compare_run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_record_changes_false_keeps_items_and_manual_history(monkeypatch, changed):
    store = _install(monkeypatch)
    fields = dict(rule_name="rule", source_zone=None, source_address=None, target_zone=None,
                  target_address=None, protocol=None, port=None, accessory_project_id=None,
                  device_asset_id="a1", raw_rule_id="rule", risk_level="medium", json={})
    actual = SimpleNamespace(id="actual", parse_status="parsed", action="permit", **fields)
    preset = SimpleNamespace(id="preset", action="deny", **fields)
    monkeypatch.setattr(manager.NetworkAccessPresetPolicy, "filter",
                        lambda cond: SimpleNamespace(all=AsyncMock(return_value=[preset] if changed else [])))
    monkeypatch.setattr(manager.NetworkAccessActualPolicy, "filter",
                        lambda cond: SimpleNamespace(all=AsyncMock(return_value=[actual])))
    items = []

    async def create_item(**kw):
        items.append(kw)
        return SimpleNamespace(compare_type=kw["compare_type"], json=kw)

    monkeypatch.setattr(manager.NetworkAccessPolicyCompareItem, "create", create_item)
    change = AsyncMock()
    monkeypatch.setattr(manager, "create_policy_change_from_compare", change)
    await manager.run_policy_compare("t1", {"device_asset_ids": ["a1"]}, "op", record_changes=False)
    assert [i["compare_type"] for i in items] == ["changed" if changed else "added"]
    assert store.created[0].total == 1
    change.assert_not_awaited()
    await manager.run_policy_compare("t1", {"device_asset_ids": ["a1"]}, "op")
    change.assert_awaited_once()
    assert change.await_args.args[2] == ("modified" if changed else "added")


@pytest.mark.asyncio
async def test_empty_scope_filters_both_sides_and_keeps_tenant_project(monkeypatch):
    store = _install(monkeypatch)
    await manager.run_policy_compare("t1", {"device_asset_ids": [], "accessory_project_id": "p1"}, "op")
    for condition in store.conditions:
        query = _sql(condition)
        assert "'device_asset_id__in': []" in query
        assert "'tenant_id': 't1'" in query and "'deleted': False" in query
        assert "'accessory_project_id': 'p1'" in query


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "NORMALIZING", "CANCELLED", "FAILED", "SUCCEEDED"])
async def test_compare_checks_live_applying_before_side_effect(monkeypatch, state):
    repo = FakePolicyRunRepository(plans=[plan()])
    current = run_row(state=state)
    repo.runs[current.id] = current
    stale = run_row(state="APPLYING")
    compare = AsyncMock(return_value={"id": "cmp"})
    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    save = AsyncMock(wraps=repo.save_fields)
    monkeypatch.setattr(repo, "save_fields", save)
    await pipeline.compare_stage(plan(), stale, [{"device_asset_id": "a1"}], repository=repo)
    compare.assert_not_awaited()
    save.assert_not_awaited()
    assert stale.state == state and current.compare_run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["CANCELLED", "FAILED"])
@pytest.mark.parametrize("failed", [False, True])
async def test_compare_discards_result_or_error_after_terminal(monkeypatch, terminal, failed, caplog):
    repo = FakePolicyRunRepository(plans=[plan()])
    current = run_row(state="APPLYING")
    repo.runs[current.id] = current
    stale = run_row(state="APPLYING")

    async def compare(*args, **kwargs):
        current.state = terminal
        if failed:
            raise RuntimeError("private-credential")
        return {"id": "cmp"}

    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    save = AsyncMock(wraps=repo.save_fields)
    monkeypatch.setattr(repo, "save_fields", save)
    await pipeline.compare_stage(plan(), stale, [{"device_asset_id": "a1"}], repository=repo)
    assert current.compare_run_id is None and current.compare_error is None
    assert stale.state == terminal and "private-credential" not in caplog.text
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_pipeline_compares_only_applied_devices_and_compare_is_advisory(monkeypatch, failed, caplog):
    from tests.network_access_control.test_policy_collection_apply import FakeNormalizer, RULE, device, document

    devices = [device("r1", "a1"), device("r2", "a2"), device("r3")]
    repo = FakePolicyRunRepository(plans=[plan()])
    row = run_row(state="COLLECTING", item_results=devices, device_total=3)
    repo.runs[row.id] = row

    async def collect(*args, **kwargs):
        row.state = "NORMALIZING"
        return devices

    async def apply(tenant, data, operator, *, max_items):
        if data["device_key"] == "resource:r2":
            raise RuntimeError("private-credential")
        return {"added": 1}

    monkeypatch.setattr(pipeline, "collect_stage", collect)
    monkeypatch.setattr(pipeline, "max_rules_per_device", AsyncMock(return_value=20000))
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    monkeypatch.setattr(pipeline.runs, "notify_after_commit", AsyncMock())
    compare = AsyncMock(return_value={"id": "cmp"},
                        side_effect=RuntimeError("private-credential") if failed else None)
    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    normalizer = FakeNormalizer({rid: pipeline.NormalizeResult(rid, "success", document(rid, [RULE]), None)
                                 for rid in ("r1", "r2", "r3")})
    await pipeline.run_pipeline(plan(), row, normalizer=normalizer, repository=repo)
    compare.assert_awaited_once_with("t1", {"device_asset_ids": ["a1"], "collection_run_id": row.id},
                                     "acc", record_changes=False)
    assert row.state == "PARTIAL" and row.device_succeeded == 2
    assert row.compare_run_id == (None if failed else "cmp")
    assert row.compare_error == ("POLICY_COMPARE_FAILED" if failed else None)
    assert "private-credential" not in caplog.text


def test_default_normalizer_factory_links_to_delayed_agent_stage(monkeypatch):
    import sys

    sentinel = object()
    def factory():
        return sentinel
    monkeypatch.setitem(sys.modules, "services.network_access_control.collection.agent_stage",
                        SimpleNamespace(build_agent_normalizer=factory))
    assert pipeline.default_normalizer() is sentinel
