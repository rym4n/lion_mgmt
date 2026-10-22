"""Physical and logical views preserve different evidence and presentation units."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.network_topology import agent_jobs
from services.network_topology.contracts import validate_contract
from services.network_topology.graph_pipeline import build_graph
from services.network_topology.query_service import scoped_view, evidence_query
from tests.network_topology.test_logical_topology_presentation import _zone_facts
from tests.network_topology.test_phase4_scope_tree import _resolution, _profile
from tests.network_topology.test_phase5_query_api import (
    _authority, _document, _request, MemoryRepository, MemoryCache,
)


def _view_graph():
    facts = _zone_facts()
    facts['interfaces'][0]['attributes'].update(canonical_name='eth0', raw_name='eth0', media_type='fiber', speed_bps=1000000000)
    facts['devices'][0]['attributes']['location'] = {'building': 'Tower A', 'room': 'DC1', 'rack': 'R12', 'rack_unit': 'U20'}
    graph = build_graph(facts, _resolution('device:fw'), _profile(), profile_revision=1, collection={})
    return facts, graph


@pytest.mark.asyncio
async def test_physical_view_is_flat_and_keeps_device_without_neighbor_evidence():
    _, graph = _view_graph()
    request = {**_request(graph['scopes'][-1]['id']), 'view_type': 'physical', 'layers': []}
    data = await scoped_view(_authority(), request, repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert data['view_type'] == 'physical'
    assert data['current_scope']['id'] == graph['root_scope_id']
    assert [node['entity_kind'] for node in data['nodes']] == ['device']
    assert not data['edges'] and not data['breadcrumbs']
    assert data['evidence_summary']['device_count'] == 1
    assert data['evidence_summary']['devices_without_physical_neighbors'] == 1
    assert 'PHYSICAL_ADJACENCY_MISSING' in {warning['code'] for warning in data['warnings']}
    assert data['nodes'][0]['attributes']['location']['rack'] == 'R12'
    assert data['nodes'][0]['attributes']['interfaces'][0]['name'] == 'eth0'
    validate_contract({'code': 0, 'data': data}, 'aioe.network-topology.api/view-response/v1')


@pytest.mark.asyncio
async def test_logical_default_preserves_configured_subnets_and_hides_physical_and_membership_edges():
    _, graph = _view_graph()
    data = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert data['view_type'] == 'logical'
    assert any(node.get('entity_kind') == 'device' for node in data['nodes'])
    # The Trust child keeps its configured network even with no reachability evidence.
    trust = next(scope['id'] for scope in graph['scopes'] if scope['name'] == 'Trust')
    child = await scoped_view(_authority(), {**_request(trust), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert any(node.get('entity_kind') == 'network_segment' for node in child['nodes'])
    assert not any(edge['relation_type'] in {'physical_link', 'gateway_for', 'security_boundary'} for edge in child['edges'])


@pytest.mark.asyncio
async def test_view_type_is_part_of_cache_identity():
    _, graph = _view_graph()
    repository, cache = MemoryRepository(_document(graph=graph)), MemoryCache()
    payload = {**_request(graph['root_scope_id']), 'layers': []}
    logical = await scoped_view(_authority(), {**payload, 'view_type': 'logical'}, repository=repository, cache=cache)
    physical = await scoped_view(_authority(), {**payload, 'view_type': 'physical'}, repository=repository, cache=cache)
    assert logical['view_type'] != physical['view_type'] and len(cache.values) == 2


def _neighbor_inputs():
    from tests.network_topology.test_logical_topology_presentation import _structured_route_input
    snapshot, document = _structured_route_input()
    document['routes'] = []
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['neighbors'] = [{'protocol': 'lldp', 'local_port': 'eth3', 'remote_port': 'Gi0/1',
                         'remote_system_name': 'switch-b', 'media_type': 'fiber', 'speed_bps': 1000000000}]
    document['adjacencies'] = [{'candidate_id': 'r1:raw:v1:neighbor', 'kind': 'lldp',
        'local_candidate_ref': 'r1:raw:v1:eth3', 'remote_selector': {'hostname': 'switch-b', 'interface': 'Gi0/1'},
        'attachments': ['eth3', 'Gi0/1'], 'layer': 'physical', 'context_ref': None, 'state': 'unknown',
        'local_port': 'eth3', 'remote_port': 'Gi0/1', 'media_type': 'fiber', 'speed_bps': 1000000000,
        'evidence_refs': [{'observation_id': 'obs-1', 'locator': {'type': 'json_pointer', 'pointer': '/output/data/neighbors/0'}}]}]
    return snapshot, document


def _convert(snapshot, document):
    return agent_jobs.convert_documents(snapshot, SimpleNamespace(id='run'),
        [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': document}])


def _built(snapshot, document):
    from services.network_topology.identity import IdentityRegistry
    converted, errors = _convert(snapshot, document)
    assert not errors
    facts = converted[0].facts
    resolution = IdentityRegistry('tenant-1', 'collection').resolve_facts(facts)
    graph = build_graph(facts, resolution, _profile(), profile_revision=1, collection=snapshot)
    validate_contract(graph, 'aioe.network-topology.graph/v2')
    return facts, graph


@pytest.mark.asyncio
async def test_observed_lldp_peer_is_external_and_parallel_physical_ports_survive():
    snapshot, document = _neighbor_inputs()
    second = deepcopy(document['adjacencies'][0])
    second.update(candidate_id='r1:raw:v1:neighbor2', local_port='eth4', remote_port='Gi0/2',
                  local_candidate_ref='r1:raw:v1:eth4', attachments=['eth4', 'Gi0/2'])
    second['remote_selector']['interface'] = 'Gi0/2'
    second['evidence_refs'][0]['locator']['pointer'] = '/output/data/neighbors/1'
    interface = deepcopy(document['interfaces'][0]); interface.update(candidate_id='r1:raw:v1:eth4', raw_name='eth4', canonical_name='eth4')
    interface['evidence_refs'] = deepcopy(second['evidence_refs'])
    document['interfaces'].append(interface); document['adjacencies'].append(second)
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['neighbors'].append({**raw['neighbors'][0], 'local_port': 'eth4', 'remote_port': 'Gi0/2'})
    facts, graph = _built(snapshot, document)
    assert len(facts['devices']) == 1
    physical = graph['physical_topology']
    assert len([node for node in physical['entities'] if node['kind'] == 'device']) == 1
    assert all(node['role'] == 'observed_physical_peer' for node in physical['entities'] if node['kind'] == 'external')
    view = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'view_type': 'physical', 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert len(view['edges']) == 2
    assert {(edge['attributes']['local_port'], edge['attributes']['remote_port']) for edge in view['edges']} == {('eth3', 'Gi0/1'), ('eth4', 'Gi0/2')}
    assert all(edge['attributes']['media_type'] == 'fiber' for edge in view['edges'])


@pytest.mark.parametrize('mutation', ['wrong-port', 'wrong-peer', 'cross-record', 'arp-not-lldp'])
def test_physical_candidate_requires_a_single_observed_neighbor_record(mutation):
    snapshot, document = _neighbor_inputs()
    neighbor = document['adjacencies'][0]
    if mutation == 'wrong-port': neighbor['remote_port'] = neighbor['remote_selector']['interface'] = 'Gi0/99'
    elif mutation == 'wrong-peer': neighbor['remote_selector']['hostname'] = 'unseen-switch'
    elif mutation == 'arp-not-lldp':
        snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']['neighbors'][0]['protocol'] = 'arp'
    else:
        raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
        raw['neighbors'].append({'protocol': 'lldp', 'local_port': 'eth4', 'remote_port': 'Gi0/2', 'remote_system_name': 'switch-c'})
        neighbor['remote_selector']['hostname'] = 'switch-c'
        ref = deepcopy(neighbor['evidence_refs'][0]);ref['locator']['pointer'] = '/output/data/neighbors/1'
        neighbor['evidence_refs'].append(ref)
    _, errors = _convert(snapshot, document)
    assert errors


@pytest.mark.asyncio
async def test_location_and_interface_metadata_keep_provenance_and_safe_drawer_evidence():
    snapshot, document = _neighbor_inputs()
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['inventory'] = {'hostname': 'SW1', 'location': {'building': 'Tower A', 'rack': 'R12'}}
    document['devices'][0]['location'] = {'building': 'Tower A', 'rack': 'R12'}
    document['devices'][0]['evidence_refs'][0]['locator']['pointer'] = '/output/data/inventory'
    raw['interfaces'] = [{'name': 'eth3', 'media_type': 'fiber', 'speed_bps': 1000000000, 'description': 'uplink'}]
    document['interfaces'][0].update(media_type='fiber', speed_bps=1000000000, description='uplink')
    document['interfaces'][0]['evidence_refs'][0]['locator']['pointer'] = '/output/data/interfaces/0'
    facts, graph = _built(snapshot, document)
    interface_id = facts['interfaces'][0]['id']
    response = await evidence_query(_authority(), {'id': 'topology-1', 'target_id': interface_id, 'version_id': None},
        repository=MemoryRepository(_document(graph=graph)))
    assert response['provenance'] and response['provenance'][0]['locator']['pointer'] == '/output/data/interfaces/0'
    document['devices'][0]['location']['rack'] = 'fake-rack'
    _, errors = _convert(snapshot, document)
    assert errors


@pytest.mark.parametrize('kind', ['lldp', 'cdp'])
def test_physical_cli_uses_ancestor_command_and_one_neighbor_detail_record(kind):
    snapshot, document = _neighbor_inputs()
    neighbor = document['adjacencies'][0]
    neighbor.update(kind=kind)
    neighbor.pop('media_type'); neighbor.pop('speed_bps')
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['identity'] = {'hostname': 'SW1'}
    document['devices'][0]['evidence_refs'][0]['locator']['pointer'] = '/output/data/identity'
    if kind == 'lldp':
        text = 'Local Interface: eth3\nSystem Name: switch-b\nPort ID: Gi0/1\n'
    else:
        text = 'Device ID: switch-b\nInterface: eth3, Port ID (outgoing port): Gi0/1\n'
    text += 'Local Interface: eth4\nSystem Name: switch-c\nPort ID: Gi0/2\n'
    raw['records'] = [{'command': 'show ' + kind + ' neighbors detail', 'data': text}]
    neighbor['evidence_refs'][0]['locator']['pointer'] = '/output/data/records/0/data'
    _, errors = _convert(snapshot, document)
    assert not errors
    neighbor['remote_selector']['hostname'] = 'switch-c'
    _, errors = _convert(snapshot, document)
    assert errors


def test_physical_device_inventory_survives_logical_access_collapse():
    facts, _ = _view_graph()
    facts['devices'][0]['attributes']['roles'] = ['access']
    profile = _profile();profile['access_device_policy'] = 'collapse'
    graph = build_graph(facts, _resolution('device:fw'), profile, profile_revision=1, collection={})
    assert any(entity['kind'] == 'device' for entity in graph['physical_topology']['entities'])


@pytest.mark.asyncio
async def test_physical_edge_and_interface_evidence_are_queryable_and_tenant_scoped():
    snapshot, document = _neighbor_inputs()
    _, graph = _built(snapshot, document)
    relation_id = graph['physical_topology']['relations'][0]['id']
    response = await evidence_query(_authority(), {'id': 'topology-1', 'target_id': relation_id, 'version_id': None},
        repository=MemoryRepository(_document(graph=graph)))
    assert response['provenance'][0]['locator']['pointer'] == '/output/data/neighbors/0'


@pytest.mark.asyncio
async def test_configured_vlan_scope_is_visible_without_physical_port_membership():
    facts, _ = _view_graph()
    facts['network_contexts'].append({'id': 'vlan:20', 'fact_family': 'network_context',
        'attributes': {'owner_id': 'device:fw', 'local_id': '20', 'name': 'Servers VLAN20', 'context_type': 'vlan'},
        'provenance_ids': []})
    graph = build_graph(facts, _resolution('device:fw'), _profile(), profile_revision=1, collection={})
    data = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert any(node['label'] == 'Servers VLAN20' for node in data['nodes'])


@pytest.mark.parametrize('mutation,code', [
    ('endpoint', 'RELATION_ENDPOINT_MISSING'),
    ('provenance-member', 'GRAPH_PROVENANCE_REFERENCE_MISSING'),
    ('interface-provenance', 'GRAPH_PROFILE_PROVENANCE_MISMATCH'),
])
def test_physical_presentation_is_validated_before_publication(mutation, code):
    from services.network_topology.versions import PublishingError, validate_publication
    from tests.network_topology.test_phase4_publishing import _profile as _publication_profile
    snapshot, document = _neighbor_inputs()
    facts, graph = _built(snapshot, document)
    physical = graph['physical_topology']
    if mutation == 'endpoint':
        physical['relations'][0]['endpoints'][1]['ref_id'] = 'not-a-real-peer'
    elif mutation == 'provenance-member':
        physical['relations'][0]['provenance'] = [{'type': 'derived', 'rule_id': 'physical.fact_presentation/v1',
            'profile_revision': 1, 'member_ids': ['not-a-real-fact']}]
    else:
        physical['entities'][0]['attributes']['interfaces'][0]['provenance'] = [
            {'type': 'derived', 'rule_id': 'physical.interface/v1', 'profile_revision': 99,
             'member_ids': [facts['interfaces'][0]['id']]}]
    with pytest.raises(PublishingError, match=code):
        validate_publication(facts, graph, {**_publication_profile(), **_profile()}, 1)


@pytest.mark.asyncio
async def test_configured_vlan_exposes_vlan_number_and_segment_context():
    facts, _ = _view_graph()
    facts['network_contexts'].append({'id': 'context:opaque-vlan', 'fact_family': 'network_context',
        'attributes': {'owner_id': 'device:fw', 'local_id': '20', 'name': 'Servers', 'context_type': 'vlan'},
        'provenance_ids': []})
    facts['segments'][0]['attributes'].update(vlan_id=20, vni=10020, context_id='context:opaque-vlan')
    graph = build_graph(facts, _resolution('device:fw'), _profile(), profile_revision=1, collection={})
    data = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    vlan = next(node for node in data['nodes'] if node.get('projection_type') == 'scope_ref'
                and node.get('attributes', {}).get('vlan_id') == 20)
    assert '20' in vlan['label'] and vlan['attributes']['network_context_type'] == 'vlan'
    child = await scoped_view(_authority(), {**_request(vlan['target_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    segment = next(node for node in child['nodes'] if node.get('entity_kind') == 'network_segment')
    assert segment['attributes']['vlan_id'] == 20 and segment['attributes']['vni'] == 10020


def test_context_vlan_and_vni_are_preserved_only_with_bound_evidence():
    snapshot, document = _neighbor_inputs()
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['vlans'] = [{'vlan': 20, 'vni': 10020, 'name': 'Servers'}]
    document['network_contexts'] = [{'candidate_id': 'r1:raw:v1:vlan20', 'kind': 'vlan',
        'owning_candidate_ref': document['devices'][0]['candidate_id'], 'local_id': '20', 'name': 'Servers',
        'namespace_attributes': {'vlan': 20, 'vni': 10020},
        'evidence_refs': [{'observation_id': 'obs-1', 'locator': {'type': 'json_pointer', 'pointer': '/output/data/vlans/0'}}]}]
    facts, graph = _built(snapshot, document)
    context = facts['network_contexts'][0]['attributes']
    assert context['vlan_id'] == 20 and context['vni'] == 10020
    assert next(scope for scope in graph['scopes'] if '20' in scope['name'])['attributes']['vni'] == 10020
    document['network_contexts'][0]['namespace_attributes']['vni'] = 10099
    _, errors = _convert(snapshot, document)
    assert errors


def test_unitless_vendor_speed_is_not_interpreted_as_bits_per_second():
    snapshot, document = _neighbor_inputs()
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['interfaces'] = [{'name': 'eth3', 'speedDuplex': {'speed': 1000}}]
    document['interfaces'][0]['speed_bps'] = 1000
    document['interfaces'][0]['evidence_refs'][0]['locator']['pointer'] = '/output/data/interfaces/0'
    _, errors = _convert(snapshot, document)
    assert errors


@pytest.mark.parametrize('mutation', ['sibling-port', 'mtu-is-not-speed'])
def test_interface_metadata_cannot_borrow_from_another_port_or_field(mutation):
    snapshot, document = _neighbor_inputs()
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['interfaces'] = [{'name': 'eth3', 'interface_type': 'physical', 'speed_bps': 1000000000, 'mtu': 1500},
                         {'name': 'eth4', 'media_type': 'fiber', 'speed_bps': 10000000000}]
    document['interfaces'][0]['evidence_refs'][0]['locator']['pointer'] = '/output/data/interfaces'
    if mutation == 'sibling-port':
        document['interfaces'][0].update(interface_type='physical', media_type='fiber', speed_bps=10000000000)
    else:
        document['interfaces'][0].update(speed_bps=1500)
    _, errors = _convert(snapshot, document)
    assert errors


@pytest.mark.parametrize('actual_port', ['Ethernet1/1/2', 'GE0/0/1/2'])
def test_physical_cli_port_tokens_must_match_complete_interface_name(actual_port):
    from services.network_topology.facts.physical_evidence import supports_physical_adjacency
    snapshot, document = _neighbor_inputs()
    neighbor = document['adjacencies'][0]
    neighbor.pop('media_type');neighbor.pop('speed_bps')
    neighbor['local_port'] = actual_port.rsplit('/', 1)[0]
    document['interfaces'][0].update(raw_name=neighbor['local_port'], canonical_name=neighbor['local_port'])
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['records'] = [{'command': 'show lldp neighbors',
                       'data': actual_port + ' switch-b Gi0/1'}]
    neighbor['evidence_refs'][0]['locator']['pointer'] = '/output/data/records/0/data'
    observations = snapshot['sources'][0]['resources'][0]['result']['observations']
    assert not supports_physical_adjacency(neighbor, document, observations)


@pytest.mark.asyncio
async def test_reciprocal_discovery_merges_one_cable_and_preserves_parallel_cables():
    facts, _ = _view_graph()
    other = deepcopy(facts['devices'][0]);other['id'] = 'device:switch';other['attributes']['device_type'] = 'switch'
    facts['devices'].append(other)
    first_interface = facts['interfaces'][0]
    second_interface = deepcopy(first_interface);second_interface['id'] = 'interface:peer'
    second_interface['attributes'].update(device_id='device:switch', raw_name='Gi0/1', canonical_name='Gi0/1')
    facts['interfaces'].append(second_interface)
    facts['adjacencies'] = [
        {'id': 'neighbor:a', 'fact_family': 'neighbor', 'attributes': {'neighbor_type': 'lldp',
         'layer': 'physical', 'local_id': first_interface['id'], 'remote_id': 'device:switch',
         'local_port': 'eth0', 'remote_port': 'Gi0/1'}, 'provenance_ids': []},
        {'id': 'neighbor:b', 'fact_family': 'neighbor', 'attributes': {'neighbor_type': 'cdp',
         'layer': 'physical', 'local_id': second_interface['id'], 'remote_id': 'device:fw',
         'local_port': 'GigabitEthernet0/1', 'remote_port': 'Ethernet0'}, 'provenance_ids': []},
    ]
    graph = build_graph(facts, _resolution('device:fw', 'device:switch'), _profile(), profile_revision=1, collection={})
    links = graph['physical_topology']['relations']
    assert len(links) == 1 and set(links[0]['member_fact_ids']) == {'neighbor:a', 'neighbor:b'}
    assert len(links[0]['provenance']) == 2
    assert next(node for node in graph['physical_topology']['entities'] if node['id'] == 'canonical:device:switch')['role'] == 'switch'
    extra = deepcopy(facts['adjacencies'][0]);extra['id'] = 'neighbor:c'
    extra['attributes'].update(local_port='eth1', remote_port='Gi0/2')
    facts['adjacencies'].append(extra)
    graph = build_graph(facts, _resolution('device:fw', 'device:switch'), _profile(), profile_revision=1, collection={})
    view = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'view_type': 'physical', 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert len(view['edges']) == 2


def test_physical_metadata_cannot_borrow_from_another_neighbor_record():
    snapshot, document = _neighbor_inputs()
    rows = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']['neighbors']
    rows[0].update(media_type='copper', speed_bps=1000000000)
    rows.append({'protocol': 'lldp', 'local_port': 'eth4', 'remote_port': 'Gi0/2',
                 'remote_system_name': 'switch-c', 'media_type': 'fiber', 'speed_bps': 10000000000})
    neighbor = document['adjacencies'][0]
    neighbor.update(media_type='fiber', speed_bps=10000000000)
    neighbor['evidence_refs'][0]['locator']['pointer'] = '/output/data/neighbors'
    _, errors = _convert(snapshot, document)
    assert errors


def test_interface_cli_metadata_is_bound_to_its_table_row():
    snapshot, document = _neighbor_inputs()
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['ports'] = 'Interface Type Media Speed\neth3 physical copper 1G\neth4 physical fiber 10G'
    interface = document['interfaces'][0]
    interface.update(interface_type='physical', media_type='fiber', speed_bps=10000000000)
    interface['evidence_refs'][0]['locator']['pointer'] = '/output/data/ports'
    _, errors = _convert(snapshot, document)
    assert errors


def test_location_cannot_join_building_and_rack_from_different_records():
    snapshot, document = _neighbor_inputs()
    raw = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    raw['locations'] = [{'building': 'A', 'rack': 'R1'}, {'building': 'B', 'rack': 'R2'}]
    document['devices'][0]['location'] = {'building': 'A', 'rack': 'R2'}
    document['devices'][0]['evidence_refs'][0]['locator']['pointer'] = '/output/data'
    _, errors = _convert(snapshot, document)
    assert errors


@pytest.mark.parametrize('state', ['up', 'down', 'unknown'])
def test_physical_state_uses_explicit_reported_neighbor_state(state):
    snapshot, document = _neighbor_inputs()
    row = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']['neighbors'][0]
    row['state'] = state;document['adjacencies'][0]['state'] = state
    _, graph = _built(snapshot, document)
    assert graph['physical_topology']['relations'][0]['operational_state'] == state
    if state != 'unknown':
        del row['state']
        _, errors = _convert(snapshot, document)
        assert errors


def test_bidirectional_routing_asns_follow_projected_endpoint_order():
    from services.network_topology.projection import _aggregate_edges
    relation = {'id': 'bgp', 'layer': 'routing', 'relation_type': 'routing_adjacency',
        'direction': 'bidirectional', 'attributes': {'protocol': 'bgp', 'local_asn': 65002, 'remote_asn': 65001}}
    edges = _aggregate_edges(((relation, ('b', 'a')),), {'a': 'a', 'b': 'b'})
    assert [item['ref_id'] for item in edges[0]['endpoints']] == ['a', 'b']
    assert edges[0]['attributes']['local_asn'] == 65001 and edges[0]['attributes']['remote_asn'] == 65002


@pytest.mark.parametrize('field,value,record', [
    ('vlan_ids', [1500], {'name': 'eth3', 'mtu': 1500}),
    ('mtu', 1000, {'name': 'eth3', 'speed_bps': 1000}),
    ('media_type', 'fiber', {'name': 'eth3', 'description': 'fiber'}),
])
def test_interface_numeric_metadata_requires_its_own_field(field, value, record):
    from services.network_topology.facts.presentation_evidence import supports_metadata
    assert not supports_metadata('interfaces', {'raw_name': 'eth3', field: value}, [record])


def test_physical_brief_ports_cannot_reverse_the_observed_tuple():
    from services.network_topology.facts.physical_evidence import _text
    assert not _text('Ethernet1/1 switch-b GE0/0/1 120', 'GE0/0/1', 'Ethernet1/1', {'hostname': 'switch-b'})


@pytest.mark.parametrize('source,target,accepted', [
    ('PHYSICALIF', 'physical', True), ('CHANNELIF', 'aggregate', True),
    ('CHANNELIF', 'lag', True), ('AGGRIF', 'aggregation', True),
    ('VLANIF', 'vlan', True), ('VLANIF', 'logical', True),
    ('VPNTUN', 'tunnel', True), ('CHANNELIF', 'physical', False),
    ('VLANIF', 'aggregate', False), ('OTHERIF', 'logical', False),
])
def test_interface_type_uses_only_explicit_vendor_aliases(source, target, accepted):
    from services.network_topology.facts.presentation_evidence import supports_metadata
    assert supports_metadata('interfaces', {'raw_name': 'eth3', 'interface_type': target},
                             [{'name': 'eth3', 'ifType': source}]) is accepted
