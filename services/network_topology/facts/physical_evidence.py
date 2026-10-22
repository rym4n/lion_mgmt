"""Validate physical neighbor tuples within one observed LLDP/CDP record."""
import re
from ipaddress import ip_address

from services.network_topology.evidence_locator import resolve_evidence
from services.network_topology.facts.presentation_evidence import supports_metadata

LOCAL_PORT_KEYS = {'localport', 'localportid', 'localinterface', 'localintf', 'localif', 'interface', 'ifname'}
REMOTE_PORT_KEYS = {'remoteport', 'remoteportid', 'remoteinterface', 'remoteintf', 'portid', 'neighborport', 'neighborportid', 'port'}
PEER_KEYS = {
    'hostname': {'hostname', 'remotehostname', 'remotesystemname', 'systemname', 'deviceid', 'neighborname', 'neighbor', 'remotename', 'remotedevice'},
    'chassis_id': {'chassisid', 'remotechassisid', 'chassis'},
    'management_ip': {'managementip', 'managementaddress', 'remotemanagementaddress', 'remoteip'},
}


def supports_physical_adjacency(item, document, observations):
    interface = next((value for value in document['interfaces'] if value['candidate_id'] == item['local_candidate_ref']), None)
    if interface is None or item['kind'] not in {'lldp', 'cdp'}:
        return False
    local = item.get('local_port')
    remote = item.get('remote_port')
    peer = {key: item['remote_selector'][key] for key in PEER_KEYS if item['remote_selector'].get(key)}
    if not local or not remote or not peer or _port(local) not in {_port(interface['raw_name']), _port(interface['canonical_name'])}:
        return False
    if item['remote_selector'].get('interface') is not None and _port(item['remote_selector']['interface']) != _port(remote):
        return False
    by_id = {value['observation_id']: value for value in observations}
    for reference in item['evidence_refs']:
        observation = by_id[reference['observation_id']]
        evidence, protocol = _bound_record(observation['data'], reference['locator'])
        protocol = protocol or _protocol(str(observation.get('kind', '')) + ' ' + str(observation.get('command_or_api', '')))
        for record, record_protocol in _records(evidence, protocol):
            if isinstance(record, dict) and _structured(record, item['kind'], local, remote, peer, record_protocol):
                if supports_metadata('physical_record', item, [record]) and _supports_state(record, item.get('state')):
                    return True
            if isinstance(record, str) and record_protocol == item['kind']:
                for block in _matching_text_records(record, local, remote, peer):
                    if supports_metadata('physical_record', item, [block]) and _supports_state(block, item.get('state')):
                        return True
    return False


def _supports_state(record, expected):
    if not expected or str(expected).lower() == 'unknown':
        return True
    expected = str(expected).lower()
    if isinstance(record, dict):
        return any(str(value).strip().lower() == expected for key, value in record.items()
                   if _key(key) in {'state', 'status', 'linkstate', 'operstate', 'operationalstate'})
    return bool(re.search(r'\b(?:state|status)\s*[:=]\s*' + re.escape(expected) + r'\b', record, re.I))


def _key(value):
    return re.sub(r'[^a-z0-9]', '', value.lower())


def _protocol(value):
    text = str(value).replace('_', ' ')
    return next((kind for kind in ('lldp', 'cdp') if re.search(r'\b' + kind + r'\b', text, re.I)), None)


def _port(value):
    value = str(value).strip().lower()
    for alias, full in (('ge', 'gigabitethernet'), ('gi', 'gigabitethernet'),
                        ('eth', 'ethernet'), ('te', 'tengigabitethernet')):
        if re.match(alias + r'\d', value):
            return full + value[len(alias):]
    return value


def _flat(record):
    return {_key(key): value for key, value in record.items() if not isinstance(value, (dict, list))}


def _looks_like_record(record):
    keys = set(_flat(record))
    return bool(keys & LOCAL_PORT_KEYS and keys & REMOTE_PORT_KEYS and keys & set.union(*PEER_KEYS.values()))


def _bound_record(data, locator):
    value = resolve_evidence(data, locator)
    if locator['type'] != 'json_pointer':
        return value, None
    current, nearest = data, None
    protocol = _protocol(locator['pointer'])
    parts = locator['pointer'].split('/')[1:] if locator['pointer'] else []
    for part in parts:
        token = part.replace('~1', '/').replace('~0', '~')
        current = current[int(token)] if isinstance(current, list) else current[token]
        if isinstance(current, dict):
            protocol = _protocol(current.get('protocol')) or _protocol(current.get('command')) or protocol
        if isinstance(current, dict) and _looks_like_record(current):
            nearest = current
    return (nearest if nearest is not None else value), protocol


def _records(value, protocol=None):
    if isinstance(value, dict):
        protocol = _protocol(value.get('protocol')) or _protocol(value.get('command')) or protocol
        if _looks_like_record(value):
            yield value, protocol
        else:
            for key, child in value.items():
                yield from _records(child, _protocol(key) or protocol)
    elif isinstance(value, list):
        for child in value:
            yield from _records(child, protocol)
    elif isinstance(value, str):
        yield value, protocol


def _structured(record, kind, local, remote, peer, inherited_protocol):
    values = _flat(record)
    explicit = values.get('protocol') or values.get('neighbortype')
    if explicit:
        if str(explicit).lower() != kind:
            return False
    elif inherited_protocol != kind:
        return False
    if not any(_port(values.get(key)) == _port(local) for key in LOCAL_PORT_KEYS if key in values):
        return False
    if not any(_port(values.get(key)) == _port(remote) for key in REMOTE_PORT_KEYS if key in values):
        return False
    return all(any(_equal(values.get(alias), expected) for alias in PEER_KEYS[key]) for key, expected in peer.items())


def _equal(value, expected):
    if value is None:
        return False
    try:
        return ip_address(str(value)) == ip_address(str(expected))
    except ValueError:
        return str(value).strip().lower() == str(expected).strip().lower()


def _token(text, value):
    return bool(re.search(r'(?<![\w./:-])' + re.escape(value) + r'(?![\w./:-])', text, re.I))


def _text(text, local, remote, peer):
    return any(_matching_text_records(text, local, remote, peer))


def _matching_text_records(text, local, remote, peer):
    # A brief line is one neighbor. Detail records split at repeated local or
    # device headers, so a tuple cannot be assembled across neighbors.
    for line in text.splitlines():
        local_match = re.search(r'(?<![\w./:-])' + re.escape(local) + r'(?![\w./:-])', line, re.I)
        remote_match = re.search(r'(?<![\w./:-])' + re.escape(remote) + r'(?![\w./:-])', line, re.I)
        if (local_match and remote_match and local_match.start() < remote_match.start()
                and all(_token(line, value) for value in peer.values())):
            yield line
    anchors = r'(?=^\s*Local (?:Intf|Interface|Port)\s*[:=])'
    if not re.search(anchors, text, re.M | re.I):
        anchors = r'(?=^\s*Device ID\s*:)'
    blocks = re.split(anchors, text, flags=re.M | re.I)
    for block in blocks:
        if not block.strip():
            continue
        if (re.search(r'(?:Local (?:Intf|Interface|Port)|Interface)\s*[:=]\s*' + re.escape(local) + r'(?![\w./:-])', block, re.I)
                and re.search(r'(?:Port ID(?:\s*\(outgoing port\))?|Remote (?:Port|Interface))\s*[:=]\s*' + re.escape(remote) + r'(?![\w./:-])', block, re.I)
                and all(_token(block, value) for value in peer.values())):
            yield block
