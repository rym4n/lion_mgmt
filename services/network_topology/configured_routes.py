"""Configured next-hop boundaries; these are not discovered peer devices."""
from collections import defaultdict
from ipaddress import ip_address, ip_network
from urllib.parse import quote


def configured_routes(facts):
    interfaces = {item["id"]: item for item in facts.get("interfaces", ())}
    devices = {item["id"] for item in facts.get("devices", ())}
    groups = defaultdict(list)
    for route in facts.get("routes", ()):
        attrs = route.get("attributes", {})
        interface = interfaces.get(attrs.get("interface_id"))
        if attrs.get("route_type") not in {"static", "default"} or interface is None:
            continue
        owner = interface.get("attributes", {}).get("device_id")
        if owner not in devices:
            continue
        try:
            address = ip_address(attrs.get("next_hop_address") or "")
            destination = ip_network(attrs["prefix"], strict=False)
        except (ValueError, KeyError):
            continue
        if (address.is_unspecified or address.is_multicast or address.is_loopback
                or address.version != destination.version):
            continue
        context = str(attrs.get("vrf_id") or "default")
        identifier = "next-hop:" + ":".join(quote(value, safe="-._~") for value in
                                            (owner, context, attrs["interface_id"], str(address)))
        groups[(identifier, owner, str(address), attrs["interface_id"])].append(route)
    return tuple((key, tuple(sorted(values, key=lambda item: item["id"])))
                 for key, values in sorted(groups.items()))


def route_attributes(address, interface, routes):
    name = interface.get("attributes", {}).get("canonical_name")
    return {"next_hop_ip": address,
            "interface_names": [name] if name else [],
            "route_prefixes": sorted({str(ip_network(item["attributes"]["prefix"], strict=False)) for item in routes}),
            "route_count": len(routes)}
