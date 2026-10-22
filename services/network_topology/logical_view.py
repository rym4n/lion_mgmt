"""Present logical objects and their ownership without physical device vertices."""
from copy import deepcopy
from ipaddress import ip_interface, ip_network

from services.network_topology.scope_tree import ScopeTreeResult


def logical_objects(tree, relations):
    entities = deepcopy(list(tree.entities))
    scopes = deepcopy(list(tree.scopes))
    devices = {item['id']: item for item in entities if item['kind'] == 'device'}
    logical = {item['id']: item for item in entities if item['kind'] != 'device'}

    def belongs(item, scope_id):
        return scope_id in {item['scope_id'], *item.get('scope_memberships', ())}

    def matching_interfaces(device, segment):
        networks = set()
        for prefix in segment.get('attributes', {}).get('network_prefixes', ()):
            try:
                networks.add(ip_network(prefix, strict=False))
            except ValueError:
                continue
        matches = []
        for interface in device.get('attributes', {}).get('interfaces', ()):
            for address in interface.get('addresses', ()):
                try:
                    if ip_interface(address).network in networks:
                        matches.append(interface)
                        break
                except ValueError:
                    continue
        return matches

    def attach(item, device, interfaces=()):
        attrs = device.get('attributes', {})
        summary = {'name': device.get('name') or device['id'],
                   **{k: attrs[k] for k in ('management_ip', 'vendor', 'model') if k in attrs},
                   'interfaces': list(interfaces)}
        values = item.setdefault('attributes', {}).setdefault('related_devices', [])
        if summary not in values:
            values.append(summary)

    for scope in scopes:
        for device in devices.values():
            if belongs(device, scope['id']):
                attach(scope, device)
    for entity in logical.values():
        if entity['kind'] != 'network_segment':
            continue
        for device in devices.values():
            if not (set([entity['scope_id'], *entity.get('scope_memberships', ())])
                    & set([device['scope_id'], *device.get('scope_memberships', ())])):
                continue
            interfaces = matching_interfaces(device, entity)
            if interfaces:
                attach(entity, device, interfaces)

    output = []
    for raw in relations:
        relation = deepcopy(raw)
        endpoints = relation.get('endpoints', ())
        device_endpoints = [endpoint for endpoint in endpoints if endpoint.get('ref_id') in devices]
        if not device_endpoints:
            output.append(relation)
            continue
        if relation.get('relation_type') != 'configured_route' or len(device_endpoints) != 1:
            continue
        device = devices[device_endpoints[0]['ref_id']]
        boundary = next((logical.get(e.get('ref_id')) for e in endpoints
                         if e.get('ref_id') in logical), None)
        if boundary is None:
            continue
        names = relation.get('attributes', {}).get('interface_names', ())
        candidates = []
        for segment in logical.values():
            if segment['kind'] != 'network_segment' or not belongs(segment, boundary['scope_id']):
                continue
            matched = [i for i in matching_interfaces(device, segment) if i.get('name') in names]
            if matched:
                candidates.append((segment, matched))
        attach(boundary, device, [i for i in device.get('attributes', {}).get('interfaces', ()) if i.get('name') in names])
        if len(candidates) != 1:
            continue
        segment, _ = candidates[0]
        for endpoint in endpoints:
            if endpoint.get('ref_id') == device['id']:
                endpoint['ref_id'] = segment['id']
        output.append(relation)
    return ScopeTreeResult(tree.root_scope_id, tuple(scopes), tuple(logical.values()),
                           tree.unresolved, tree.excluded_fact_ids, tree.collapsed_fact_ids), output
