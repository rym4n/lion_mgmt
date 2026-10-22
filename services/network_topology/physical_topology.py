"""Store collected physical devices and observed links independently of logical scope policy."""
from copy import deepcopy
from hashlib import sha256
import json

from services.network_topology.device_presentation import interface_presentations
from services.network_topology.scope_tree import _device_attributes, _entity_name
from services.network_topology.facts.physical_evidence import _port


def build_physical_topology(facts, resolution, root_scope_id, profile_revision=1):
    catalog = {item['id']: item['provenance'] for item in facts.get('provenance_catalog', ())}
    def provenance(fact):
        entries = [deepcopy(catalog[key]) for key in fact.get('provenance_ids', ()) if key in catalog]
        return entries or [{'type': 'derived', 'rule_id': 'physical.fact_presentation/v1',
            'profile_revision': profile_revision, 'member_ids': [fact['id']]}]
    entities, mapping = {}, {}
    for fact in sorted(facts.get('devices', ()), key=lambda item: item['id']):
        identifier = resolution.fact_identity_map.get(fact['id']) or fact['id']
        mapping[fact['id']] = identifier
        attrs = _device_attributes(fact)
        interfaces = interface_presentations(facts, fact['id'])
        if interfaces:
            attrs['interfaces'] = interfaces
        entities[identifier] = _entity(identifier, root_scope_id, 'device', _entity_name(fact, identifier),
            _device_role(fact), attrs, provenance(fact))
    interfaces = {item['id']: item for item in facts.get('interfaces', ())}
    relations = {}
    for fact in sorted(facts.get('adjacencies', ()), key=lambda item: item['id']):
        attrs = fact.get('attributes', {})
        if attrs.get('layer') != 'physical' or attrs.get('neighbor_type') not in {'lldp', 'cdp'}:
            continue
        local_interface = interfaces.get(attrs.get('local_id'), {})
        local_attrs = local_interface.get('attributes', {})
        left = mapping.get(local_attrs.get('device_id') or attrs.get('local_id'))
        remote_interface = interfaces.get(attrs.get('remote_id'), {})
        remote_attrs = remote_interface.get('attributes', {})
        right = mapping.get(remote_attrs.get('device_id') or attrs.get('remote_id'))
        local_port = attrs.get('local_port') or local_attrs.get('canonical_name') or local_attrs.get('raw_name')
        remote_port = attrs.get('remote_port') or remote_attrs.get('canonical_name') or remote_attrs.get('raw_name') or attrs.get('remote_identity', {}).get('interface')
        if left is None:
            continue
        if right is None:
            identity = {key: value for key, value in attrs.get('remote_identity', {}).items()
                        if key in {'hostname', 'chassis_id', 'management_ip'} and value}
            if not identity or not local_port or not remote_port:
                continue
            key = json.dumps([left, identity], sort_keys=True, separators=(',', ':'))
            right = 'physical-peer:' + sha256(key.encode()).hexdigest()[:24]
            name = identity.get('hostname') or identity.get('chassis_id') or identity['management_ip']
            entities.setdefault(right, _entity(right, root_scope_id, 'external', name,
                'observed_physical_peer', {'observed_identity': identity}, provenance(fact)))
        if left == right:
            continue
        attributes = {'protocol': attrs['neighbor_type']}
        if local_port: attributes['local_port'] = local_port
        if remote_port: attributes['remote_port'] = remote_port
        for key in ('media_type', 'speed_bps'):
            if key in attrs: attributes[key] = attrs[key]
            elif key in local_attrs: attributes[key] = local_attrs[key]
        attachment = '|'.join(sorted((left + ':' + str(local_port or ''), right + ':' + str(remote_port or ''))))
        cable_key = tuple(sorted(((left, _port(local_port or '')), (right, _port(remote_port or '')))))
        state = str(attrs.get('state') or 'unknown').lower()
        state = state if state in {'up', 'down', 'degraded'} else 'unknown'
        if cable_key in relations:
            previous = relations[cable_key]
            previous['member_fact_ids'].append(fact['id'])
            for entry in provenance(fact):
                if entry not in previous['provenance']:
                    previous['provenance'].append(entry)
            protocols = set(previous['attributes']['protocol'].split('/')) | {attrs['neighbor_type']}
            previous['attributes']['protocol'] = '/'.join(sorted(protocols))
            known = {previous['operational_state'], state} - {'unknown'}
            previous['operational_state'] = 'degraded' if len(known) > 1 else next(iter(known), 'unknown')
            continue
        relations[cable_key] = {'id': 'physical-link:' + sha256(json.dumps(cable_key).encode()).hexdigest()[:24],
            'multi_edge_identity': {'endpoint_key': '|'.join(sorted((left, right))), 'layer': 'physical',
                'relation_type': 'physical_link', 'attachment_key': attachment, 'context_key': ''},
            'relation_type': 'physical_link', 'layer': 'physical', 'direction': 'bidirectional',
            'endpoints': [{'ref_type': 'entity', 'ref_id': left, 'attachment': local_port or ''},
                          {'ref_type': 'entity', 'ref_id': right, 'attachment': remote_port or ''}],
            'owner_scope_id': root_scope_id, 'attributes': attributes,
            'member_fact_ids': [fact['id']], 'member_relation_ids': [],
            'configured_state': 'unknown', 'operational_state': state, 'provenance': provenance(fact)}
    return {'entities': list(entities.values()), 'relations': list(relations.values())}


def _device_role(fact):
    attributes = fact.get('attributes', {})
    kind = attributes.get('device_type')
    if kind and kind not in {'unknown', 'device', 'network_device'}:
        return kind
    return next(iter(attributes.get('roles', ())), 'network_device')


def _entity(identifier, root, kind, name, role, attributes, provenance):
    return {'id': identifier, 'scope_id': root, 'scope_memberships': [], 'kind': kind,
        'name': name, 'role': role, 'representation': 'network_device' if kind == 'device' else 'external_network',
        'importance': 'normal', 'importance_reasons': [], 'attributes': attributes, 'provenance': provenance}
