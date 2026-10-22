"""Present logical objects and their ownership without physical device vertices."""
from copy import deepcopy
from ipaddress import ip_interface, ip_network
from urllib.parse import quote

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
        source, _ = candidates[0]
        prefixes = []
        for prefix in relation.get('attributes', {}).get('route_prefixes', ()):
            try:
                prefixes.append(ip_network(prefix, strict=False))
            except ValueError:
                continue
        projected_routes = []
        for route in prefixes:
            if route.prefixlen == 0:
                # A default route has no canonical destination subnet. Retain
                # the configured next-hop as the explicit external boundary.
                boundary['scope_id'] = tree.root_scope_id
                projected_routes.append((boundary, str(route)))
                continue

            candidates = []
            for segment in logical.values():
                if segment['kind'] != 'network_segment' or segment['id'] == source['id']:
                    continue
                networks = []
                for prefix in segment.get('attributes', {}).get('network_prefixes', ()):
                    try:
                        networks.append(ip_network(prefix, strict=False))
                    except ValueError:
                        continue
                if any(route.version == network.version and route.overlaps(network)
                       for network in networks):
                    candidates.append(segment)

            if len(candidates) == 1:
                target = candidates[0]
            else:
                # If the destination prefix is missing or maps to overlapping
                # networks in multiple scopes, show the configured prefix as a
                # boundary instead of asserting connectivity to an arbitrary
                # or multiple security domains.
                target_id = f"route-target:{quote(relation['id'], safe='')}:{quote(str(route), safe='')}"
                target = logical.get(target_id)
                if target is None:
                    target = {
                        'id': target_id,
                        'kind': 'external',
                        'name': str(route),
                        'role': 'configured_route_destination',
                        'importance': 'normal',
                        'scope_id': tree.root_scope_id,
                        'scope_memberships': [],
                        'attributes': {
                            'network_prefixes': [str(route)],
                            'route_prefixes': [str(route)],
                        },
                    }
                    attach(target, device, [
                        interface for interface in device.get('attributes', {}).get('interfaces', ())
                        if interface.get('name') in names
                    ])
                    logical[target_id] = target
            projected_routes.append((target, str(route)))

        # Preserve older route records that have a real next hop but no
        # parseable destination prefix.
        if not projected_routes:
            boundary['scope_id'] = tree.root_scope_id
            projected_routes.append((boundary, None))

        for target, prefix in projected_routes:
            projected = deepcopy(relation)
            projected['endpoints'] = [
                {'ref_id': source['id'] if endpoint.get('ref_id') == device['id']
                 else target['id'] if endpoint.get('ref_id') == boundary['id']
                 else endpoint.get('ref_id')}
                for endpoint in endpoints
            ]
            if prefix is not None:
                projected['attributes']['route_prefixes'] = [prefix]
            output.append(projected)
    return ScopeTreeResult(tree.root_scope_id, tuple(scopes), tuple(logical.values()),
                           tree.unresolved, tree.excluded_fact_ids, tree.collapsed_fact_ids), output
