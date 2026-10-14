from types import SimpleNamespace

import orjson
import pytest

from models._network_topology_v2_base import AnalysisRunStage
from services.network_topology import analysis_pipeline as module, contracts
from services.network_topology.graph_pipeline import authoritative_inputs, build_graph
from services.network_topology.identity import IdentityRegistry
from services.network_topology.publishing import PublicationResult
from services.network_topology.worker import PipelineFailure
from tests.network_topology.test_phase3_pipeline import (
    VENDORS, _fixture, _pipeline, _Repository, _run, _lease,
)
from tests.network_topology.test_phase4_scope_tree import _profile
from tests.network_topology.test_phase4_publishing import _request


@pytest.mark.asyncio
@pytest.mark.parametrize('vendor', VENDORS)
async def test_golden_facts_project_to_valid_graph(vendor):
    fixture = _fixture(vendor)
    pipeline, _ = _pipeline(fixture)
    merged = await pipeline.analyze_collection(
        fixture['collection'], analysis_run_id=fixture['expected_facts']['analysis_run_id'])
    resolution = IdentityRegistry('tenant-a', 'collection').resolve_facts(merged.facts)
    graph = build_graph(merged.facts, resolution, _profile(), profile_revision=1,
                        collection=fixture['collection'])
    contracts.validate_contract(graph, 'aioe.network-topology.graph/v2')
    assert graph['entities']


def test_authoritative_inputs_maps_asset_scopes():
    collection = {'authoritative_context': {'scopes': [{'code': 'org'}]},
                  'sources': [{'resources': [
                      {'resource_id': 'r1', 'asset_context': {'scope_ids': ['org']}},
                      {'resource_id': 'r2', 'asset_context': {'scope_ids': []}}]}]}
    assert authoritative_inputs(collection) == ([{'code': 'org'}], {'device:r1': ['org']})
    assert authoritative_inputs({}) == ([], {})


class GraphRepository(_Repository):
    async def load_facts(self, run, store):
        return orjson.loads(run.fact_graph_payload)

    async def load_profile(self, run):
        return SimpleNamespace(profile_json=orjson.dumps(_profile()), revision=3)

    async def load_snapshot(self, run):
        return SimpleNamespace(collection_sequence=7)

    async def upgrade_intent_id(self, run):
        return getattr(run, 'upgrade_intent_id', None)

    async def resolve_identities(self, run, stage, lease, facts):
        await self.assert_active(run, stage, lease)
        return IdentityRegistry(run.tenant_id, 'collection').resolve_facts(facts)


async def graph_context():
    fixture = _fixture()
    repository = GraphRepository(fixture['collection'])
    pipeline, _ = _pipeline(fixture, repository)
    run = _run(fixture)
    facts = await pipeline.analyze_collection(fixture['collection'], analysis_run_id=str(run.id))
    run.fact_graph_payload = orjson.dumps(facts.facts)
    run.graph_payload = None
    run.topology_row_id = 'topology-1'
    run.snapshot_row_id = 'snapshot-1'
    run.target_profile_revision_row_id = 'profile-1'
    run.expected_current_graph_revision = 2
    run.expected_publication_revision = 4
    run.submitter_subject = 'user-1'
    return pipeline, run, repository


@pytest.mark.asyncio
@pytest.mark.parametrize('upgrade', [None, 'intent-1'])
async def test_all_graph_stages_publish_with_bound_request(monkeypatch, upgrade):
    pipeline, run, repository = await graph_context()
    run.upgrade_intent_id = upgrade
    requests = []

    async def plain(request):
        assert upgrade is None
        requests.append(request)
        return PublicationResult('SUCCEEDED', True, 'v1', 3, 5)

    async def legacy(request):
        assert upgrade == 'intent-1'
        requests.append(request)
        return PublicationResult('SUCCEEDED', True, 'v1', 3, 5)

    monkeypatch.setattr(module, 'publish_graph', plain)
    monkeypatch.setattr(module, 'publish_upgrade_graph', legacy)
    for stage in (AnalysisRunStage.RESOLVING_IDENTITIES, AnalysisRunStage.BUILDING_SCOPES,
                  AnalysisRunStage.PROJECTING_GRAPH, AnalysisRunStage.VALIDATING_GRAPH,
                  AnalysisRunStage.PUBLISHING):
        run.stage = stage
        result = await pipeline.execute_stage(run, stage, _lease(run))
        for field, value in (result.updates or {}).items():
            setattr(run, field, value)
        assert result.published == (stage == AnalysisRunStage.PUBLISHING)
    contracts.validate_contract(orjson.loads(run.graph_payload), 'aioe.network-topology.graph/v2')
    assert len(requests) == 1
    request = requests[0]
    assert (request.collection_sequence, request.profile_revision) == (7, 3)
    assert (request.expected_graph_revision, request.expected_publication_revision) == (2, 4)
    assert (request.lease_generation, request.fencing_token) == (3, 7)
    assert request.upgrade_intent_id == upgrade
    assert repository.commits == 0


@pytest.mark.asyncio
async def test_superseded_publication_fails_closed(monkeypatch):
    pipeline, run, _ = await graph_context()
    run.stage = AnalysisRunStage.PUBLISHING
    run.graph_payload = orjson.dumps(_request().graph)

    async def publish(request):
        return PublicationResult('SUPERSEDED', False, None, 3, 5)

    monkeypatch.setattr(module, 'publish_graph', publish)
    with pytest.raises(PipelineFailure, match='RUN_SUPERSEDED'):
        await pipeline.execute_stage(run, run.stage, _lease(run))


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', ['null', '{}', '{'])
async def test_invalid_graph_has_safe_failure(payload):
    pipeline, run, _ = await graph_context()
    run.stage = AnalysisRunStage.VALIDATING_GRAPH
    run.graph_payload = payload
    with pytest.raises(PipelineFailure, match='GRAPH_VALIDATION_FAILED'):
        await pipeline.execute_stage(run, run.stage, _lease(run))


@pytest.mark.parametrize('code', ['RUN_SUPERSEDED', 'AGENT_NOT_CONFIGURED'])
def test_new_errors_are_public_and_terminal(code):
    from services.network_topology.analysis_runs import PUBLIC_RUN_ERRORS, classify_error
    assert code in PUBLIC_RUN_ERRORS
    assert classify_error(code).terminal and not classify_error(code).retryable
    schema = contracts.load_schema('aioe.network-topology.api/common/v1')
    assert code in schema['$defs']['errorCode']['enum']


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', [False, True])
async def test_identity_persistence_uses_fenced_transaction(monkeypatch, lost):
    from contextlib import asynccontextmanager
    from models._network_topology_v2_base import AnalysisRunState
    pipeline, run, _ = await graph_context()
    run.state = AnalysisRunState.RUNNING
    run.stage = AnalysisRunStage.RESOLVING_IDENTITIES
    lease = _lease(run)
    if lost:
        run.fencing_token += 1
    connection = object()
    calls = []

    @asynccontextmanager
    async def transaction():
        yield connection

    async def locked(actual_run, actual_connection):
        assert actual_run is run and actual_connection is connection
        return run

    class Persistence:
        def __init__(self, topology_id, analysis_run_id, *, connection, created_by):
            calls.append((topology_id, analysis_run_id, connection, created_by))

        async def load_identities(self, tenant_id):
            assert tenant_id == run.tenant_id
            return ()

        async def save_resolution(self, tenant_id, resolution):
            assert tenant_id == run.tenant_id
            assert resolution.identities

    monkeypatch.setattr(module, 'in_transaction', transaction)
    monkeypatch.setattr(module, '_locked_run', locked)
    monkeypatch.setattr(module, 'OrmIdentityPersistence', Persistence)
    repository = module.OrmAnalysisPipelineRepository()
    facts = orjson.loads(run.fact_graph_payload)
    if lost:
        with pytest.raises(PipelineFailure, match='WORKER_LEASE_LOST'):
            await repository.resolve_identities(run, run.stage, lease, facts)
        assert calls == []
    else:
        result = await repository.resolve_identities(run, run.stage, lease, facts)
        assert result.identities
        assert calls == [(run.topology_row_id, run.id, connection, run.submitter_subject)]


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', [False, True])
async def test_object_facts_require_live_tenant_object_and_verified_hash(monkeypatch, corrupt):
    from services.network_topology.canonicalization import sha256_uri
    from models._network_topology_v2_base import PayloadObjectStatus
    pipeline, run, _ = await graph_context()
    payload = run.fact_graph_payload
    digest = sha256_uri(payload)
    run.fact_graph_payload = None
    run.fact_graph_payload_object_row_id = 'object-1'
    run.facts_hash = digest
    row = SimpleNamespace(object_key='key-1', size_bytes=len(payload),
                          content_hash='sha256:bad' if corrupt else digest)

    class Query:
        async def first(self):
            return row

    class Objects:
        @staticmethod
        def filter(**filters):
            assert filters == dict(id='object-1', tenant_id=run.tenant_id,
                                   deleted=False, status=PayloadObjectStatus.LIVE)
            return Query()

    class Backend:
        async def read(self, key):
            assert key == 'key-1'
            return b'encrypted'

    class Codec:
        def decrypt(self, value):
            assert value == b'encrypted'
            return payload

    monkeypatch.setattr(module, 'TopologyPayloadObject', Objects)
    repository = module.OrmAnalysisPipelineRepository()
    store = SimpleNamespace(backend=Backend(), codec=Codec())
    if corrupt:
        with pytest.raises(PipelineFailure, match='EVIDENCE_INVALID'):
            await repository.load_facts(run, store)
    else:
        assert await repository.load_facts(run, store) == orjson.loads(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_real_worker_crosses_facts_checkpoint_and_finalizes_publication(monkeypatch, enabled):
    import settings
    from datetime import timedelta
    from services.network_topology import worker
    from models._network_topology_v2_base import AnalysisRunState
    from tests.network_topology.test_phase3_worker import (
        MemoryLeaseRepository, _run as worker_run, _topology, NOW,
    )
    pipeline, initial, _ = await graph_context()
    run = worker_run(**vars(initial))
    run.stage = AnalysisRunStage.FACTS_READY
    run.state = AnalysisRunState.RUNNING
    run.lease_expires_at = NOW + timedelta(minutes=2)
    repo = MemoryLeaseRepository([run], topology=_topology(
        current_graph_revision=2, publication_revision=4))
    publication_time = NOW + timedelta(seconds=1)

    async def publish(request):
        # The real publishing adapter commits this terminal state atomically.
        run.state = AnalysisRunState.SUCCEEDED
        run.finished_at = publication_time
        return PublicationResult('SUCCEEDED', True, 'v1', 3, 5)

    monkeypatch.setattr(module, 'publish_graph', publish)
    monkeypatch.setattr(settings, 'TOPOLOGY_V2_PUBLISH_ENABLED', enabled)
    consumer = worker.TopologyAnalysisWorker(
        worker_id=run.lease_owner, pipeline=pipeline, lease_repository=repo,
        clock=lambda: NOW, heartbeat_interval=60,
    )
    await consumer._run_stages(run, _lease(run))
    if enabled:
        assert run.state == AnalysisRunState.SUCCEEDED
        assert run.finished_at == publication_time
        assert run.graph_hash.startswith('sha256:')
    else:
        assert run.state == AnalysisRunState.WAITING_FOR_PUBLISH
        assert run.graph_payload is None
    assert run.lease_owner is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'generation', 'token', 'stage', 'superseded', 'failed', 'cancelled'])
async def test_finalize_committed_publication_rejects_mismatches(change):
    from services.network_topology import leases
    from models._network_topology_v2_base import AnalysisRunState
    from tests.network_topology.test_phase3_worker import MemoryLeaseRepository, _run as worker_run, NOW
    run = worker_run(state=AnalysisRunState.SUCCEEDED, stage=AnalysisRunStage.PUBLISHING,
                     lease_owner='worker', lease_generation=2, fencing_token=7, finished_at=NOW)
    lease = _lease(run)
    if change == 'owner':
        run.lease_owner = 'new-worker'
    elif change == 'generation':
        run.lease_generation += 1
    elif change == 'token':
        run.fencing_token += 1
    elif change == 'stage':
        run.stage = AnalysisRunStage.PROJECTING_GRAPH
    else:
        run.state = AnalysisRunState(change.upper())
    repo = MemoryLeaseRepository([run])
    assert not await leases.mark_succeeded(lease, repository=repo, clock=lambda: NOW)
    assert not repo.saved
    assert run.finished_at == NOW


@pytest.mark.asyncio
async def test_inline_facts_reject_changed_content_with_committed_hash():
    from services.network_topology.versions import facts_hash
    _, run, _ = await graph_context()
    facts = orjson.loads(run.fact_graph_payload)
    run.facts_hash = facts_hash(facts)
    facts['analysis_run_id'] = 'tampered-run'
    run.fact_graph_payload = orjson.dumps(facts).decode()
    with pytest.raises(PipelineFailure, match='EVIDENCE_INVALID'):
        await module.OrmAnalysisPipelineRepository().load_facts(run, None)


@pytest.mark.asyncio
async def test_inline_facts_accept_equivalent_escaped_representation():
    from services.network_topology.canonicalization import sha256_uri
    from services.network_topology.versions import facts_hash
    _, run, _ = await graph_context()
    facts = orjson.loads(run.fact_graph_payload)
    run.facts_hash = facts_hash(facts)
    run.fact_graph_payload = orjson.dumps(facts).decode().replace('analysis', 'analys\\u0069s')
    assert '\\u0069' in run.fact_graph_payload
    assert sha256_uri(run.fact_graph_payload.encode()) != run.facts_hash
    assert await module.OrmAnalysisPipelineRepository().load_facts(run, None) == facts
