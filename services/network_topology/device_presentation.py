"""Safe, evidence-backed device and interface presentation fields."""
from copy import deepcopy

INTERFACE_FIELDS = ('interface_type', 'media_type', 'speed_bps', 'description', 'mtu', 'vlan_ids')


def interface_presentations(facts, device_id):
    provenance = {item['id']: item['provenance'] for item in facts.get('provenance_catalog', ())}
    output = []
    for interface in sorted(facts.get('interfaces', ()), key=lambda item: item['id']):
        attrs = interface.get('attributes', {})
        if attrs.get('device_id') != device_id:
            continue
        output.append({'id': interface['id'],
            'name': attrs.get('canonical_name') or attrs.get('raw_name') or interface['id'],
            'addresses': list(attrs.get('addresses', ())),
            'configured_state': attrs.get('configured_state') or 'unknown',
            'operational_state': attrs.get('operational_state') or 'unknown',
            **{key: deepcopy(attrs[key]) for key in INTERFACE_FIELDS if key in attrs},
            'provenance': [deepcopy(provenance[key]) for key in interface.get('provenance_ids', ()) if key in provenance]})
    return output
