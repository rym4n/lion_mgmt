"""Collection metadata supplies display fallbacks without becoming device identity."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.network_topology import agent_jobs, contracts
from services.network_topology.graph_pipeline import build_graph
from services.network_topology.identity import IdentityRegistry
from services.network_topology.versions import validate_publication
from tests.network_topology.test_auto_collection_agent_jobs import collection, doc
from tests.network_topology.test_phase4_scope_tree import _profile


def _inputs():
    snapshot = collection()
    snapshot['sources'][0]['resources'][0]['asset_context'].update(
        name='Synthetic resource', management_ips=['not-an-ip', '192.0.2.19'],
        vendor='synthetic-firewall',
    )
    candidate = doc()
    candidate['devices'][0]['identity_aliases'] = []
    results, errors = agent_jobs.convert_documents(
        snapshot, SimpleNamespace(id='run-1'),
        [{'resource_id': 'r1', 'chunk_id': 'r1:raw:v1', 'document': candidate}],
    )
    assert not errors
    facts = results[0].facts
    resolution = IdentityRegistry('tenant-1', 'collection').resolve_facts(facts)
    return snapshot, facts, resolution


def _graph(snapshot, facts, resolution):
    return build_graph(facts, resolution, _profile_document(), profile_revision=3,
                       collection=snapshot)


def _profile_document():
    profile = deepcopy(contracts.load_schema('aioe.network-topology.profile/v1')['examples'][0])
    profile.update(_profile())
    return profile


def test_bound_snapshot_metadata_labels_device_without_changing_facts_or_identity():
    snapshot, facts, resolution = _inputs()
    frozen_snapshot, frozen_facts = deepcopy(snapshot), deepcopy(facts)

    graph = _graph(snapshot, facts, resolution)

    entity = graph['entities'][0]
    assert entity['id'] == 'device:collection:r1'
    assert entity['name'] == 'Synthetic resource'
    assert entity['attributes'] == {
        'management_ip': '192.0.2.19', 'vendor': 'synthetic-firewall',
    }
    assert entity['provenance'][-1] == {
        'type': 'derived', 'rule_id': 'collection.asset_display/v1',
        'profile_revision': 3,
        'member_ids': sorted([facts['snapshot_id'], 'device:r1']),
    }
    assert facts == frozen_facts and snapshot == frozen_snapshot
    assert IdentityRegistry('tenant-1', 'collection').resolve_facts(facts) == resolution
    validate_publication(facts, graph, _profile_document(), 3, require_non_empty=True)


def test_observed_hostname_and_attributes_take_priority_over_resource_display_context():
    snapshot, facts, resolution = _inputs()
    facts['devices'][0]['attributes'].update(
        identity_aliases=[{'type': 'hostname', 'namespace': 'default', 'value': 'Observed FW'}],
        vendor='observed-vendor', management_ips=['192.0.2.20'],
    )

    entity = _graph(snapshot, facts, resolution)['entities'][0]

    assert entity['name'] == 'Observed FW'
    assert entity['attributes'] == {
        'management_ip': '192.0.2.20', 'vendor': 'observed-vendor',
    }
    assert not any(item.get('rule_id') == 'collection.asset_display/v1'
                   for item in entity['provenance'])


@pytest.mark.parametrize('hostname', ['   ', 'unknown', ' Unknown '])
def test_placeholder_hostname_does_not_hide_bound_resource_name(hostname):
    snapshot, facts, resolution = _inputs()
    facts['devices'][0]['attributes']['identity_aliases'] = [
        {'type': 'hostname', 'namespace': 'default', 'value': hostname},
    ]

    entity = _graph(snapshot, facts, resolution)['entities'][0]

    assert entity['name'] == 'Synthetic resource'


def test_display_name_uses_first_meaningful_hostname_alias():
    snapshot, facts, resolution = _inputs()
    facts['devices'][0]['attributes']['identity_aliases'] = [
        {'type': 'hostname', 'namespace': 'default', 'value': 'unknown'},
        {'type': 'hostname', 'namespace': 'default', 'value': ' FW1 '},
    ]

    entity = _graph(snapshot, facts, resolution)['entities'][0]

    assert entity['name'] == 'FW1'


@pytest.mark.parametrize('invalid_binding', ['snapshot', 'resource', 'fact', 'duplicate'])
def test_display_context_never_crosses_snapshot_or_resource_binding(invalid_binding):
    snapshot, facts, resolution = _inputs()
    resource = snapshot['sources'][0]['resources'][0]
    if invalid_binding == 'snapshot':
        snapshot['snapshot_id'] = 'other-snapshot'
    elif invalid_binding == 'resource':
        resource['resource_id'] = 'other-resource'
    elif invalid_binding == 'fact':
        facts['devices'][0]['resource_ids'] = ['other-resource']
    else:
        snapshot['sources'][0]['resources'].append(deepcopy(resource))

    entity = _graph(snapshot, facts, resolution)['entities'][0]

    assert entity['name'] == entity['id']
    assert entity['attributes'] == {}
    assert not any(item.get('rule_id') == 'collection.asset_display/v1'
                   for item in entity['provenance'])


@pytest.mark.parametrize('management_ips', [[], ['not-an-ip'], ['https://192.0.2.19'], ['192.0.2.19/24']])
def test_invalid_management_address_is_not_displayed(management_ips):
    snapshot, facts, resolution = _inputs()
    snapshot['sources'][0]['resources'][0]['asset_context']['management_ips'] = management_ips

    entity = _graph(snapshot, facts, resolution)['entities'][0]

    assert 'management_ip' not in entity['attributes']


def test_ipv6_management_address_is_canonicalized():
    snapshot, facts, resolution = _inputs()
    snapshot['sources'][0]['resources'][0]['asset_context']['management_ips'] = ['2001:db8:0::19']

    entity = _graph(snapshot, facts, resolution)['entities'][0]

    assert entity['attributes']['management_ip'] == '2001:db8::19'
