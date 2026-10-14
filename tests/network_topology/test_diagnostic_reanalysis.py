"""Original snapshot diagnostic reruns must finish without graph publication."""
from copy import deepcopy
from types import SimpleNamespace

import orjson
import pytest

import settings
from models._network_topology_v2_base import AnalysisRunStage, AnalysisRunState
from services.network_topology import analysis_pipeline, contracts, leases, worker
from tests.network_topology.test_auto_collection_graph_pipeline import GraphRepository
from tests.network_topology.test_phase3_pipeline import _fixture, _pipeline
from tests.network_topology.test_phase3_worker import (
    MemoryAlertRepository, MemoryLeaseRepository, NOW, _run, _snapshot, _topology,
)


class DiagnosticLeaseRepository(MemoryLeaseRepository):
    def __init__(self, run, *, profile_revision=3, newer_collection=False):
        topology = _topology(
            topology_profile_revision=profile_revision,
            last_published_collection_sequence=13 if newer_collection else 12,
            current_snapshot_row_id="snapshot-1", current_analysis_run_row_id="published-run",
        )
        super().__init__([run], topology=topology)
        self.profile_revision = profile_revision

    async def get_guard_context(self, run, *, connection):
        return leases.GuardContext(self.topology, self.snapshots[run.snapshot_row_id],
                                   SimpleNamespace(revision=self.profile_revision), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_revision,newer_collection,publish_enabled", [(3, False, True), (4, False, True), (4, True, False)])
async def test_published_snapshot_diagnostic_completes_with_current_profile_without_publication(
    monkeypatch, profile_revision, newer_collection, publish_enabled,
):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(settings, "TOPOLOGY_V2_PUBLISH_ENABLED", publish_enabled)
    fixture = _fixture()
    run = _run(analysis_kind="REANALYZE", publish_as_current=False, submitter_subject="admin",
               target_profile_revision_row_id=f"profile-{profile_revision}",
               fact_graph_payload=None, facts_hash=None, graph_payload=None, graph_hash=None)
    repo = DiagnosticLeaseRepository(run, profile_revision=profile_revision, newer_collection=newer_collection)
    before = deepcopy(vars(repo.topology))

    class Repository(GraphRepository):
        async def load_profile(self, run):
            result = await super().load_profile(run)
            result.revision = profile_revision
            return result

    pipeline_repo = Repository(fixture["collection"])
    pipeline, _ = _pipeline(fixture, pipeline_repo)
    async def forbidden(*args, **kwargs):
        pytest.fail("diagnostic rerun attempted graph publication")
    monkeypatch.setattr(analysis_pipeline, "_publish", forbidden)
    service = worker.TopologyAnalysisWorker(
        worker_id="diagnostics", pipeline=pipeline, lease_repository=repo,
        alert_repository=MemoryAlertRepository(), clock=lambda: NOW,
    )
    assert await service.run_once()
    assert run.state == AnalysisRunState.SUCCEEDED
    assert run.stage == AnalysisRunStage.VALIDATING_GRAPH
    assert run.finished_at == NOW and run.lease_owner is None
    assert run.error_code is None
    contracts.validate_contract(orjson.loads(run.graph_payload), "aioe.network-topology.graph/v2")
    def profile_revisions(value):
        if isinstance(value, dict):
            return ([value["profile_revision"]] if "profile_revision" in value else []) + [
                revision for child in value.values() for revision in profile_revisions(child)]
        if isinstance(value, list):
            return [revision for child in value for revision in profile_revisions(child)]
        return []
    revisions = profile_revisions(orjson.loads(run.graph_payload))
    assert revisions and set(revisions) == {profile_revision}
    assert pipeline_repo.commits == 1 and run.facts_hash and run.graph_hash
    assert vars(repo.topology) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("run_overrides,allowed", [
    ({}, True), ({"analysis_kind": "SNAPSHOT"}, False), ({"publish_as_current": True}, False),
])
async def test_diagnostic_guard_exception_is_narrow(run_overrides, allowed):
    run = _run(analysis_kind="REANALYZE", publish_as_current=False)
    vars(run).update(run_overrides)
    repo = DiagnosticLeaseRepository(run, profile_revision=4, newer_collection=True)
    claimed = await leases.claim_due_run("diagnostics", repository=repo, clock=lambda: NOW)
    assert (claimed is not None) is allowed
    if not allowed:
        assert run.state == AnalysisRunState.SUPERSEDED


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"cancellation_requested": True}, {"fencing_token": 99},
                                         {"stage": AnalysisRunStage.PROJECTING_GRAPH}, {"stage": AnalysisRunStage.PUBLISHING},
                                         {"publish_as_current": True}])
async def test_diagnostic_success_requires_active_fence_and_validated_stage(overrides):
    run = _run(analysis_kind="REANALYZE", publish_as_current=False, state=AnalysisRunState.RUNNING,
               stage=AnalysisRunStage.VALIDATING_GRAPH, lease_owner="diagnostics",
               lease_generation=1, fencing_token=10)
    vars(run).update(overrides)
    repo = DiagnosticLeaseRepository(run)
    identity = leases.LeaseIdentity(run.id, "diagnostics", 1, 10)
    if "stage" in overrides or "publish_as_current" in overrides:
        with pytest.raises(leases.InvalidTransition):
            await leases.mark_succeeded(identity, repository=repo, clock=lambda: NOW)
    else:
        assert not await leases.mark_succeeded(identity, repository=repo, clock=lambda: NOW)
    assert run.state != AnalysisRunState.SUCCEEDED


@pytest.mark.asyncio
async def test_invalid_diagnostic_graph_cannot_be_success(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(settings, "TOPOLOGY_V2_PUBLISH_ENABLED", False)
    run = _run(analysis_kind="REANALYZE", publish_as_current=False,
               stage=AnalysisRunStage.VALIDATING_GRAPH, graph_payload="{}", fact_graph_payload="{}")
    fixture = _fixture()
    pipeline, _ = _pipeline(fixture, GraphRepository(fixture["collection"]))
    repo = DiagnosticLeaseRepository(run)
    service = worker.TopologyAnalysisWorker(
        worker_id="diagnostics", pipeline=pipeline, lease_repository=repo,
        alert_repository=MemoryAlertRepository(), clock=lambda: NOW,
    )
    assert await service.run_once()
    assert run.state == AnalysisRunState.FAILED and run.error_code == "GRAPH_VALIDATION_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["topology", "snapshot", "profile", "snapshot_topology"])
async def test_diagnostic_claim_keeps_existence_and_snapshot_binding_guards(invalid):
    run = _run(analysis_kind="REANALYZE", publish_as_current=False)
    repo = DiagnosticLeaseRepository(run)
    context = await repo.get_guard_context(run, connection=None)
    if invalid == "snapshot_topology":
        context.snapshot.topology_row_id = "other-topology"
    else:
        context = leases.GuardContext(**{
            key: None if key == invalid else getattr(context, key)
            for key in ("topology", "snapshot", "profile", "current_snapshot")
        })
    async def guard(run, *, connection):
        return context
    repo.get_guard_context = guard
    assert await leases.claim_due_run("diagnostics", repository=repo, clock=lambda: NOW) is None
    assert run.state == AnalysisRunState.SUPERSEDED


@pytest.mark.asyncio
async def test_cancelled_diagnostic_is_not_claimed():
    run = _run(analysis_kind="REANALYZE", publish_as_current=False, cancellation_requested=True)
    repo = DiagnosticLeaseRepository(run, profile_revision=4)
    assert await leases.claim_due_run("diagnostics", repository=repo, clock=lambda: NOW) is None
    assert run.state == AnalysisRunState.CANCELLED
