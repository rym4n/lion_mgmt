from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from models._network_topology_v2_base import AnalysisRunStage
from services.network_topology.analysis_pipeline import AnalysisPipeline
from services.network_topology.leases import LeaseIdentity
from services.network_topology.payload_store import PayloadStore
from services.network_topology.worker import PipelineFailure


FIXTURES = Path(__file__).parents[1] / "fixtures" / "network_topology"
VENDORS = ("huawei", "h3c", "cisco", "fortinet", "adc_lb")


def _fixture(vendor="huawei"):
    path = FIXTURES / vendor / "full-golden-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _run(fixture, *, token=7):
    return SimpleNamespace(
        id=fixture["expected_facts"]["analysis_run_id"],
        tenant_id="tenant-1",
        stage=AnalysisRunStage.SPLITTING,
        lease_owner="worker-1",
        lease_generation=3,
        fencing_token=token,
        cancellation_requested=False,
        facts_hash=None,
        fact_graph_payload=None,
        fact_graph_payload_object_row_id=None,
        validation_report=None,
        validation_report_object_row_id=None,
    )


def _lease(run, *, token=None):
    return LeaseIdentity(
        str(run.id), run.lease_owner, run.lease_generation,
        run.fencing_token if token is None else token,
    )


class _Repository:
    def __init__(self, collection):
        self.collection = deepcopy(collection)
        self.guards = []
        self.commits = 0

    async def load_collection(self, run, store):
        del run, store
        return deepcopy(self.collection)

    async def assert_active(self, run, stage, lease):
        self.guards.append(stage)
        if (
            run.cancellation_requested
            or lease.fencing_token != run.fencing_token
            or lease.lease_generation != run.lease_generation
            or str(lease.run_id) != str(run.id)
        ):
            raise PipelineFailure("WORKER_LEASE_LOST")

    async def commit_payloads(self, run, stage, lease, prepared, store):
        del store
        await self.assert_active(run, stage, lease)
        if run.facts_hash is not None:
            if run.facts_hash != prepared.facts.payload_hash:
                raise PipelineFailure("EVIDENCE_INVALID")
            return False
        run.fact_graph_payload = prepared.facts.inline_payload
        run.validation_report = prepared.report.inline_payload
        run.facts_hash = prepared.facts.payload_hash
        self.commits += 1
        return True


def _pipeline(fixture, repository=None):
    repository = repository or _Repository(fixture["collection"])
    store = PayloadStore(inline_threshold=10_000_000)
    return AnalysisPipeline(repository=repository, payload_store=store), repository


def _by_id(values):
    return {item["id"]: item for item in values}


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", VENDORS)
async def test_structured_golden_collection_becomes_formal_facts_only(vendor):
    fixture = _fixture(vendor)
    pipeline, _ = _pipeline(fixture)
    result = await pipeline.analyze_collection(
        fixture["collection"],
        analysis_run_id=fixture["expected_facts"]["analysis_run_id"],
    )

    expected = fixture["expected_facts"]
    assert result.facts["schema_version"] == "aioe.network-topology.facts/v1"
    for family, values in expected.items():
        if isinstance(values, list) and values and "id" in values[0]:
            assert _by_id(result.facts[family]) == _by_id(values)
    assert result.validation_report["valid"] is True
    assert "candidates" not in result.facts
    assert "raw_output" not in json.dumps(result.validation_report)


@pytest.mark.asyncio
async def test_worker_stages_recheck_fence_and_commit_idempotently():
    fixture = _fixture()
    pipeline, repository = _pipeline(fixture)
    run = _run(fixture)
    lease = _lease(run)

    for stage in (
        AnalysisRunStage.SPLITTING,
        AnalysisRunStage.PARSING,
        AnalysisRunStage.VALIDATING_FACTS,
    ):
        run.stage = stage
        result = await pipeline.execute_stage(run, stage, lease)
        assert result.published is False

    assert repository.commits == 1
    assert json.loads(run.fact_graph_payload)["schema_version"].endswith("facts/v1")
    assert "candidate" not in run.fact_graph_payload
    assert repository.guards.count(AnalysisRunStage.VALIDATING_FACTS) >= 3

    await pipeline.execute_stage(
        run, AnalysisRunStage.VALIDATING_FACTS, lease
    )
    assert repository.commits == 1


@pytest.mark.asyncio
async def test_stale_fence_and_cancellation_cannot_write_formal_results():
    fixture = _fixture()
    pipeline, repository = _pipeline(fixture)
    run = _run(fixture)

    with pytest.raises(PipelineFailure, match="WORKER_LEASE_LOST"):
        await pipeline.execute_stage(
            run, AnalysisRunStage.VALIDATING_FACTS, _lease(run, token=6)
        )
    run.cancellation_requested = True
    with pytest.raises(PipelineFailure, match="WORKER_LEASE_LOST"):
        await pipeline.execute_stage(
            run, AnalysisRunStage.VALIDATING_FACTS, _lease(run)
        )
    assert repository.commits == 0
    assert run.fact_graph_payload is None


@pytest.mark.asyncio
async def test_non_empty_collection_parsed_to_empty_fails_closed():
    fixture = _fixture()
    collection = deepcopy(fixture["collection"])
    for source in collection["sources"]:
        for resource in source["resources"]:
            for observation in resource["result"]["observations"]:
                observation["observation_status"] = "failed"
    pipeline, _ = _pipeline(fixture)
    with pytest.raises(PipelineFailure, match="PARSER_FAILED"):
        await pipeline.analyze_collection(
            collection, analysis_run_id="analysis-empty"
        )


@pytest.mark.asyncio
async def test_phase3_pipeline_refuses_phase4_and_publish_stages():
    fixture = _fixture()
    pipeline, _ = _pipeline(fixture)
    run = _run(fixture)
    for stage in (
        AnalysisRunStage.FACTS_READY,
        AnalysisRunStage.RESOLVING_IDENTITIES,
        AnalysisRunStage.PUBLISHING,
    ):
        with pytest.raises(PipelineFailure, match="GRAPH_VALIDATION_FAILED"):
            await pipeline.execute_stage(run, stage, _lease(run))

