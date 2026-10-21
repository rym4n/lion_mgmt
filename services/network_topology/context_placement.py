"""Evidence-bound network context membership used only for presentation."""
from collections import defaultdict
from ipaddress import ip_interface, ip_network


def context_type(fact):
    family = fact.get("fact_family")
    return fact.get("attributes", {}).get("context_type") if family == "network_context" else family


def context_memberships(facts):
    """Resolve exact fact references; matching CIDRs never crosses a device."""
    contexts = {item["id"]: item for item in facts.get("network_contexts", ())
                if context_type(item) in {"zone", "vrf", "vlan"}}
    interfaces = {item["id"]: item for item in facts.get("interfaces", ())}
    devices = {item["id"] for item in facts.get("devices", ())}
    segments = {item["id"]: item for item in facts.get("segments", ())}
    members = defaultdict(set)

    def bind(member_id, context_id):
        context = contexts.get(context_id)
        if context is None:
            return
        owner = context.get("attributes", {}).get("owner_id")
        member = interfaces.get(member_id) or segments.get(member_id)
        device_id = member.get("attributes", {}).get("device_id") if member else member_id
        if member_id not in interfaces and member_id not in segments and member_id not in devices:
            return
        if owner and device_id != owner:
            return
        members[member_id].add(context_id)

    for identifier, interface in interfaces.items():
        attrs = interface.get("attributes", {})
        bind(identifier, attrs.get("context_id"))
        bind(identifier, attrs.get("vrf_id"))
    for membership in facts.get("memberships", ()):
        attrs = membership.get("attributes", {})
        bind(attrs.get("member_id"), attrs.get("container_id"))
    for boundary in facts.get("security_boundaries", ()):
        attrs = boundary.get("attributes", {})
        bind(attrs.get("interface_id"), attrs.get("zone_id"))
    for identifier, context in contexts.items():
        for interface_id in context.get("attributes", {}).get("interface_ids", ()):
            bind(interface_id, identifier)
    for identifier, segment in segments.items():
        attrs = segment.get("attributes", {})
        bind(identifier, attrs.get("context_id"))
        bind(identifier, attrs.get("vrf_id"))
        try:
            prefix = ip_network(attrs.get("canonical_cidr"), strict=False)
        except (ValueError, TypeError):
            continue
        explicit_contexts = frozenset(members.get(identifier, ()))
        for interface_id, interface in interfaces.items():
            interface_attrs = interface.get("attributes", {})
            if interface_attrs.get("device_id") != attrs.get("device_id"):
                continue
            if not compatible_contexts(attrs, interface_attrs, contexts,
                                       explicit_contexts, members.get(interface_id, ())):
                continue
            if any(_same_network(value, prefix) for value in interface_attrs.get("addresses", ())):
                members[identifier].update(members.get(interface_id, ()))
    for identifier, interface in interfaces.items():
        device_id = interface.get("attributes", {}).get("device_id")
        if device_id in devices:
            members[device_id].update(members.get(identifier, ()))
    for identifier, segment in segments.items():
        device_id = segment.get("attributes", {}).get("device_id")
        if device_id in devices:
            members[device_id].update(members.get(identifier, ()))
    return {key: tuple(sorted(values)) for key, values in members.items() if values}


def compatible_contexts(left, right, contexts=None, left_ids=(), right_ids=()):
    """Known contexts of the same kind must agree before address-based binding."""
    for key in ("context_id", "vrf_id"):
        if left.get(key) and right.get(key) and left[key] != right[key]:
            return False
    if contexts:
        for kind in ("zone", "vrf", "vlan"):
            left_kind = {value for value in left_ids if context_type(contexts[value]) == kind}
            right_kind = {value for value in right_ids if context_type(contexts[value]) == kind}
            if left_kind and right_kind and not left_kind.intersection(right_kind):
                return False
    return True


def _same_network(value, prefix):
    try:
        return ip_interface(value).network == prefix
    except (ValueError, TypeError):
        return False
