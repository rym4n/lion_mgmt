from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from services.network_topology.identity import (
    IdentityResolution,
    LogicalDeviceGroup,
)
from services.network_topology.relationships import build_relationships
from services.network_topology.scope_tree import ROOT_SCOPE_ID, ScopeTreeResult


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "network_topology"


def _resolution(mapping, *, groups=()):
    return IdentityResolution((), tuple(groups), (), (), dict(mapping))


def _scope_result(entities, *, scopes=None, collapsed=()):
    scope_items = scopes or [_scope(ROOT_SCOPE_ID, None)]
    return ScopeTreeResult(
        ROOT_SCOPE_ID,
        tuple(scope_items),
        tuple(entities),
        (),
        (),
        tuple(collapsed),
    )


def _scope(identifier, parent):
    return {
        "id": identifier,
        "parent_scope_id": parent,
        "scope_type": "organization" if parent is None else "region",
        "name": identifier,
        "summary": {},
        "provenance": [],
    }


def _entity(identifier, scope_id=ROOT_SCOPE_ID):
    return {"id": identifier, "scope_id": scope_id}


def _fact(identifier, family, attributes, provenance="p:relation"):
    return {
        "id": identifier,
        "fact_family": family,
        "attributes": attributes,
        "provenance_ids": [provenance],
    }


def _facts(**overrides):
    document = {
        "schema_version": "aioe.network-topology.facts/v1",
        "snapshot_id": "snapshot-relations",
        "devices": [],
        "device_groups": [],
        "interfaces": [],
        "segments": [],
        "adjacencies": [],
        "routing_relationships": [],
        "tunnels": [],
        "gateways": [],
        "security_boundaries": [],
        "wan_connections": [],
        "provenance_catalog": [
            {
                "id": "p:relation",
                "provenance": {
                    "type": "derived",
                    "rule_id": "test.relationship/v1",
                    "profile_revision": 1,
                    "member_ids": ["source"],
                },
            }
        ],
    }
    document.update(overrides)
    return document


def _interface(identifier, device_id, *, vlans=()):
    return _fact(
        identifier,
        "interface",
        {"device_id": device_id, "vlan_ids": list(vlans)},
    )


def _adjacency(identifier, local_id, remote_id, *, layer="l2", context=None):
    return _fact(
        identifier,
        "neighbor",
        {
            "neighbor_type": "lldp",
            "local_id": local_id,
            "remote_id": remote_id,
            "attachments": [local_id, f"remote:{identifier}"],
            "layer": layer,
            "context_id": context,
            "state": "up",
        },
    )


def test_adc_golden_relations_are_reproduced_without_projection_objects():
    case = json.loads((FIXTURE_ROOT / "adc_lb" / "full-golden-v1.json").read_text())
    graph = case["expected_presentation_graph"]
    group = LogicalDeviceGroup(
        "device-group:adc-cluster-1",
        "cluster",
        "SYN-ADC-CLUSTER-01",
        ("device:adc-lb-1", "device:adc-lb-2"),
        ("p-adc-cluster",),
    )
    resolution = _resolution(
        {
            "device:adc-lb-1": "device:adc-lb-1",
            "device:adc-lb-2": "device:adc-lb-2",
        },
        groups=(group,),
    )
    scope_tree = _scope_result(graph["entities"], scopes=graph["scopes"])

    result = build_relationships(case["expected_facts"], resolution, scope_tree)

    assert list(result.relations) == graph["relations"]
    assert result.unresolved == ()
    assert all(
        endpoint["ref_type"] == "entity"
        and not endpoint["ref_id"].startswith(("entity-ref:", "scope-ref:"))
        for relation in result.relations
        for endpoint in relation["endpoints"]
    )


def test_same_endpoints_keep_physical_l2_routing_vpn_and_wan_multiedges():
    mapping = {"device:a": "device:canonical:a", "device:b": "device:canonical:b"}
    facts = _multiedge_facts()
    scope_tree = _scope_result([_entity(item) for item in mapping.values()])

    result = build_relationships(facts, _resolution(mapping), scope_tree)

    assert {item["relation_type"] for item in result.relations} == {
        "physical_link",
        "l2_trunk",
        "routing_adjacency",
        "tunnel",
        "wan_connection",
    }
    identities = [
        tuple(item["multi_edge_identity"].values()) for item in result.relations
    ]
    assert len(identities) == len(set(identities)) == 5
    assert {
        item["multi_edge_identity"]["endpoint_key"] for item in result.relations
    } == {"device:canonical:a|device:canonical:b"}
    tunnel = next(
        item for item in result.relations if item["relation_type"] == "tunnel"
    )
    trunk = next(
        item for item in result.relations if item["relation_type"] == "l2_trunk"
    )
    assert "vpn-7" in tunnel["multi_edge_identity"]["attachment_key"]
    assert "vrf:blue" in trunk["multi_edge_identity"]["context_key"]
    assert "vlan:10" in trunk["multi_edge_identity"]["context_key"]


def _multiedge_facts():
    return _facts(
        interfaces=[
            _interface("interface:a:p1", "device:a"),
            _interface("interface:a:p2", "device:a", vlans=("vlan:10",)),
        ],
        adjacencies=[
            _adjacency(
                "neighbor:physical",
                "interface:a:p1",
                "device:b",
                layer="physical",
                context="vrf:blue",
            ),
            _adjacency(
                "neighbor:trunk",
                "interface:a:p2",
                "device:b",
                layer="l2",
                context="vrf:blue",
            ),
        ],
        routing_relationships=[_routing_fact()],
        tunnels=[_tunnel_fact()],
        wan_connections=[_wan_fact()],
    )


def _routing_fact():
    return _fact(
        "routing:bgp",
        "routing_relationship",
        {
            "protocol": "bgp",
            "local_id": "device:a",
            "remote_id": "device:b",
            "vrf_id": "vrf:blue",
            "session_state": "up",
        },
    )


def _tunnel_fact():
    return _fact(
        "tunnel:vpn-7",
        "tunnel",
        {
            "tunnel_type": "ipsec",
            "tunnel_id": "vpn-7",
            "context_id": "vrf:blue",
            "interface_id": None,
            "local_endpoint": "device:a",
            "remote_endpoint": "device:b",
            "protected_prefixes": [],
            "state": "up",
        },
    )


def _wan_fact():
    return _fact(
        "wan:circuit-9",
        "wan",
        {
            "connection_type": "circuit",
            "local_id": "device:a",
            "remote_id": "device:b",
            "circuit_id": "circuit-9",
            "state": "up",
        },
    )


def test_cross_region_relation_owner_is_endpoint_scope_lca():
    left_scope = "scope:region:north"
    right_scope = "scope:region:south"
    scopes = [
        _scope(ROOT_SCOPE_ID, None),
        _scope(left_scope, ROOT_SCOPE_ID),
        _scope(right_scope, ROOT_SCOPE_ID),
    ]
    mapping = {"device:a": "device:a", "device:b": "device:b"}
    facts = _facts(
        wan_connections=[
            _fact(
                "wan:cross-region",
                "wan",
                {
                    "connection_type": "circuit",
                    "local_id": "device:a",
                    "remote_id": "device:b",
                    "circuit_id": "cross-region",
                    "state": "up",
                },
            )
        ]
    )
    tree = _scope_result(
        [_entity("device:a", left_scope), _entity("device:b", right_scope)],
        scopes=scopes,
    )

    relation = build_relationships(facts, _resolution(mapping), tree).relations[0]

    assert relation["owner_scope_id"] == ROOT_SCOPE_ID
    assert relation["member_fact_ids"] == ["wan:cross-region"]


def test_collapsed_path_is_traceable_and_deterministic():
    facts, resolution, tree = _collapsed_path_case()

    first = build_relationships(facts, resolution, tree)
    shuffled = deepcopy(facts)
    shuffled["interfaces"].reverse()
    shuffled["adjacencies"].reverse()
    second = build_relationships(shuffled, resolution, tree)

    assert first == second
    assert first.unresolved == ()
    assert len(first.relations) == 1
    path = first.relations[0]
    assert path["relation_type"] == "collapsed_path"
    assert path["relation_type"] != "physical_link"
    assert path["owner_scope_id"] == ROOT_SCOPE_ID
    assert path["member_fact_ids"] == [
        "device:access",
        "interface:access:p1",
        "neighbor:access:left",
        "interface:access:p2",
        "neighbor:access:right",
    ]
    assert path["attributes"]["ordered_member_fact_ids"] == path["member_fact_ids"]


def test_cross_region_collapse_uses_aggregated_connectivity_with_lca_owner():
    facts, resolution, tree = _collapsed_path_case(cross_region=True)

    relation = build_relationships(facts, resolution, tree).relations[0]

    assert relation["relation_type"] == "aggregated_connectivity"
    assert relation["owner_scope_id"] == ROOT_SCOPE_ID
    assert (
        relation["attributes"]["ordered_member_fact_ids"] == relation["member_fact_ids"]
    )


def test_collapsing_requires_consistent_layer_context_vrf_and_vlan():
    facts, resolution, tree = _collapsed_path_case()
    facts["adjacencies"][1]["attributes"]["context_id"] = "vrf:green"

    result = build_relationships(facts, resolution, tree)

    assert result.relations == ()
    assert [item.reason_code for item in result.unresolved] == [
        "COLLAPSE_PATH_INCOMPATIBLE"
    ]


@pytest.mark.parametrize(
    ("case_factory", "reason_code"),
    [("branch", "COLLAPSE_PATH_BRANCH"), ("cycle", "COLLAPSE_PATH_CYCLE")],
)
def test_collapsing_branch_or_cycle_fails_closed(case_factory, reason_code):
    facts, resolution, tree = _ambiguous_collapse_case(case_factory)

    result = build_relationships(facts, resolution, tree)

    assert not result.relations
    assert [item.reason_code for item in result.unresolved] == [reason_code]


def _collapsed_path_case(*, cross_region=False):
    left_scope = "scope:region:left"
    right_scope = "scope:region:right"
    scopes = [
        _scope(ROOT_SCOPE_ID, None),
        _scope(left_scope, ROOT_SCOPE_ID),
        _scope(right_scope, ROOT_SCOPE_ID),
    ]
    interfaces = [
        _interface("interface:access:p1", "device:access", vlans=("vlan:20",)),
        _interface("interface:access:p2", "device:access", vlans=("vlan:20",)),
    ]
    facts = _facts(
        interfaces=interfaces,
        adjacencies=[
            _adjacency(
                "neighbor:access:left",
                "interface:access:p1",
                "device:left",
                context="vrf:blue",
            ),
            _adjacency(
                "neighbor:access:right",
                "interface:access:p2",
                "device:right",
                context="vrf:blue",
            ),
        ],
    )
    mapping = {
        "device:access": "device:access",
        "device:left": "device:left",
        "device:right": "device:right",
    }
    endpoint_scopes = _endpoint_scopes(cross_region, left_scope, right_scope)
    tree = _scope_result(
        [
            _entity("device:left", endpoint_scopes[0]),
            _entity("device:right", endpoint_scopes[1]),
        ],
        scopes=scopes,
        collapsed=("device:access",),
    )
    return facts, _resolution(mapping), tree


def _endpoint_scopes(cross_region, left_scope, right_scope):
    if cross_region:
        return left_scope, right_scope
    return ROOT_SCOPE_ID, ROOT_SCOPE_ID


def _ambiguous_collapse_case(kind):
    if kind == "branch":
        collapsed = ("device:access",)
        links = [
            ("access", "p1", "left"),
            ("access", "p2", "right"),
            ("access", "p3", "third"),
        ]
        visible = ("left", "right", "third")
    else:
        collapsed = ("device:x", "device:y", "device:z")
        links = [("x", "p1", "y"), ("y", "p1", "z"), ("z", "p1", "x")]
        visible = ()
    interfaces = [
        _interface(f"interface:{local}:{port}", f"device:{local}")
        for local, port, _ in links
    ]
    adjacencies = [
        _adjacency(
            f"neighbor:{local}:{remote}",
            f"interface:{local}:{port}",
            f"device:{remote}",
            context="vrf:blue",
        )
        for local, port, remote in links
    ]
    names = {item for link in links for item in (link[0], link[2])}
    mapping = {f"device:{item}": f"device:{item}" for item in names}
    tree = _scope_result(
        [_entity(f"device:{item}") for item in visible], collapsed=collapsed
    )
    return (
        _facts(interfaces=interfaces, adjacencies=adjacencies),
        _resolution(mapping),
        tree,
    )
