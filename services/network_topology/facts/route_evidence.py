"""Bind a configured next hop to one observed route record and egress."""
from ipaddress import ip_address, ip_network
import re

from services.network_topology.evidence_locator import resolve_evidence
from services.network_topology.route_state import route_is_down


def supports_route(item, document, observations):
    address = ip_address(item['next_hop_selector']['management_ip'])
    prefix = ip_network(item['prefix'], strict=False)
    interface_ref = item.get('interface_candidate_ref')
    interface = next((value for value in document['interfaces']
                      if value['candidate_id'] == interface_ref), None)
    if interface_ref and interface is None:
        return False
    name = interface['raw_name'] if interface else None
    by_id = {value['observation_id']: value for value in observations}
    for reference in item['evidence_refs']:
        observation = by_id[reference['observation_id']]
        locator = reference['locator']
        value, table = _record_context(observation['data'], locator)
        for row, row_table in _rows(value, table):
            if isinstance(row, str):
                # A text route is indivisible only within its own line.
                if any(_text_supports(line, prefix, address, name, item.get('vrf'), item.get('state'))
                       for line in row.splitlines()):
                    return True
            elif _row_supports(row, row_table, item, prefix, address, name):
                return True
    return False


def _record_context(data, locator):
    """Allow a field locator to bind its nearest route row, never another row."""
    value = resolve_evidence(data, locator)
    if locator['type'] != 'json_pointer':
        return value, None
    pointer = locator['pointer']
    parts = pointer.split('/')[1:] if pointer else []
    current, table, nearest = data, None, None
    table = _table_context(current, table)
    if _is_row(current):
        nearest = current, table
    for part in parts:
        token = part.replace('~1', '/').replace('~0', '~')
        current = current[int(token)] if isinstance(current, list) else current[token]
        table = _table_context(current, table)
        if _is_row(current):
            nearest = current, table
    return nearest if nearest is not None else (value, table)


def _table_context(value, inherited):
    if not isinstance(value, dict):
        return inherited
    if 'tableId' in value:
        return value['tableId']
    query = value.get('query')
    return query.get('tableId', inherited) if isinstance(query, dict) else inherited


def _is_row(value):
    return isinstance(value, dict) and 'prefix' in value and 'gateway' in value


def _rows(value, table):
    table = _table_context(value, table)
    if _is_row(value) or isinstance(value, str):
        yield value, table
    elif isinstance(value, dict):
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _rows(child, table)
    elif isinstance(value, list):
        for child in value:
            yield from _rows(child, table)


def _normalize_table(value):
    return 'default' if value in (None, '', 0, '0', 'table:0', 'default') else (
        str(value) if str(value).startswith('table:') else 'table:' + str(value))


def _row_supports(row, table, item, prefix, address, name):
    try:
        if ip_network(row['prefix'], strict=False) != prefix:
            return False
    except (ValueError, TypeError):
        return False
    if _normalize_table(item.get('vrf')) != _normalize_table(table):
        return False
    gateways = row['gateway'] if isinstance(row['gateway'], list) else [row['gateway']]
    interfaces = row.get('ifname')
    interfaces = interfaces if isinstance(interfaces, list) else [interfaces]
    if len(interfaces) == 1:
        interfaces = interfaces * len(gateways)
    if len(gateways) != len(interfaces):
        return False
    for index, (gateway, ifname) in enumerate(zip(gateways, interfaces)):
        try:
            if ip_address(gateway) != address:
                continue
        except (ValueError, TypeError):
            continue
        if route_is_down(item.get('state')) and not _reported_down(row, index):
            continue
        if name is not None:
            if ifname == name:
                return True
        elif ifname in (None, '', 'auto', 'null0', 'Null0'):
            return True
    return False


def _reported_down(row, index):
    if any(route_is_down(row.get(key)) for key in ('state', 'status')):
        return True
    next_hops = row.get('nexthopStatus')
    return (isinstance(next_hops, list) and index < len(next_hops)
            and isinstance(next_hops[index], dict)
            and route_is_down(next_hops[index].get('nexthopStatus')))


def _text_supports(line, prefix, address, name, vrf, state=None):
    networks, addresses = set(), set()
    for token in re.findall(r'[0-9A-Fa-f:.]+(?:/\d+)?', line):
        try:
            if '/' in token:
                networks.add(ip_network(token, strict=False))
            else:
                addresses.add(ip_address(token))
        except ValueError:
            continue
    if prefix not in networks or address not in addresses:
        return False
    if route_is_down(state) and not re.search(r'\b(?:down|invalid|inactive|disabled)\b', line, re.I):
        return False
    if name and not re.search(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])', line):
        return False
    if _normalize_table(vrf) == 'default':
        explicit = re.findall(r'\b(?:table|vrf)\s+([\w.:-]+)', line, re.I)
        return all(_normalize_table(value) == 'default' for value in explicit)
    # Numeric table identities require an explicit table in that same line.
    if str(vrf).startswith('table:'):
        return bool(re.search(r'\btable\s+' + re.escape(str(vrf)[6:]) + r'\b', line))
    if _normalize_table(vrf) != 'default':
        return bool(re.search(r'\bvrf\s+' + re.escape(str(vrf)) + r'(?![\w.-])', line))
    return True
