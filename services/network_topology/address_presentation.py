"""Stable address labels: private means RFC1918 or IPv6 ULA only."""
from ipaddress import ip_network

PRIVATE_NETWORKS = tuple(map(ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")))


def network_attributes(cidr):
    network = ip_network(cidr, strict=False)
    return {"network_prefixes": [str(network)], "ip_version": network.version,
            "prefix_length": network.prefixlen, "address_scope": _scope(network)}


def _scope(network):
    first, last = network.network_address, network.broadcast_address
    if network.num_addresses == 1 and first.is_unspecified:
        return "unspecified"
    for attribute, label in (("is_loopback", "loopback"), ("is_link_local", "link_local"),
                             ("is_multicast", "multicast")):
        if getattr(first, attribute) and getattr(last, attribute):
            return label
    if any(network.version == private.version and network.subnet_of(private)
           for private in PRIVATE_NETWORKS):
        return "private"
    if (first.is_global and last.is_global and not first.is_reserved
            and not last.is_reserved and not first.is_multicast and not last.is_multicast):
        return "public"
    return "reserved"
