"""Logical communication views expose device ownership without device vertices."""
from copy import deepcopy

import pytest

from services.network_topology.query_service import scoped_view
from services.network_topology.contracts import validate_contract
from services.network_topology.graph_pipeline import build_graph
from tests.network_topology.test_logical_topology_presentation import _zone_facts
from tests.network_topology.test_phase4_scope_tree import _resolution, _profile
from tests.network_topology.test_phase5_query_api import _authority, _request, _document, MemoryRepository, MemoryCache


def _graph():
    facts = _zone_facts()
    facts['interfaces'][0]['attributes']['canonical_name'] = 'eth0'
    facts['interfaces'][1]['attributes']['canonical_name'] = 'eth1'
    facts['routes'] = [{'id': 'route:a', 'attributes': {
        'route_type': 'default', 'prefix': '0.0.0.0/0', 'vrf_id': 'business',
        'next_hop_address': '10.1.0.254', 'interface_id': 'interface:a', 'state': 'unknown'},
        'provenance_ids': []}]
    return build_graph(facts, _resolution('device:fw'), _profile(), profile_revision=1, collection={})


@pytest.mark.asyncio
async def test_logical_route_joins_egress_subnet_and_next_hop_with_device_as_attribute():
    graph = _graph(); original = deepcopy(graph)
    trust = next(scope['id'] for scope in graph['scopes'] if scope['name'] == 'Trust')
    data = await scoped_view(_authority(), {**_request(trust), 'layers': [], 'view_type': 'logical'},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert not any(n.get('entity_kind') == 'device' for n in data['nodes'])
    assert {n['label'] for n in data['nodes']} == {'10.1.0.0/24', '10.1.0.254'}
    assert len(data['edges']) == 1
    labels = {n['id']: n['label'] for n in data['nodes']}
    edge = data['edges'][0]
    assert (labels[edge['source']], labels[edge['target']]) == ('10.1.0.0/24', '10.1.0.254')
    assert edge['attributes']['route_prefixes'] == ['0.0.0.0/0']
    for node in data['nodes']:
        assert len(node['attributes']['related_devices']) == 1
        assert node['attributes']['related_devices'][0]['name']
    assert graph == original
    validate_contract({'code': 0, 'data': data}, 'aioe.network-topology.api/view-response/v1')


@pytest.mark.asyncio
async def test_root_logical_view_connects_zones_when_a_configured_route_matches_one_target_segment():
    facts = _zone_facts()
    facts['interfaces'][1]['attributes']['canonical_name'] = 'eth1'
    facts['segments'].append({
        'id': 'network:trust-destination', 'fact_family': 'network',
        'attributes': {
            'canonical_cidr': '2001:db8::/64', 'network_domain_id': 'lab',
            'device_id': 'device:fw', 'context_id': 'context:trust',
        }, 'provenance_ids': [],
    })
    facts['routes'] = [{
        'id': 'route:wan-to-trust', 'attributes': {
            'route_type': 'static', 'prefix': '2001:db8::/64', 'vrf_id': 'business',
            'next_hop_address': '2000::2', 'interface_id': 'interface:b', 'state': 'unknown',
        }, 'provenance_ids': [],
    }]
    graph = build_graph(facts, _resolution('device:fw'), _profile(), profile_revision=1, collection={})
    root = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())

    assert not any(node.get('entity_kind') == 'device' for node in root['nodes'])
    zones = {node['label']: node['id'] for node in root['nodes'] if node['projection_type'] == 'scope_ref'}
    edge, = root['edges']
    assert {edge['source'], edge['target']} == {zones['Trust'], zones['WAN']}
    assert edge['relation_type'] == 'configured_route'
    assert edge['direction'] == 'unidirectional'
    assert edge['attributes']['route_prefixes'] == ['2001:db8::/64']
    assert edge['attributes']['next_hop_ip'] == '2000::2'


@pytest.mark.asyncio
async def test_root_logical_view_keeps_default_route_as_zone_to_next_hop_path():
    graph = _graph()
    root = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())

    assert not any(node.get('entity_kind') == 'device' for node in root['nodes'])
    trust = next(node for node in root['nodes'] if node['projection_type'] == 'scope_ref'
        and node['label'] == 'Trust')
    edge, = root['edges']
    next_hop = next(node for node in root['nodes'] if node['id'] == edge['target'])
    assert edge['source'] == trust['id']
    assert next_hop.get('entity_kind') == 'external'
    assert next_hop['label'] == '10.1.0.254'
    assert edge['attributes']['route_prefixes'] == ['0.0.0.0/0']


@pytest.mark.asyncio
async def test_ambiguous_route_prefix_does_not_connect_multiple_security_zones():
    facts = _zone_facts()
    facts['interfaces'][1]['attributes']['canonical_name'] = 'eth1'
    facts['segments'].extend([
        {'id': 'network:trust-target', 'fact_family': 'network', 'attributes': {
            'canonical_cidr': '2001:db8::/64', 'network_domain_id': 'lab',
            'device_id': 'device:fw', 'context_id': 'context:trust'}, 'provenance_ids': []},
        {'id': 'network:wan-target', 'fact_family': 'network', 'attributes': {
            'canonical_cidr': '2001:db8::/64', 'network_domain_id': 'lab',
            'device_id': 'device:fw', 'context_id': 'context:wan'}, 'provenance_ids': []},
    ])
    facts['routes'] = [{'id': 'route:ambiguous', 'attributes': {
        'route_type': 'static', 'prefix': '2001:db8::/64', 'vrf_id': 'business',
        'next_hop_address': '2000::2', 'interface_id': 'interface:b', 'state': 'unknown'},
        'provenance_ids': []}]
    graph = build_graph(facts, _resolution('device:fw'), _profile(), profile_revision=1, collection={})
    root = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())

    zones = {node['label']: node['id'] for node in root['nodes'] if node['projection_type'] == 'scope_ref'}
    edge, = root['edges']
    assert edge['source'] == zones['WAN']
    assert edge['target'] not in set(zones.values())
    target = next(node for node in root['nodes'] if node['id'] == edge['target'])
    assert target['label'] == '2001:db8::/64'
    assert target['role'] == 'configured_route_destination'


@pytest.mark.asyncio
async def test_scope_carries_device_properties_and_only_real_logical_content_can_drill():
    graph = _graph()
    data = await scoped_view(_authority(), {**_request(graph['root_scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert not any(n.get('entity_kind') == 'device' for n in data['nodes'])
    scopes = [n for n in data['nodes'] if n['projection_type'] == 'scope_ref']
    assert {n['label'] for n in scopes} == {'Trust', 'WAN'}
    assert all(n['can_drill'] for n in scopes)
    assert all(n['attributes']['related_devices'] for n in scopes)


@pytest.mark.asyncio
async def test_missing_or_ambiguous_egress_subnet_does_not_create_guessed_connectivity():
    graph = _graph()
    segment = next(n for n in graph['entities'] if n['name'] == '10.1.0.0/24')
    other = deepcopy(segment);other['id'] += ':other-context'
    graph['entities'].append(other)
    data = await scoped_view(_authority(), {**_request(segment['scope_id']), 'layers': []},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert not data['edges']

