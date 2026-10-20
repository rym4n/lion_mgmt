import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.action_collection.errors import CollectionError
from services.action_collection.execute import ItemOutcome, execute_targets, text_time
from services.action_collection.executor import ExecResult
from services.action_collection.outputs import parse_action_output
from services.action_collection.targets import Target


def target(resource_id, item_id="i"):
    return Target(item_id, "huawei_fw", "v", "main.py", "collect_policy_observations", "采集策略",
                  {}, resource_id, resource_id, "10.0.0.1", None)


def test_topology_shares_core_types():
    from services.network_topology.collection.errors import CollectionError as TopologyError
    from services.network_topology.collection.snapshot_builder import ItemOutcome as TopologyOutcome
    from services.network_topology.collection.dispatch import ExecResult as TopologyResult

    assert TopologyError is CollectionError
    assert TopologyOutcome is ItemOutcome
    assert TopologyResult is ExecResult


@pytest.mark.parametrize("value,code", [
    ("not json", "COLLECTION_OUTPUT_NOT_JSON"),
    ('{"x": "...(已截断，原始 100 字节"}', "COLLECTION_OUTPUT_TRUNCATED"),
    ("", "COLLECTION_OUTPUT_NOT_JSON"),
    ('"' + 'a' * (4 * 1024 * 1024) + '"', "COLLECTION_OUTPUT_TOO_LARGE"),
])
def test_parse_action_output_failures(value, code):
    with pytest.raises(CollectionError) as exc:
        parse_action_output(value)
    assert exc.value.code == code


def test_parse_action_output_scrubs_credentials():
    assert parse_action_output('{"code":200,"password":"p","data":{"x":1}}') == {"code": 200, "data": {"x": 1}}


@pytest.mark.asyncio
async def test_execute_targets_concurrency_order_failures_and_run_ids():
    active = peak = 0

    async def executor(tenant_id, item, *, timeout, executor_id):
        nonlocal active, peak
        assert tenant_id == "t1" and timeout == 30 and executor_id == "acct"
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if item.resource_id == "bad":
            return ExecResult(-1, '{"code":500,"msg":"login failed"}', 5, "run-bad")
        if item.resource_id == "err":
            return ExecResult(0, '{"code":500,"msg":"denied"}', 5, "run-err")
        return ExecResult(0, '{"code":200,"data":{"outputs":[]}}', 5, f"run-{item.resource_id}")

    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    outcomes = await execute_targets("t1", [target("r1"), target("bad"), target("r2"), target("err")],
                                     concurrency=2, per_item_timeout=30, executor_id="acct",
                                     executor=executor, now=now)
    assert 1 < peak <= 2
    assert [o.resource_id for o in outcomes] == ["r1", "bad", "r2", "err"]
    assert [(o.status, o.error_code) for o in outcomes] == [
        ("success", None), ("failed", "COLLECTION_ACTION_FAILED"),
        ("success", None), ("failed", "COLLECTION_ACTION_FAILED")]
    assert [o.action_run_id for o in outcomes] == ["run-r1", "run-bad", "run-r2", "run-err"]
    assert outcomes[0].output == {"code": 200, "data": {"outputs": []}}
    assert outcomes[0].observed_at == "2026-10-20T00:00:00Z"
    assert outcomes[1].output is None


@pytest.mark.asyncio
async def test_execute_targets_timeout_and_unexpected_exception():
    async def executor(tenant_id, item, *, timeout, executor_id):
        if item.resource_id == "slow":
            await asyncio.sleep(5)
        raise RuntimeError("password=leaked")

    outcomes = await execute_targets("t1", [target("slow"), target("boom")], concurrency=2,
                                     per_item_timeout=0.05, executor_id="", executor=executor)
    assert [o.error_code for o in outcomes] == ["COLLECTION_ITEM_TIMEOUT", "COLLECTION_ACTION_FAILED"]
    assert "leaked" not in str(outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize("result_text", ["x" * (4 * 1024 * 1024 + 1), "é" * (2 * 1024 * 1024 + 1)])
async def test_oversized_failed_result_skips_decode_and_redaction(monkeypatch, result_text):
    from services.action_collection import execute as execute_module

    calls = []

    def decode(value):
        calls.append("decode")
        return {"msg": "oversized diagnostic"}

    def scrub(value):
        calls.append("scrub")
        return value

    monkeypatch.setattr(execute_module.orjson, "loads", decode)
    monkeypatch.setattr(execute_module, "scrub_action_output", scrub)

    async def executor(*args, **kwargs):
        return ExecResult(-1, result_text, 7, "action-run")

    outcomes = await execute_targets("t1", [target("r1")], concurrency=1, per_item_timeout=30,
                                     executor_id="acct", executor=executor)

    assert calls == []
    assert outcomes[0].status == "failed"
    assert outcomes[0].error_code == outcomes[0].error_message == "COLLECTION_ACTION_FAILED"
    assert outcomes[0].output is None
    assert outcomes[0].action_run_id == "action-run"


def test_text_time_treats_naive_as_utc():
    assert text_time(datetime(2026, 10, 20, 1, 2, 3)) == "2026-10-20T01:02:03Z"


@pytest.mark.asyncio
async def test_kafka_executor_returns_generated_action_run_id(monkeypatch):
    from services.action_collection import executor as executor_module

    published = {}

    async def produce(topic, payload, *, raise_on_error):
        published.update(topic=topic, payload=payload, raise_on_error=raise_on_error)

    class Query:
        def order_by(self, field):
            assert field == "-utime"
            return self

        async def first(self):
            return SimpleNamespace(status=0, result='{"code":200}')

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {"run_id": "generated-run", "tenant_id": "tenant", "deleted": False}
            return Query()

    monkeypatch.setattr(executor_module, "gen_run_id", lambda: "generated-run")
    monkeypatch.setattr(executor_module, "aproduce", produce)
    monkeypatch.setattr(executor_module, "Runlog", FakeRunlog)

    result = await executor_module.KafkaRunlogExecutor()("tenant", target("r1"), timeout=1, executor_id="acct")

    assert published["payload"]["run_id"] == "generated-run"
    assert result.action_run_id == "generated-run"


@pytest.mark.asyncio
async def test_topology_dispatch_filters_action_run_id_from_item_results():
    from services.network_topology.collection.dispatch import run_dispatch

    async def executor(*args, **kwargs):
        return ExecResult(0, '{"code":200}', 1, "action-run")

    run = SimpleNamespace(triggered_by=None, state="RUNNING", item_results=[], device_succeeded=0)
    outcomes = await run_dispatch(SimpleNamespace(tenant_id="tenant", concurrency=1), run, [target("r1")],
                                  executor=executor, persist=False)

    assert outcomes[0].action_run_id == "action-run"
    assert "output" not in run.item_results[0]
    assert "action_run_id" not in run.item_results[0]
