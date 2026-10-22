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

