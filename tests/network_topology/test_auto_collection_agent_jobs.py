from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.network_topology import agent_jobs, contracts
from services.network_topology.collection.errors import CollectionError
from tests.network_topology.test_auto_collection_contracts import RAW


def collection():
    value = deepcopy(contracts.load_schema('aioe.network-topology.collection/v2')['examples'][0])
    resource = value['sources'][0]['resources'][0]
    resource['resource_id'] = 'r1'
    one = deepcopy(RAW)
    one['data']['output'] = {'code': 200, 'data': {'records': [{'data': 'sysname SW1'}]}}
    resource['result']['observations'] = [one]
    return value


def doc():
    return {
        'schema_version': 'aioe.network-topology.llm-fact-candidates/v1',
        'devices': [{'candidate_id': 'r1:raw:v1:dev', 'device_type': 'switch',
                     'identity_aliases': [{'type': 'hostname', 'namespace': 'default', 'value': 'SW1'}],
                     'roles': [], 'inventory_state': 'managed',
                     'evidence_refs': [{'observation_id': 'obs-1', 'locator': {'type': 'json_pointer', 'pointer': '/output/data/records/0/data'}}]}],
        **{family: [] for family in ('device_groups', 'network_contexts', 'interfaces', 'segments', 'adjacencies', 'routes', 'routing_relationships', 'tunnels', 'memberships', 'gateways', 'security_boundaries', 'wan_connections', 'unresolved_references', 'warnings')},
    }


def test_input_groups_multiple_raw_observations_per_resource():
    value = collection()
    observations = value['sources'][0]['resources'][0]['result']['observations']
    second = deepcopy(observations[0]); second['observation_id'] = 'obs-2'
    observations.append(second)
    result = agent_jobs.build_agent_input(value, ['obs-1', 'obs-2'])
    assert len(result) == 1 and len(result[0]['observations']) == 2
    assert result[0]['chunk_id'] == 'r1:raw:v1'


def test_agent_input_keeps_only_parser_context_without_resource_display_metadata():
    value = collection()
    context = value['sources'][0]['resources'][0]['asset_context']
    context.update(name='Resource label', management_ips=['192.0.2.19'],
                   vendor='synthetic-vendor', platform='lab', os_family='lab-os',
                   device_type='firewall')

    resource = agent_jobs.build_agent_input(value, ['obs-1'])[0]

    assert resource['asset_context'] == {
        'vendor': 'synthetic-vendor', 'platform': 'lab',
        'os_family': 'lab-os', 'device_type': 'firewall',
    }
    assert 'Resource label' not in str(resource)
    resource['asset_context']['vendor'] = 'changed'
    assert context['vendor'] == 'synthetic-vendor'


def test_submit_accepts_valid_document():
    results, errors = agent_jobs.convert_documents(collection(), SimpleNamespace(id='run-1'), [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': doc()}])
    assert errors == []
    assert results[0].facts['devices'][0]['id'] == 'device:r1'


def test_collected_resource_identity_does_not_depend_on_agent_inventory_guess():
    from services.network_topology.graph_pipeline import build_graph
    from services.network_topology.identity import IdentityRegistry
    from services.network_topology.versions import validate_publication

    candidate = doc()
    candidate['devices'][0].update(
        inventory_state='unknown', identity_aliases=[], device_type='unknown'
    )
    results, errors = agent_jobs.convert_documents(
        collection(), SimpleNamespace(id='run-1'),
        [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': candidate}],
    )

    assert errors == []
    resolution = IdentityRegistry('tenant-1', 'collection').resolve_facts(results[0].facts)
    assert resolution.fact_identity_map['device:r1'] == 'device:collection:r1'
    assert not resolution.unresolved
    assert candidate['devices'][0]['inventory_state'] == 'unknown'
    profile = deepcopy(contracts.load_schema('aioe.network-topology.profile/v1')['examples'][0])
    graph = build_graph(results[0].facts, resolution, profile, profile_revision=1, collection=collection())
    assert len(graph['entities']) == 1
    validate_publication(results[0].facts, graph, profile, 1, require_non_empty=True)


@pytest.mark.parametrize('mutation', ['foreign', 'pointer', 'chunk', 'resource'])
def test_submit_rejects_invalid_evidence_and_binding(mutation):
    item = {'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': doc()}
    if mutation == 'foreign': item['document']['devices'][0]['evidence_refs'][0]['observation_id'] = 'obs-other'
    if mutation == 'pointer': item['document']['devices'][0]['evidence_refs'][0]['locator']['pointer'] = '/nope'
    if mutation == 'chunk': item['chunk_id'] = 'r1:identity:v1'
    if mutation == 'resource': item['resource_id'] = 'other'
    _, errors = agent_jobs.convert_documents(collection(), SimpleNamespace(id='run-1'), [item])
    assert errors


@pytest.mark.asyncio
async def test_authentication_fails_closed_when_internal_token_is_unset(monkeypatch):
    monkeypatch.setattr(agent_jobs.settings, 'LION_AGENT_INTERNAL_TOKEN', '')
    with pytest.raises(CollectionError) as exc:
        await agent_jobs.authenticate({'X-Internal-Token': 'anything'}, {'job_id': 'j', 'agent_job_token': 'token'})
    assert exc.value.code == 'AGENT_JOB_FORBIDDEN' and exc.value.status == 403


def test_input_includes_all_successful_observations_for_selected_resource():
    value = collection()
    second = deepcopy(RAW); second['observation_id'] = 'obs-extra'
    value['sources'][0]['resources'][0]['result']['observations'].append(second)
    assert len(agent_jobs.build_agent_input(value, ['obs-1'])[0]['observations']) == 2


def test_conversion_rejects_duplicate_documents():
    item = {'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': doc()}
    _, errors = agent_jobs.convert_documents(collection(), SimpleNamespace(id='run-1'), [item, item])
    assert errors


def test_conversion_rejects_wrong_domain_when_raw_chunk_exists():
    value = collection()
    typed = deepcopy(RAW)
    typed['kind'] = 'system.identity'
    typed['observation_id'] = 'typed-1'
    value['sources'][0]['resources'][0]['result']['observations'].append(typed)
    item = {'resource_id': 'r1', 'chunk_id': 'r1:identity:v1', 'document': doc()}
    _, errors = agent_jobs.convert_documents(value, SimpleNamespace(id='run-1'), [item])
    assert errors


@pytest.mark.asyncio
async def test_authentication_rejects_body_tenant_before_query(monkeypatch):
    monkeypatch.setattr(agent_jobs.settings, 'LION_AGENT_INTERNAL_TOKEN', 'secret')
    with pytest.raises(CollectionError) as exc:
        await agent_jobs.authenticate({'X-Internal-Token': 'secret'}, {'job_id': 'j', 'agent_job_token': 'token', 'tenant_id': 'other'})
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_submit_uses_atomic_compare_and_set(monkeypatch):
    from services.network_topology import agent_views
    job = SimpleNamespace(id='j', tenant_id='t', analysis_run_id='a', topology_id='topo', resource_ids=['r1'], job_token_hash='hash')
    async def auth(*args): return job
    async def load(*args): return SimpleNamespace(id='a', agent_observation_ids=['obs-1']), collection()
    calls = []
    class Query:
        async def update(self, **values):
            calls.append(values)
            return 0
    class Model:
        @staticmethod
        def filter(**values):
            assert values['state'] == 'RUNNING' and values['tenant_id'] == 't'
            return Query()
    monkeypatch.setattr(agent_views, 'authenticate', auth)
    monkeypatch.setattr(agent_views, '_load_context', load, raising=False)
    monkeypatch.setattr(agent_views, 'TopologyAgentJob', Model, raising=False)
    request = SimpleNamespace(headers={}, json={'documents': [{'resource_id':'r1','chunk_id':'r1:raw:v1','document':doc()}]})
    response = await agent_views.agent_facts_submit(request)
    assert response.status == 403 and calls[0]['state'] == 'SUBMITTED'


def test_submit_rejects_evidence_from_other_resource():
    from tests.network_topology.auto_collection_fakes import raw_collection
    output = {'code': 200, 'data': {'records': [{'data': 'sysname SW1'}]}}
    value, ids = raw_collection({'r1': [output, output], 'r2': [output]})
    inputs = {r['resource_id']: r for r in agent_jobs.build_agent_input(value, ids)}
    assert len(inputs['r1']['observations']) == 2
    candidate = doc()
    candidate['devices'][0]['evidence_refs'][0]['observation_id'] = inputs['r2']['observations'][0]['observation_id']
    _, errors = agent_jobs.convert_documents(value, SimpleNamespace(id='a', agent_observation_ids=ids), [{'resource_id':'r1','chunk_id':'r1:raw:v1','document':candidate}])
    assert errors and errors[0]['resource_id'] == 'r1'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['valid', 'wrong', 'submitted', 'expired'])
async def test_job_token_and_lifecycle_are_checked(monkeypatch, case):
    import hashlib
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(agent_jobs.settings, 'LION_AGENT_INTERNAL_TOKEN', 'secret')
    job = SimpleNamespace(state='SUBMITTED' if case == 'submitted' else 'RUNNING',
                          job_token_hash=hashlib.sha256(b'job-secret').hexdigest(),
                          started_at=datetime.now(timezone.utc) - timedelta(seconds=1900 if case == 'expired' else 1))
    class Query:
        async def first(self): return job
    class Model:
        @staticmethod
        def filter(**kw):
            assert kw == {'id':'j','deleted':False}
            return Query()
    monkeypatch.setattr(agent_jobs, 'TopologyAgentJob', Model)
    args = ({'X-Internal-Token':'secret'}, {'job_id':'j','agent_job_token':'wrong' if case == 'wrong' else 'job-secret'})
    if case == 'valid':
        assert await agent_jobs.authenticate(*args) is job
    else:
        with pytest.raises(CollectionError):
            await agent_jobs.authenticate(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [False, True])
async def test_submit_acceptance_and_validation_are_all_or_nothing(monkeypatch, invalid):
    import orjson
    from services.network_topology import agent_views
    job = SimpleNamespace(id='j', tenant_id='t', analysis_run_id='a', topology_id='topo', resource_ids=['r1'], job_token_hash='hash')
    async def auth(*args): return job
    async def load(*args): return SimpleNamespace(id='a', agent_observation_ids=['obs-1']), collection()
    writes = []
    class Query:
        async def update(self, **values):
            writes.append(values)
            return 1
    class Model:
        @staticmethod
        def filter(**values): return Query()
    monkeypatch.setattr(agent_views, 'authenticate', auth)
    monkeypatch.setattr(agent_views, '_load_context', load)
    monkeypatch.setattr(agent_views, 'TopologyAgentJob', Model)
    candidate = doc()
    if invalid:
        candidate['devices'][0]['evidence_refs'][0]['locator']['pointer'] = '/nope'
    documents = [{'resource_id':'r1','chunk_id':'r1:raw:v1','document':candidate}]
    response = await agent_views.agent_facts_submit(SimpleNamespace(headers={}, json={'documents':documents}))
    body = orjson.loads(response.body)
    if invalid:
        assert response.status == 422 and writes == []
        assert body['data']['error_code'] == 'AGENT_SUBMISSION_INVALID'
    else:
        assert response.status == 200 and body['data'] == {'accepted': True}
        assert orjson.loads(writes[0]['documents']) == documents


@pytest.mark.asyncio
async def test_input_response_uses_server_analysis_scope(monkeypatch):
    import orjson
    from services.network_topology import agent_views
    from tests.network_topology.auto_collection_fakes import raw_collection
    value, ids = raw_collection({'r1':[{'code':200,'data':{}}], 'r2':[{'code':200,'data':{}}]})
    job = SimpleNamespace(resource_ids=['r1'])
    async def auth(*args): return job
    async def load(*args): return SimpleNamespace(agent_observation_ids=ids), value
    monkeypatch.setattr(agent_views, 'authenticate', auth)
    monkeypatch.setattr(agent_views, '_load_context', load)
    response = await agent_views.agent_job_input(SimpleNamespace(headers={}, json={'agent_observation_ids': ['forged']}))
    data = orjson.loads(response.body)['data']
    assert [r['resource_id'] for r in data['resources']] == ['r1']
    assert data['candidate_schema_id'] in data['schemas']


@pytest.mark.asyncio
async def test_context_uses_analysis_tenant_and_topology_and_immutable_snapshot(monkeypatch):
    from services.network_topology import agent_views
    job = SimpleNamespace(analysis_run_id='a', tenant_id='tenant', topology_id='topo')
    run = SimpleNamespace(id='a')
    class Query:
        async def first(self): return run
    class Model:
        @staticmethod
        def filter(**kw):
            assert kw == dict(id='a', tenant_id='tenant', topology_row_id='topo', deleted=False)
            return Query()
    class Repository:
        async def load_collection(self, row, store):
            assert row is run
            return {'snapshot_id':'immutable'}
    monkeypatch.setattr(agent_views, 'TopologyAnalysisRun', Model)
    monkeypatch.setattr(agent_views, 'OrmAnalysisPipelineRepository', Repository)
    _, result = await agent_views._load_context(job)
    assert result == {'snapshot_id':'immutable'}
