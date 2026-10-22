"""Regressions for real network_context facts and the logical default view."""
from copy import deepcopy

import pytest

from services.network_topology.contracts import load_schema, validate_contract
from services.network_topology.query_service import _api_node, scoped_view
from services.network_topology.scope_tree import build_scope_tree, ROOT_SCOPE_ID
from services.network_topology.projection import project_scope
from services.network_topology.relationships import RelationshipResult
from services.network_topology.relationships import build_relationships
from tests.network_topology.test_phase4_scope_tree import _device, _facts, _resolution, _profile
from tests.network_topology.test_phase4_projection import _entity, _relation, _scope, _tree
from tests.network_topology.test_phase5_query_api import (
    _authority, _request, _document, MemoryRepository, MemoryCache,
)


def _context(identifier, name, *, context_type='zone'):
    return {'id': identifier, 'fact_family': 'network_context',
            'attributes': {'owner_id': 'device:fw', 'local_id': name,
                           'name': name, 'context_type': context_type},
            'provenance_ids': []}


def _segment(identifier, prefix, **attrs):
    return {'id': identifier, 'fact_family': 'network',
            'attributes': {'canonical_cidr': prefix, 'network_domain_id': 'lab',
                           'device_id': 'device:fw', **attrs}, 'provenance_ids': []}


def _interface(identifier, address, **attrs):
    return {'id': identifier, 'attributes': {
        'device_id': 'device:fw', 'addresses': [address], **attrs}}


def _zone_facts():
    facts = _facts(_device('fw'), segments=(
        _segment('network:a', '10.1.0.0/24'),
        _segment('network:b', '2000::/96'),
    ))
    facts['network_contexts'] = [
        _context('context:trust', 'Trust'), _context('context:wan', 'WAN'),
        _context('context:empty', 'Empty'),
        _context('context:default', 'default', context_type='vrf'),
    ]
    facts['interfaces'] = [
        _interface('interface:a', '10.1.0.1/24'),
        _interface('interface:b', '2000::1/96', context_id='context:wan'),
    ]
    facts['memberships'] = [{'id': 'membership:a', 'attributes': {
        'member_id': 'interface:a', 'container_id': 'context:trust',
        'membership_kind': 'zone_member'}}]
    return facts


def test_real_network_contexts_place_segments_and_cross_zone_device_references():
    facts = _zone_facts()
    frozen = deepcopy(facts)
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    scope_by_name = {scope['name']: scope['id'] for scope in tree.scopes}
    assert set(scope_by_name) == {'Organization', 'Trust', 'WAN'}
    entities = {item['name']: item for item in tree.entities}
    assert entities['10.1.0.0/24']['scope_id'] == scope_by_name['Trust']
    assert entities['2000::/96']['scope_id'] == scope_by_name['WAN']
    device = next(item for item in tree.entities if item['kind'] == 'device')
    assert device['scope_id'] == ROOT_SCOPE_ID
    assert set(device['scope_memberships']) == {scope_by_name['Trust'], scope_by_name['WAN']}
    child = project_scope(tree, RelationshipResult((), ()), scope_by_name['Trust'])
    assert any(node.get('ref_id') == device['id'] for node in child.nodes)
    assert facts == frozen


def test_context_binding_never_uses_other_device_interface_with_overlapping_address():
    facts = _zone_facts()
    facts['interfaces'][0]['attributes']['device_id'] = 'device:other'
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    entity = next(item for item in tree.entities if item['name'] == '10.1.0.0/24')
    assert entity['scope_id'] == ROOT_SCOPE_ID


def test_segment_explicit_context_rejects_same_cidr_from_another_vrf():
    from services.network_topology.context_placement import context_memberships
    facts = _zone_facts()
    facts['segments'] = [_segment('network:a', '10.1.0.0/24', context_id='context:trust')]
    facts['interfaces'][0]['attributes']['context_id'] = 'context:trust'
    facts['interfaces'][1]['attributes']['addresses'] = ['10.1.0.2/24']
    assert context_memberships(facts)['network:a'] == ('context:trust',)


def test_segment_without_explicit_context_preserves_ambiguous_same_cidr_memberships():
    from services.network_topology.context_placement import context_memberships
    facts = _zone_facts()
    facts['interfaces'][1]['attributes']['addresses'] = ['10.1.0.2/24']
    expected = ('context:trust', 'context:wan')
    assert context_memberships(facts)['network:a'] == expected
    facts['interfaces'].reverse()
    assert context_memberships(facts)['network:a'] == expected


@pytest.mark.parametrize('prefix,version,prefix_length,scope', [
    ('10.0.0.0/8', 4, 8, 'private'), ('172.16.0.0/12', 4, 12, 'private'),
    ('192.168.0.0/16', 4, 16, 'private'), ('fc00::/7', 6, 7, 'private'),
    ('2000::/96', 6, 96, 'public'), ('8.8.8.0/24', 4, 24, 'public'),
    ('169.254.0.0/16', 4, 16, 'link_local'), ('fe80::/10', 6, 10, 'link_local'),
    ('127.0.0.0/8', 4, 8, 'loopback'), ('::1/128', 6, 128, 'loopback'),
    ('224.0.0.0/4', 4, 4, 'multicast'), ('ff00::/8', 6, 8, 'multicast'),
    ('0.0.0.0/32', 4, 32, 'unspecified'), ('::/128', 6, 128, 'unspecified'),
    ('192.0.2.0/24', 4, 24, 'reserved'), ('2001:db8::/32', 6, 32, 'reserved'),
    ('100.64.0.0/10', 4, 10, 'reserved'), ('0.0.0.0/0', 4, 0, 'reserved'),
])
def test_segment_address_classification(prefix, version, prefix_length, scope):
    facts = _facts(segments=(_segment('network:a', prefix),))
    tree = build_scope_tree(facts, _resolution(), _profile())
    attrs = tree.entities[0]['attributes']
    assert attrs['ip_version'] == version
    assert attrs['prefix_length'] == prefix_length
    assert attrs['address_scope'] == scope
    validate_contract(tree.as_graph(), 'aioe.network-topology.graph/v2')


@pytest.mark.asyncio
async def test_empty_layers_is_logical_default_without_gateway_star_or_empty_scope():
    child, empty = 'scope:security_zone:trust', 'scope:security_zone:empty'
    scopes = [_scope(ROOT_SCOPE_ID), _scope(child, ROOT_SCOPE_ID), _scope(empty, ROOT_SCOPE_ID)]
    scopes[1]['scope_type'] = scopes[2]['scope_type'] = 'security_zone'
    entities = [_entity('device:fw', ROOT_SCOPE_ID, memberships=(child,)),
                _entity('network:in-zone', child, kind='network_segment'),
                _entity('network:orphan', ROOT_SCOPE_ID, kind='network_segment')]
    relations = [_relation('gateway:zone', 'device:fw', 'network:in-zone', layer='service', relation_type='gateway_for'),
                 _relation('gateway:orphan', 'device:fw', 'network:orphan', layer='service', relation_type='gateway_for')]
    graph = _tree(scopes, entities).as_graph()
    graph['relations'] = relations
    request = {**_request(ROOT_SCOPE_ID), 'layers': []}
    data = await scoped_view(_authority(), request,
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert {node['target_id'] for node in data['nodes']} == {'device:fw', 'network:orphan', child}
    assert not data['edges']
    child_data = await scoped_view(_authority(), {**request, 'scope_id': child},
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert {node['target_id'] for node in child_data['nodes']} == {'device:fw', 'network:in-zone'}
    request['layers'] = ['service']
    data = await scoped_view(_authority(), request,
        repository=MemoryRepository(_document(graph=graph)), cache=MemoryCache())
    assert 'network:orphan' in {node['target_id'] for node in data['nodes']}
    assert not data['edges']
    assert len(graph['relations']) == 2 and len(graph['entities']) == 3


def test_api_node_exposes_only_safe_presentation_metadata():
    node = _api_node({'id': 'network:a', 'kind': 'network_segment',
        'name': '2000::/96', 'role': 'connected_network', 'importance': 'normal',
        'attributes': {'network_prefixes': ['2000::/96'], 'ip_version': 6,
                       'prefix_length': 96, 'address_scope': 'public', 'password': 'secret'}})
    assert node['attributes'] == {'network_prefixes': ['2000::/96'], 'ip_version': 6,
                                  'prefix_length': 96, 'address_scope': 'public'}
    assert node['role'] == 'connected_network' and node['importance'] == 'normal'
    response = deepcopy(load_schema('aioe.network-topology.api/view-response/v1')['examples'][0])
    response['data']['nodes'] = [node]
    validate_contract(response, 'aioe.network-topology.api/view-response/v1')


def test_configured_next_hops_are_boundaries_with_observed_routes_not_discovered_devices():
    facts = _zone_facts()
    facts['routes'] = [
        {'id': 'route:a', 'attributes': {'route_type': 'default', 'prefix': '0.0.0.0/0',
            'vrf_id': 'business', 'next_hop_address': '10.1.0.254',
            'next_hop_id': 'unresolved-selector:ip', 'interface_id': 'interface:a', 'state': 'up'},
         'provenance_ids': []},
        {'id': 'route:b', 'attributes': {'route_type': 'static', 'prefix': '10.8.0.0/16',
            'vrf_id': 'business', 'next_hop_address': '10.1.0.254',
            'next_hop_id': 'unresolved-selector:ip', 'interface_id': 'interface:a', 'state': 'up'},
         'provenance_ids': []},
    ]
    resolution = _resolution('device:fw')
    tree = build_scope_tree(facts, resolution, _profile())
    boundary = next(entity for entity in tree.entities if entity['role'] == 'configured_next_hop')
    assert boundary['kind'] == 'external' and boundary['name'] == '10.1.0.254'
    assert boundary['attributes']['route_prefixes'] == ['0.0.0.0/0', '10.8.0.0/16']
    assert sum(entity['kind'] == 'device' for entity in tree.entities) == 1
    relations = build_relationships(facts, resolution, tree)
    routes = [relation for relation in relations.relations if relation['relation_type'] == 'configured_route']
    assert len(routes) == 1
    route = routes[0]
    assert route['direction'] == 'unidirectional' and route['layer'] == 'routing'
    assert route['configured_state'] == 'present' and route['operational_state'] == 'unknown'
    assert route['attributes']['route_prefixes'] == ['0.0.0.0/0', '10.8.0.0/16']
    assert route['member_fact_ids'] == ['route:a', 'route:b']
    root = project_scope(tree, relations, ROOT_SCOPE_ID, layers=['routing'])
    edge = next(edge for edge in root.edges if edge['relation_type'] == 'configured_route')
    assert edge['direction'] == 'unidirectional'
    assert edge['endpoints'][0]['ref_id'] == 'canonical:device:fw'


@pytest.mark.parametrize('mutation', ['unknown-interface', 'bad-address', 'connected', 'unspecified'])
def test_routes_without_valid_next_hop_binding_never_create_boundaries(mutation):
    facts = _zone_facts()
    attrs = {'route_type': 'static', 'prefix': '10.8.0.0/16', 'vrf_id': 'business',
             'next_hop_address': '10.1.0.254', 'interface_id': 'interface:a', 'state': 'up'}
    if mutation == 'unknown-interface': attrs['interface_id'] = 'interface:other'
    elif mutation == 'bad-address': attrs['next_hop_address'] = 'not-an-ip'
    elif mutation == 'connected': attrs['route_type'] = 'connected'
    else: attrs['next_hop_address'] = '0.0.0.0'
    facts['routes'] = [{'id': 'route:a', 'attributes': attrs, 'provenance_ids': []}]
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    assert not any(entity['role'] == 'configured_next_hop' for entity in tree.entities)


def test_route_candidate_keeps_only_evidence_supported_next_hop_address():
    from services.network_topology import agent_jobs
    from types import SimpleNamespace
    from tests.network_topology.test_auto_collection_agent_jobs import collection, doc
    snapshot, candidate = collection(), doc()
    snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']['records'][0]['data'] = '0.0.0.0/0 via 192.0.2.1'
    candidate['routes'] = [{'candidate_id': 'r1:raw:v1:route', 'route_type': 'default',
        'prefix': '0.0.0.0/0', 'vrf': 'default', 'next_hop_selector': {'management_ip': '192.0.2.1'},
        'interface_candidate_ref': None, 'state': 'unknown',
        'evidence_refs': deepcopy(candidate['devices'][0]['evidence_refs'])}]
    candidate['devices'][0]['identity_aliases'] = []
    items = [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': candidate}]
    converted, errors = agent_jobs.convert_documents(snapshot, SimpleNamespace(id='run'), items)
    assert not errors
    assert converted[0].facts['routes'][0]['attributes']['next_hop_address'] == '192.0.2.1'
    candidate['routes'][0]['next_hop_selector']['management_ip'] = '192.0.2.199'
    _, errors = agent_jobs.convert_documents(snapshot, SimpleNamespace(id='run'), items)
    assert errors


def _structured_route_input():
    from tests.network_topology.test_auto_collection_agent_jobs import collection, doc
    snapshot, candidate = collection(), doc()
    data = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']
    data['routes'] = {'scopes': [{'query': {'tableId': 251}, 'data': {'items': [
        {'prefix': '0.0.0.0/0', 'gateway': ['192.0.2.1', '192.0.2.2'], 'ifname': ['eth3', 'eth4']},
        {'prefix': '10.0.0.0/8', 'gateway': '192.0.2.9', 'ifname': 'eth3'},
    ]}}]}
    candidate['interfaces'] = [{'candidate_id': 'r1:raw:v1:eth3', 'device_candidate_ref': 'r1:raw:v1:dev',
        'context_candidate_ref': None, 'canonical_name': 'eth3', 'raw_name': 'eth3', 'if_index': None,
        'mac': None, 'parent_ref': None, 'lag_ref': None, 'addresses': [],
        'configured_state': 'up', 'operational_state': 'up', 'evidence_refs': [{
            'observation_id': 'obs-1', 'locator': {'type': 'json_pointer',
                'pointer': '/output/data/routes/scopes/0/data/items/0'}}]}]
    candidate['routes'] = [{'candidate_id': 'r1:raw:v1:route', 'route_type': 'default',
        'prefix': '0.0.0.0/0', 'vrf': 'table:251', 'next_hop_selector': {'management_ip': '192.0.2.1'},
        'interface_candidate_ref': 'r1:raw:v1:eth3', 'state': 'unknown',
        'evidence_refs': deepcopy(candidate['interfaces'][0]['evidence_refs'])}]
    return snapshot, candidate


@pytest.mark.parametrize('mutation', ['none', 'field-locator', 'wrong-interface', 'wrong-table', 'cross-row', 'wrong-field'])
def test_route_tuple_must_match_one_record_and_ecmp_index(mutation):
    from services.network_topology import agent_jobs
    from types import SimpleNamespace
    snapshot, candidate = _structured_route_input()
    route = candidate['routes'][0]
    if mutation == 'field-locator': route['evidence_refs'][0]['locator']['pointer'] += '/gateway/0'
    elif mutation == 'wrong-interface': route['next_hop_selector']['management_ip'] = '192.0.2.2'
    elif mutation == 'wrong-table': route['vrf'] = 'table:250'
    elif mutation == 'wrong-field':
        row = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']['routes']['scopes'][0]['data']['items'][0]
        row['comment'] = '192.0.2.99'
        route['next_hop_selector']['management_ip'] = '192.0.2.99'
    elif mutation == 'cross-row':
        route['next_hop_selector']['management_ip'] = '192.0.2.9'
        second = deepcopy(route['evidence_refs'][0])
        second['locator']['pointer'] = second['locator']['pointer'][:-1] + '1'
        route['evidence_refs'].append(second)
    results, errors = agent_jobs.convert_documents(snapshot, SimpleNamespace(id='run'),
        [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': candidate}])
    assert bool(errors) == (mutation not in {'none', 'field-locator'})
    if not errors:
        assert results[0].facts['routes'][0]['attributes']['next_hop_address'] == '192.0.2.1'


@pytest.mark.parametrize('vrf', ['default', 'table:0'])
def test_default_route_table_zero_has_canonical_aliases(vrf):
    from services.network_topology.facts.route_evidence import supports_route
    snapshot, document = _structured_route_input()
    observations = snapshot['sources'][0]['resources'][0]['result']['observations']
    observations[0]['data']['output']['data']['routes']['scopes'][0]['query']['tableId'] = 0
    document['routes'][0]['vrf'] = vrf
    assert supports_route(document['routes'][0], document, observations)


@pytest.mark.parametrize('state', ['down', 'inactive', 'invalid', 'disabled', 'INACTIVE'])
def test_route_down_requires_reported_invalid_or_inactive_state_in_same_record(state):
    from services.network_topology.facts.route_evidence import supports_route
    snapshot, document = _structured_route_input()
    observations = snapshot['sources'][0]['resources'][0]['result']['observations']
    route = document['routes'][0]
    route['state'] = state
    assert not supports_route(route, document, observations)
    row = observations[0]['data']['output']['data']['routes']['scopes'][0]['data']['items'][0]
    row['status'] = 'INVALID'
    assert supports_route(route, document, observations)


@pytest.mark.parametrize('state', ['inactive', 'invalid', 'disabled'])
def test_route_state_alias_requires_matching_next_hop_status_and_converts_to_down(state):
    from services.network_topology import agent_jobs
    from types import SimpleNamespace
    snapshot, document = _structured_route_input()
    route = document['routes'][0]
    route['state'] = state
    row = snapshot['sources'][0]['resources'][0]['result']['observations'][0]['data']['output']['data']['routes']['scopes'][0]['data']['items'][0]
    row['nexthopStatus'] = [{'nexthopStatus': 'active'}, {'nexthopStatus': 'inactive'}]
    items = [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': document}]
    _, errors = agent_jobs.convert_documents(snapshot, SimpleNamespace(id='run'), items)
    assert errors
    row['nexthopStatus'][0]['nexthopStatus'] = 'inactive'
    results, errors = agent_jobs.convert_documents(snapshot, SimpleNamespace(id='run'), items)
    assert not errors
    assert results[0].facts['routes'][0]['attributes']['state'] == 'down'


@pytest.mark.parametrize('state', ['inactive', 'invalid', 'disabled'])
def test_text_route_state_alias_requires_same_line_proof(state):
    from services.network_topology.facts.route_evidence import _text_supports
    from ipaddress import ip_network, ip_address
    arguments = ip_network('0.0.0.0/0'), ip_address('192.0.2.1'), 'eth3', 'default', state
    line = '0.0.0.0/0 via 192.0.2.1 dev eth3'
    assert not _text_supports(line, *arguments)
    assert _text_supports(line + ' inactive', *arguments)


def test_text_route_vrf_and_tuple_are_bound_to_same_line():
    from services.network_topology.facts.route_evidence import _text_supports
    from ipaddress import ip_network, ip_address
    arguments = ip_network('0.0.0.0/0'), ip_address('192.0.2.1'), 'eth3'
    line = 'vrf business 0.0.0.0/0 via 192.0.2.1 dev eth3'
    assert _text_supports(line, *arguments, 'business')
    assert not _text_supports(line, *arguments, 'other-business')
    assert not _text_supports('0.0.0.0/0 via 192.0.2.1 dev eth3', *arguments, 'business')


@pytest.mark.parametrize('vrf', ['default', 'table:0'])
def test_text_default_route_rejects_explicit_other_namespace(vrf):
    from services.network_topology.facts.route_evidence import _text_supports
    from ipaddress import ip_network, ip_address
    arguments = ip_network('0.0.0.0/0'), ip_address('192.0.2.1'), 'eth3', vrf
    line = '0.0.0.0/0 via 192.0.2.1 dev eth3'
    assert _text_supports(line, *arguments)
    assert _text_supports(line + ' table 0', *arguments)
    assert _text_supports(line + ' vrf default', *arguments)
    assert not _text_supports(line + ' table 250', *arguments)
    assert not _text_supports(line + ' vrf blue', *arguments)


@pytest.mark.parametrize('state', ['down', 'inactive', 'invalid', 'disabled'])
def test_equal_link_local_next_hops_on_different_interfaces_are_distinct_boundaries(state):
    facts = _zone_facts()
    facts['routes'] = [{'id': 'route:' + iface, 'attributes': {'route_type': 'default', 'prefix': '::/0',
        'vrf_id': 'business', 'next_hop_address': 'fe80::1', 'interface_id': iface, 'state': state},
        'provenance_ids': []} for iface in ('interface:a', 'interface:b')]
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    assert len([entity for entity in tree.entities if entity['role'] == 'configured_next_hop']) == 2
    relationships = build_relationships(facts, _resolution('device:fw'), tree)
    assert all(item['operational_state'] == 'down' for item in relationships.relations)


def test_address_matching_for_security_never_uses_another_device_or_context():
    from services.network_topology.relationships import _RelationshipState
    facts = _zone_facts()
    facts['segments'] = [_segment('network:a', '10.1.0.0/24', device_id='device:other')]
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    state = _RelationshipState(facts, _resolution('device:fw'), tree, 1)
    assert state._interface_segment('interface:a') is None
    facts['segments'][0]['attributes'].update(device_id='device:fw', context_id='context:wan')
    facts['interfaces'][0]['attributes']['context_id'] = 'context:trust'
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    state = _RelationshipState(facts, _resolution('device:fw'), tree, 1)
    assert state._interface_segment('interface:a') is None


def test_api_route_edge_preserves_down_summary_and_rejects_extra_metadata():
    from services.network_topology.query_service import _api_edge
    edge = _api_edge({'id': 'edge:a', 'endpoints': [{'ref_id': 'a'}, {'ref_id': 'b'}],
        'relation_type': 'configured_route', 'layer': 'routing', 'member_relation_ids': ['route:a'],
        'summary': {'configured': {'present': 1}, 'operational': {'down': 1}, 'password': 'secret'}})
    assert edge['state_summary'] == {'configured': {'present': 1}, 'operational': {'down': 1}}
    response = deepcopy(load_schema('aioe.network-topology.api/view-response/v1')['examples'][0])
    response['data']['edges'] = [edge]
    validate_contract(response, 'aioe.network-topology.api/view-response/v1')


def test_configured_routes_in_child_scope_follow_their_egress_boundary():
    facts = _zone_facts()
    facts['routes'] = [{'id': 'route:' + iface, 'attributes': {'route_type': 'default', 'prefix': '0.0.0.0/0',
        'vrf_id': 'business', 'next_hop_address': ip, 'interface_id': iface, 'state': 'unknown'},
        'provenance_ids': []} for iface, ip in [('interface:a', '10.1.0.254'), ('interface:b', '192.0.2.1')]]
    tree = build_scope_tree(facts, _resolution('device:fw'), _profile())
    relations = build_relationships(facts, _resolution('device:fw'), tree)
    scopes = {scope['name']: scope['id'] for scope in tree.scopes}
    trust = project_scope(tree, relations, scopes['Trust'], layers=['routing'])
    assert len(trust.edges) == 1
    assert {node['name'] for node in trust.nodes if node.get('role') == 'configured_next_hop'} == {'10.1.0.254'}
    root = project_scope(tree, relations, ROOT_SCOPE_ID, layers=['routing'])
    assert len(root.edges) == 2
