# tests/network_access_control/test_policy_collection_apply.py
from unittest.mock import AsyncMock

import pytest

from libs.error_code.exception import CustomError
from core.error_code import ErrorCode
from services.network_access_control.collection import pipeline
from services.network_access_control.collection.pipeline import NormalizeResult
from tests.network_access_control.policy_collection_fakes import FakePolicyRunRepository, plan, run_row


def device(rid, asset=None):
    return {"resource_id": rid, "resource_name": f"fw-{rid}", "device_asset_id": asset, "app_name": "huawei_usg",
            "action_func": "collect_policy_observations", "item_id": "i1", "stage": "collect", "status": "success",
            "error_code": None, "error_message": None, "policy_count": None, "sync": None, "duration_ms": 3,
            "lion_action_run_id": None}


def document(rid, policies):
    return {"resource_id": rid, "device_type": "firewall", "policy_type": "firewall", "policies": policies}


RULE = {"policy_key": "r-a", "rule_name": "a", "source_address": "10.0.0.0/8", "target_address": "any",
        "destination_port": "443", "protocol": "tcp", "action": "permit", "parse_status": "parsed",
        "raw_text": "rule name a", "policy_order": 1}


class FakeNormalizer:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def __call__(self, plan_row, run, resource_ids):
        self.calls.append(resource_ids)
        return {rid: self.results[rid] for rid in resource_ids if rid in self.results}


def setup(monkeypatch, devices, counts=None):
    repo = FakePolicyRunRepository(plans=[plan()], policy_counts=counts or {})
    run = run_row(state="NORMALIZING", item_results=devices, device_total=len(devices),
                  device_succeeded=len(devices))
    repo.runs[run.id] = run
    monkeypatch.setattr(pipeline, "max_rules_per_device", AsyncMock(return_value=20000))
    monkeypatch.setattr(pipeline.runs, "notify_after_commit", AsyncMock())
    return repo, run


def test_policy_item_maps_fields_and_keeps_inferred_action():
    item = pipeline.policy_item({**RULE, "parse_status": "unparsed", "action": "deny", "action_inferred": False})
    assert item["port"] == "443" and item["action"] == "deny" and item["parse_status"] == "unparsed"
    assert item["policy_data"] == {"destination_port": "443", "policy_order": 1, "action_inferred": False}
    assert "collection_run_id" not in item["policy_data"]


def test_long_address_is_fitted_and_full_text_kept():
    long_value = ",".join(f"10.0.{i}.0/24" for i in range(60))
    item = pipeline.policy_item({**RULE, "source_address": long_value})
    assert len(item["source_address"]) == 255 and item["source_address"].endswith("...")
    assert item["policy_data"]["full_source_address"] == long_value


@pytest.mark.asyncio
async def test_normalize_failure_is_per_device(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1"), device("r2")])
    normalizer = FakeNormalizer({
        "r1": NormalizeResult("r1", "success", document("r1", [RULE]), None),
        "r2": NormalizeResult("r2", "failed", None, "POLICY_NORMALIZE_FAILED")})
    ok = await pipeline.normalize_stage(plan(), run, run.item_results, normalizer=normalizer, repository=repo)
    assert [d["resource_id"] for d in ok] == ["r1"]
    by_id = {d["resource_id"]: d for d in run.item_results}
    assert by_id["r2"]["stage"] == "normalize" and by_id["r2"]["error_code"] == "POLICY_NORMALIZE_FAILED"
    assert by_id["r1"]["policy_count"] == 1 and "_document" not in by_id["r1"]
    assert run.state == "APPLYING"


@pytest.mark.asyncio
async def test_normalizer_missing_result_and_crash(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1")])
    ok = await pipeline.normalize_stage(plan(), run, run.item_results, normalizer=FakeNormalizer({}),
                                        repository=repo)
    assert ok == [] and run.item_results[0]["error_code"] == "POLICY_NORMALIZE_FAILED"
    repo, run = setup(monkeypatch, [device("r1")])
    crash = AsyncMock(side_effect=RuntimeError("boom"))
    ok = await pipeline.normalize_stage(plan(), run, run.item_results, normalizer=crash, repository=repo)
    assert ok == [] and run.item_results[0]["error_code"] == "AGENT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_empty_result_keeps_existing_policies(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1")], counts={"resource:r1": 7})
    apply = AsyncMock()
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    devices = [{**device("r1"), "_document": document("r1", [])}]
    run.state = "APPLYING"
    applied = await pipeline.apply_stage(plan(), run, devices, repository=repo)
    assert applied == [] and apply.await_count == 0
    assert run.item_results[0]["error_code"] == "POLICY_EMPTY_RESULT"
    assert run.item_results[0]["stage"] == "apply"


@pytest.mark.asyncio
async def test_empty_result_on_new_device_is_applied(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1")], counts={"resource:r1": 0})
    apply = AsyncMock(return_value={"added": 0, "modified": 0, "moved": 0, "deleted": 0, "unchanged": 0})
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    devices = [{**device("r1"), "_document": document("r1", [])}]
    run.state = "APPLYING"
    assert len(await pipeline.apply_stage(plan(), run, devices, repository=repo)) == 1


@pytest.mark.asyncio
async def test_apply_payload_contract(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1", "a1")])
    apply = AsyncMock(return_value={"added": 1, "modified": 0, "moved": 0, "deleted": 2, "unchanged": 0})
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    run.state = "APPLYING"
    await pipeline.apply_stage(plan(), run, [{**device("r1", "a1"), "_document": document("r1", [RULE])}],
                               repository=repo)
    tenant, data, operator = apply.await_args.args
    assert tenant == "t1" and operator == "acc" and apply.await_args.kwargs == {"max_items": 20000}
    assert data["replace"] is True and data["device_key"] == "resource:r1"
    assert data["device_asset_id"] == "a1" and data["device_name"] == "fw-r1"
    assert data["sync_source"] == "auto_collection" and data["collection_run_id"] == run.id
    assert data["batch_id"] == f"{run.id}:r1"
    assert data["device_type"] == "firewall" and data["policy_type"] == "firewall"
    assert run.item_results[0]["sync"] == {"added": 1, "modified": 0, "moved": 0, "deleted": 2, "unchanged": 0}


@pytest.mark.asyncio
async def test_partial_run_isolates_failed_device(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1", "a1"), device("r2", "a2")], counts={"resource:r2": 4})

    async def apply(tenant, data, operator, *, max_items):
        if data["device_key"] == "resource:r2":
            raise CustomError(ErrorCode.PARAM_ERROR)
        return {"added": 1, "modified": 0, "moved": 0, "deleted": 0, "unchanged": 0}

    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    monkeypatch.setattr(pipeline, "compare_stage", AsyncMock())
    normalizer = FakeNormalizer({rid: NormalizeResult(rid, "success", document(rid, [RULE]), None)
                                 for rid in ("r1", "r2")})
    run.state = "COLLECTING"
    async def collect(*args, **kwargs):
        return await _advance(run)

    monkeypatch.setattr(pipeline, "collect_stage", collect)
    await pipeline.run_pipeline(plan(), run, normalizer=normalizer, repository=repo)
    assert run.state == "PARTIAL"
    by_id = {d["resource_id"]: d for d in run.item_results}
    assert by_id["r2"]["error_code"] == "POLICY_SYNC_FAILED" and by_id["r1"]["status"] == "success"
    assert repo.plans["p1"].consecutive_failures == 0 and run.device_succeeded == 1


async def _advance(run):
    run.state = "NORMALIZING"
    return list(run.item_results)


@pytest.mark.asyncio
async def test_finalize_states(monkeypatch):
    for statuses, expected, code in ((["success", "success"], "SUCCEEDED", None),
                                     (["failed", "failed"], "FAILED", "POLICY_SYNC_LIMIT")):
        devices = [{**device(f"r{i}"), "stage": "apply", "status": s,
                    "error_code": None if s == "success" else "POLICY_SYNC_LIMIT"}
                   for i, s in enumerate(statuses)]
        repo, run = setup(monkeypatch, devices)
        run.state = "APPLYING"
        await pipeline.finalize(run, repository=repo)
        assert (run.state, run.error_code) == (expected, code)


@pytest.mark.parametrize("field,limit", [("rule_name", 255), ("source_zone", 100), ("source_address", 255),
                                         ("target_zone", 100), ("target_address", 255),
                                         ("protocol", 30), ("port", 100)])
def test_all_ledger_field_limits_preserve_full_values(field, limit):
    input_field = "destination_port" if field == "port" else field
    value = "x" * (limit + 20)
    item = pipeline.policy_item({**RULE, input_field: value})
    assert item[field] == "x" * (limit - 3) + "..."
    assert item["policy_data"][f"full_{field}"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["CANCELLED", "FAILED"])
async def test_stale_normalizer_does_not_start_after_terminal(monkeypatch, terminal):
    from copy import deepcopy

    repo, run = setup(monkeypatch, [device("r1")])
    stale = deepcopy(run)
    run.state = terminal
    normalizer = AsyncMock()
    assert await pipeline.normalize_stage(plan(), stale, stale.item_results,
                                           normalizer=normalizer, repository=repo) == []
    normalizer.assert_not_awaited()
    assert stale.state == terminal and run.item_results[0]["stage"] == "collect"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["CANCELLED", "FAILED"])
async def test_normalizer_result_discarded_when_terminal_wins(monkeypatch, terminal):
    from copy import deepcopy

    repo, run = setup(monkeypatch, [device("r1")])
    stale = deepcopy(run)

    async def normalize(*args):
        run.state = terminal
        return {"r1": NormalizeResult("r1", "success", document("r1", [RULE]), None)}

    assert await pipeline.normalize_stage(plan(), stale, stale.item_results,
                                           normalizer=normalize, repository=repo) == []
    assert run.item_results[0]["stage"] == "collect" and stale.state == terminal


@pytest.mark.asyncio
async def test_lost_normalizing_claim_prevents_apply(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1")])
    run.state = "COLLECTING"

    async def collect(*args, **kwargs):
        return await _advance(run)

    monkeypatch.setattr(pipeline, "collect_stage", collect)
    monkeypatch.setattr(repo, "transition", AsyncMock(return_value=False))
    apply = AsyncMock()
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    normalizer = FakeNormalizer({"r1": NormalizeResult("r1", "success", document("r1", [RULE]), None)})
    await pipeline.run_pipeline(plan(), run, normalizer=normalizer, repository=repo)
    apply.assert_not_awaited()
    assert run.state == "NORMALIZING" and run.finished_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before", "limit", "count", "apply"])
async def test_apply_stops_after_terminal_at_await_boundaries(monkeypatch, boundary):
    from copy import deepcopy

    repo, run = setup(monkeypatch, [device("r1"), device("r2")])
    run.state = "APPLYING"
    stale = deepcopy(run)
    writes = []

    async def limit():
        if boundary == "limit":
            run.state = "FAILED"
        return 20000

    async def count(*args):
        if boundary == "count":
            run.state = "CANCELLED"
        return 0

    async def apply(tenant, data, operator, *, max_items):
        writes.append(data["device_key"])
        if boundary == "apply":
            run.state = "CANCELLED"
        return {"added": 1}

    monkeypatch.setattr(pipeline, "max_rules_per_device", limit)
    monkeypatch.setattr(repo, "count_policies", count)
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    if boundary == "before":
        run.state = "FAILED"
    docs = [{**d, "_document": document(d["resource_id"], [] if boundary == "count" else [RULE])}
            for d in stale.item_results]
    assert await pipeline.apply_stage(plan(), stale, docs, repository=repo) == []
    assert writes == (["resource:r1"] if boundary == "apply" else [])
    assert all(d["stage"] == "collect" for d in run.item_results)
    assert stale.state in ("CANCELLED", "FAILED")


@pytest.mark.asyncio
async def test_apply_limit_rejects_only_oversized_device(monkeypatch):
    repo, run = setup(monkeypatch, [device("r1"), device("r2")])
    run.state = "APPLYING"
    monkeypatch.setattr(pipeline, "max_rules_per_device", AsyncMock(return_value=1))
    apply = AsyncMock(return_value={"added": 1})
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    result = await pipeline.apply_stage(plan(), run, [
        {**device("r1"), "_document": document("r1", [RULE, {**RULE, "policy_key": "b"}])},
        {**device("r2"), "_document": document("r2", [RULE])}], repository=repo)
    assert [d["resource_id"] for d in result] == ["r2"]
    assert run.item_results[0]["error_code"] == "POLICY_SYNC_LIMIT"
    assert apply.await_args.args[1]["device_key"] == "resource:r2"


@pytest.mark.asyncio
async def test_finalize_uses_frozen_order_for_equal_error_counts(monkeypatch):
    repo, run = setup(monkeypatch, [
        {**device("r1"), "status": "failed", "error_code": "POLICY_EMPTY_RESULT"},
        {**device("r2"), "status": "failed", "error_code": "POLICY_SYNC_LIMIT"}])
    run.state = "APPLYING"
    await pipeline.finalize(run, repository=repo)
    assert run.state == "FAILED" and run.error_code == "POLICY_EMPTY_RESULT"
    assert run.device_succeeded == 0 and repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["CANCELLED", "FAILED", "NORMALIZING"])
async def test_finalize_cannot_overwrite_state_changed_under_terminal_lock(monkeypatch, state):
    repo, run = setup(monkeypatch, [{**device("r1"), "stage": "apply"}])
    run.state = "APPLYING"
    finish = repo.finish_terminal

    async def race(*args, **kwargs):
        run.state = state
        run.error_code = "COLLECTION_WORKER_LOST" if state == "FAILED" else None
        return await finish(*args, **kwargs)

    monkeypatch.setattr(repo, "finish_terminal", race)
    await pipeline.finalize(run, repository=repo)
    assert run.state == state
    assert repo.plans["p1"].last_run_id is None
    pipeline.runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalize_preserves_collection_failure_in_final_partial_run(monkeypatch):
    devices = [device("r1"), {**device("r2"), "status": "failed", "error_code": "COLLECTION_TIMEOUT"}]
    repo, run = setup(monkeypatch, devices)
    normalizer = FakeNormalizer({"r1": NormalizeResult("r1", "success", document("r1", [RULE]), None)})
    normalized = await pipeline.normalize_stage(plan(), run, [devices[0]], normalizer=normalizer, repository=repo)
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", AsyncMock(return_value={"added": 1}))
    applied = await pipeline.apply_stage(plan(), run, normalized, repository=repo)
    await pipeline.finalize(run, repository=repo)
    assert normalizer.calls == [["r1"]] and len(applied) == 1
    assert run.item_results[1] == devices[1]
    assert run.state == "PARTIAL" and run.device_succeeded == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("secret-value"), TypeError("secret-value"),
                                   RuntimeError("secret-value")])
async def test_apply_exception_is_isolated_and_does_not_expose_text(monkeypatch, caplog, error):
    repo, run = setup(monkeypatch, [device("r1"), device("r2")])
    run.state = "APPLYING"

    async def apply(tenant, data, operator, *, max_items):
        if data["device_key"] == "resource:r1":
            raise error
        return {"added": 1}

    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    result = await pipeline.apply_stage(plan(), run, [
        {**device(rid), "_document": document(rid, [RULE])} for rid in ("r1", "r2")], repository=repo)
    assert [d["resource_id"] for d in result] == ["r2"]
    assert run.item_results[0]["error_code"] == "POLICY_SYNC_FAILED"
    assert run.item_results[0]["error_message"] is None and "secret-value" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "failed"])
async def test_normalization_cas_loser_cannot_finalize_applying_winner(monkeypatch, status):
    repo, run = setup(monkeypatch, [device("r1")])
    run.state = "COLLECTING"
    transition = repo.transition

    async def collect(*args, **kwargs):
        return await _advance(run)

    async def winner_claims(*args, **kwargs):
        # Another worker commits the same source-state transition before this
        # worker's CAS returns False; the persisted state is now APPLYING.
        assert await transition(*args, **kwargs)
        return False

    monkeypatch.setattr(pipeline, "collect_stage", collect)
    monkeypatch.setattr(repo, "transition", winner_claims)
    apply = AsyncMock()
    compare = AsyncMock()
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    monkeypatch.setattr(pipeline, "compare_stage", compare)
    normalizer = FakeNormalizer({"r1": NormalizeResult(
        "r1", status, document("r1", [RULE]) if status == "success" else None,
        None if status == "success" else "POLICY_NORMALIZE_FAILED")})
    await pipeline.run_pipeline(plan(), run, normalizer=normalizer, repository=repo)
    assert run.state == "APPLYING" and run.finished_at is None
    assert repo.plans["p1"].last_run_id is None
    apply.assert_not_awaited()
    compare.assert_not_awaited()
