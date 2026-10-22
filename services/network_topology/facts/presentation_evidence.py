"""Require optional presentation metadata in the same bound fact record."""
import re

INTERFACE_METADATA = ('interface_type', 'media_type', 'speed_bps', 'description', 'mtu', 'vlan_ids')
ADJACENCY_METADATA = ('media_type', 'speed_bps')


def supports_metadata(family, item, resolved):
    if family == 'devices':
        expected = item.get('location')
        if not expected:
            return True
        return any(all(_contains(record, text) for text in expected.values())
                   for value in resolved for record in _atomic_records(value))
    if family == 'network_contexts':
        expected = {key: value for key, value in item.get('namespace_attributes', {}).items()
                    if key in {'vlan', 'vni'}}
        return not expected or any(all(_supports_field(record, key, text) for key, text in expected.items())
                                   for value in resolved for record in _atomic_records(value))
    keys = INTERFACE_METADATA if family == 'interfaces' else ADJACENCY_METADATA if family in {'adjacencies', 'physical_record'} else ()
    expected = {key: item[key] for key in keys if key in item and item[key] is not None}
    if not expected:
        return True
    for value in resolved:
        records = _interface_records(value, item['raw_name']) if family == 'interfaces' else (value,)
        for record in records:
            if all(_supports_field(record, key, text) for key, text in expected.items()):
                return True
    return False


def _interface_records(value, name):
    """Use the nearest named port record, never its surrounding port list."""
    if isinstance(value, dict):
        identities = [child for key, child in value.items() if _key(key) in
            {'name', 'rawname', 'canonicalname', 'ifname', 'ifdescr', 'interface',
             'interfacename', 'port', 'portname', 'localport'} and isinstance(child, str)]
        if any(_port(identity) == _port(name) for identity in identities):
            yield _without_other_ports(value, name)
        for key, child in value.items():
            if _port(key) == _port(name) and isinstance(child, dict):
                yield _without_other_ports(child, name)
            elif isinstance(child, (dict, list)):
                yield from _interface_records(child, name)
    elif isinstance(value, list):
        for child in value:
            yield from _interface_records(child, name)
    elif isinstance(value, str):
        anchors = r'(?im)(?=^\s*(?:interface|port)\s*[:=]\s*\S+)'
        blocks = re.split(anchors, value) if re.search(anchors, value) else value.splitlines()
        for block in blocks:
            if _contains(block, name):
                yield block


def _atomic_records(value):
    if isinstance(value, dict):
        scalars = {key: child for key, child in value.items() if not isinstance(child, (dict, list))}
        if scalars:
            yield scalars
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _atomic_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _atomic_records(child)
    elif isinstance(value, str):
        yield value


def _without_other_ports(value, name):
    if isinstance(value, dict):
        identities = [child for key, child in value.items() if _key(key) in
            {'name', 'rawname', 'canonicalname', 'ifname', 'ifdescr', 'interface',
             'interfacename', 'port', 'portname', 'localport'} and isinstance(child, str)]
        if identities and not any(_port(identity) == _port(name) for identity in identities):
            return {}
        return {key: _without_other_ports(child, name) for key, child in value.items()}
    if isinstance(value, list):
        return [_without_other_ports(child, name) for child in value]
    return value


def _key(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _port(value):
    text = re.sub(r'\s+', '', str(value)).lower()
    for prefixes, expanded in ((('gigabitethernet', 'gi', 'ge'), 'gigabitethernet'),
                               (('tengigabitethernet', 'te'), 'tengigabitethernet'),
                               (('ethernet', 'eth'), 'ethernet')):
        for prefix in prefixes:
            if text.startswith(prefix) and text[len(prefix):len(prefix)+1].isdigit():
                return expanded + text[len(prefix):]
    return text


def _supports_field(value, key, expected):
    if key == 'speed_bps':
        return _supports_speed(value, expected)
    aliases = {'interface_type': {'type', 'interfacetype', 'iftype', 'linktype'},
               'media_type': {'media', 'mediatype', 'medium', 'physicalmedium', 'portmedia', 'porttype'},
               'description': {'description', 'desc', 'ifalias', 'alias'},
               'mtu': {'mtu', 'ipmtu'},
               'vlan_ids': {'vlan', 'vlans', 'vlanid', 'vlanids', 'pvid', 'accessvlan', 'taggedvlans', 'untaggedvlan'},
               'vlan': {'vlan', 'vlanid'}, 'vni': {'vni', 'vxlanid'}}
    if key in aliases:
        if isinstance(value, dict):
            if key in {'interface_type', 'media_type', 'description'}:
                return any((_supports_interface_type(child, expected) if key == 'interface_type'
                            else _contains(child, expected)) for field, child in value.items()
                           if _key(field) in aliases[key] and not isinstance(child, (dict, list))) or any(
                               _supports_field(child, key, expected) for child in value.values()
                               if isinstance(child, (dict, list)))
            return any(_contains(child, expected) if not isinstance(expected, list)
                       else all(_contains(child, item) for item in expected)
                       for field, child in value.items() if _key(field) in aliases[key]) or any(
                           _supports_field(child, key, expected) for child in value.values()
                           if isinstance(child, (dict, list)))
        if isinstance(value, list):
            return any(_supports_field(child, key, expected) for child in value)
        if isinstance(value, str):
            if key in {'interface_type', 'media_type', 'description'}:
                return _supports_interface_type(value, expected) if key == 'interface_type' else _contains(value, expected)
            expected_values = expected if isinstance(expected, list) else [expected]
            names = '|'.join(re.escape(alias) for alias in aliases[key])
            return all(re.search(r'\b(?:' + names + r')\s*[:= ]\s*' + re.escape(str(item)) + r'(?!\d)', value, re.I)
                       for item in expected_values)
        return False
    if isinstance(expected, list):
        return all(_contains(value, item) for item in expected)
    return _contains(value, expected)


def _contains(value, expected):
    if isinstance(value, dict):
        return any(_contains(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains(child, expected) for child in value)
    if type(expected) is int:
        return type(value) in (int, float) and value == expected or (
            isinstance(value, str) and bool(re.search(r'(?<!\d)' + str(expected) + r'(?!\d)', value)))
    if isinstance(value, str):
        return bool(re.search(r'(?<![\w.-])' + re.escape(str(expected)) + r'(?![\w.-])', value, re.I))
    return value == expected


def _supports_interface_type(value, expected):
    source = str(value).strip().lower()
    target = str(expected).strip().lower()
    if source == target:
        return True
    aliases = {
        'physicalif': {'physical'},
        'channelif': {'aggregate', 'aggregation', 'lag'},
        'aggrif': {'aggregate', 'aggregation', 'lag'},
        'vlanif': {'vlan', 'logical'},
        'vpntun': {'tunnel'},
    }
    return target in aliases.get(source, ())


def _supports_speed(value, expected):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r'[^a-z]', '', key.lower())
            if normalized in {'speed', 'speedbps', 'bandwidth', 'ifspeed'}:
                explicit_units = normalized in {'speedbps', 'ifspeed'} or isinstance(child, str) and bool(re.search(r'[a-z]', child, re.I))
                if explicit_units and _speed(child) == expected:
                    return True
            if isinstance(child, (dict, list)) and _supports_speed(child, expected):
                return True
    elif isinstance(value, list):
        return any(_supports_speed(child, expected) for child in value)
    elif isinstance(value, str):
        return any(_speed(token) == expected for token in re.findall(r'\d+(?:\.\d+)?\s*[kKmMgGtT](?:b(?:it)?/?s|bps)?', value))
    return False


def _speed(value):
    if type(value) in (int, float):
        return value
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:b(?:it)?/?s|bps)?\s*', str(value), re.I)
    return float(match[1]) * 1000 ** ('kmgt'.find(match[2].lower()) + 1) if match else None
