from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from services.network_topology.overflow import (
    DIRECT_ITEM_LIMIT,
    OverflowTokenError,
    page_bucket,
    plan_overflow,
)
from services.network_topology.projection import project_scope, search_canonical
from services.network_topology.relationships import RelationshipResult
from services.network_topology.scope_tree import ROOT_SCOPE_ID, ScopeTreeResult


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "network_topology"


def _scope(identifier, parent=None, *, name=None, summary=None):
    return {
        "id": identifier,
        "parent_scope_id": parent,
        "scope_type": "organization" if parent is None else "site",
        "name": name or identifier,
        "summary": summary
        or {
            "key_device_count": 0,
            "network_segment_count": 0,
            "child_scope_count": 0,
            "cross_scope_relation_count": 0,
        },
        "provenance": [],
    }


def _entity(
    identifier, scope_id, *, kind="device", importance="normal", memberships=()
):
    return {
        "id": identifier,
        "scope_id": scope_id,
        "scope_memberships": list(memberships),
        "kind": kind,
        "name": identifier,
        "role": "network_device",
        "representation": "network_segment"
        if kind == "network_segment"
        else "network_device",
        "importance": importance,
        "importance_reasons": [],
        "attributes": {},
        "provenance": [],
    }


def _relation(identifier, left, right, *, layer="l2", relation_type="l2_trunk"):
    return {
        "id": identifier,
        "multi_edge_identity": {
            "endpoint_key": "|".join(sorted((left, right))),
            "layer": layer,
            "relation_type": relation_type,
            "attachment_key": identifier,
            "context_key": "",
        },
        "relation_type": relation_type,
        "layer": layer,
        "direction": "bidirectional",
        "endpoints": [
            {"ref_type": "entity", "ref_id": left},
            {"ref_type": "entity", "ref_id": right},
        ],
        "owner_scope_id": ROOT_SCOPE_ID,
        "attributes": {},
        "member_fact_ids": [identifier.replace("relation:", "fact:")],
        "member_relation_ids": [],
        "configured_state": "present",
        "operational_state": "up",
        "provenance": [],
    }


def _tree(scopes, entities):
    return ScopeTreeResult(ROOT_SCOPE_ID, tuple(scopes), tuple(entities), (), (), ())


def _relations(*items):
    return RelationshipResult(tuple(items), ())


def test_root_hides_descendant_devices_and_drill_down_restores_entities():
    site = "scope:site:east"
    zone = "scope:site:east-zone"
    scopes = [_scope(ROOT_SCOPE_ID), _scope(site, ROOT_SCOPE_ID), _scope(zone, site)]
    entities = [
        _entity("device:core", ROOT_SCOPE_ID, importance="critical"),
        _entity("device:distribution", site, importance="important"),
        _entity("device:leaf", zone),
        _entity("device:firewall", site, memberships=(zone,)),
    ]
    relations = _relations(
        _relation("relation:core-leaf", "device:core", "device:leaf"),
        _relation("relation:dist-leaf", "device:distribution", "device:leaf"),
        _relation(
            "relation:firewall-leaf",
            "device:firewall",
            "device:leaf",
            layer="security",
            relation_type="security_boundary",
        ),
    )

    root = project_scope(_tree(scopes, entities), relations, ROOT_SCOPE_ID)
    root_ids = {item["id"] for item in root.nodes}
    assert "device:core" in root_ids
    assert f"scope-ref:{site}" in root_ids
    assert "device:distribution" not in root_ids
    assert "device:leaf" not in root_ids
    assert next(iter(root.edges))["member_relation_ids"] == ["relation:core-leaf"]

    site_view = project_scope(_tree(scopes, entities), relations, site)
    site_ids = {item["id"] for item in site_view.nodes}
    assert {"device:distribution", "device:firewall"} <= site_ids
    assert "device:leaf" not in site_ids
    assert f"scope-ref:{zone}" in site_ids

    zone_view = project_scope(_tree(scopes, entities), relations, zone)
    zone_ids = {item["id"] for item in zone_view.nodes}
    assert "device:leaf" in zone_ids
    assert f"entity-ref:{zone}:device:firewall" in zone_ids
    boundary = next(
        item for item in zone_view.edges if item["relation_type"] == "security_boundary"
    )
    assert boundary["count"] == 1


def test_aggregate_edge_id_and_members_are_order_independent():
    child = "scope:site:branch"
    scopes = [_scope(ROOT_SCOPE_ID), _scope(child, ROOT_SCOPE_ID)]
    entities = [
        _entity("device:core", ROOT_SCOPE_ID),
        _entity("device:a", child),
        _entity("device:b", child),
    ]
    first = _relation("relation:z", "device:core", "device:a")
    second = _relation("relation:a", "device:core", "device:b")

    forward = project_scope(
        _tree(scopes, entities), _relations(first, second), ROOT_SCOPE_ID
    )
    reverse = project_scope(
        _tree(scopes, entities), _relations(second, first), ROOT_SCOPE_ID
    )

    assert forward.edges == reverse.edges
    edge = forward.edges[0]
    assert edge["id"].startswith("aggregate-edge:")
    assert edge["count"] == 2
    assert edge["member_relation_ids"] == ["relation:a", "relation:z"]
    assert edge["summary"] == {"configured": {"present": 2}, "operational": {"up": 2}}


def test_overflow_boundary_is_exact_and_has_no_silent_hiding():
    scopes = [_scope(ROOT_SCOPE_ID)]
    exactly = [_entity(f"device:{index:03d}", ROOT_SCOPE_ID) for index in range(200)]
    normal = project_scope(_tree(scopes, exactly), _relations(), ROOT_SCOPE_ID)
    assert normal.direct_item_total == DIRECT_ITEM_LIMIT == 200
    assert len(normal.nodes) == 200
    assert normal.overflow is None

    over = [
        *exactly,
        _entity("network:overflow", ROOT_SCOPE_ID, kind="network_segment"),
    ]
    projected = project_scope(_tree(scopes, over), _relations(), ROOT_SCOPE_ID)
    assert projected.direct_item_total == 201
    assert projected.overflow["total_count"] == 201
    assert sum(item["total_count"] for item in projected.buckets) == 201
    assert {item["category"] for item in projected.buckets} == {
        "device:normal",
        "network_segment:normal",
    }
    assert all(item["projection_kind"] == "view_bucket" for item in projected.nodes)


def test_mixed_category_bucket_ids_and_pages_survive_input_reordering():
    items = [
        _entity(f"device:normal:{index:03d}", ROOT_SCOPE_ID) for index in range(205)
    ]
    items.extend(
        _entity(f"device:critical:{index:03d}", ROOT_SCOPE_ID, importance="critical")
        for index in range(3)
    )
    items.extend(
        _entity(f"network:{index:03d}", ROOT_SCOPE_ID, kind="network_segment")
        for index in range(4)
    )
    shuffled = deepcopy(items)
    random.Random(44).shuffle(shuffled)

    original = plan_overflow(ROOT_SCOPE_ID, items)
    reordered = plan_overflow(ROOT_SCOPE_ID, shuffled)
    assert original.buckets == reordered.buckets
    bucket = next(item for item in original.buckets if item.category == "device:normal")

    first = page_bucket(ROOT_SCOPE_ID, items, bucket.id, bucket.continuation_token)
    repeated = page_bucket(
        ROOT_SCOPE_ID, shuffled, bucket.id, bucket.continuation_token
    )
    assert first.items == repeated.items
    assert first.continuation_token == repeated.continuation_token
    assert len(first.items) == 200
    second = page_bucket(ROOT_SCOPE_ID, shuffled, bucket.id, first.continuation_token)
    assert len(second.items) == 5
    assert second.continuation_token is None
    assert (
        len({item["id"] for item in (*first.items, *second.items)})
        == bucket.total_count
    )


def test_continuation_token_is_bound_to_scope_bucket_and_membership():
    items = [_entity(f"device:{index:03d}", ROOT_SCOPE_ID) for index in range(201)]
    plan = plan_overflow(ROOT_SCOPE_ID, items)
    bucket = plan.buckets[0]

    with pytest.raises(OverflowTokenError):
        page_bucket(
            "scope:organization:other", items, bucket.id, bucket.continuation_token
        )
    changed = [*items[:-1], _entity("device:replacement", ROOT_SCOPE_ID)]
    with pytest.raises(OverflowTokenError):
        page_bucket(ROOT_SCOPE_ID, changed, bucket.id, bucket.continuation_token)


def test_exactly_four_hundred_bucket_members_produce_two_full_pages():
    items = [_entity(f"device:{index:03d}", ROOT_SCOPE_ID) for index in range(400)]
    bucket = plan_overflow(ROOT_SCOPE_ID, items).buckets[0]

    first = page_bucket(ROOT_SCOPE_ID, items, bucket.id, bucket.continuation_token)
    second = page_bucket(ROOT_SCOPE_ID, items, bucket.id, first.continuation_token)

    assert len(first.items) == len(second.items) == 200
    assert first.continuation_token is not None
    assert second.continuation_token is None


def test_malformed_and_cross_bucket_tokens_are_rejected():
    items = [
        *[_entity(f"device:{index:03d}", ROOT_SCOPE_ID) for index in range(201)],
        _entity("network:a", ROOT_SCOPE_ID, kind="network_segment"),
    ]
    plan = plan_overflow(ROOT_SCOPE_ID, items)
    device_bucket = next(
        item for item in plan.buckets if item.category == "device:normal"
    )
    segment_bucket = next(
        item for item in plan.buckets if item.category == "network_segment:normal"
    )

    with pytest.raises(OverflowTokenError):
        page_bucket(ROOT_SCOPE_ID, items, device_bucket.id, "not-base64")
    with pytest.raises(OverflowTokenError):
        page_bucket(
            ROOT_SCOPE_ID,
            items,
            segment_bucket.id,
            device_bucket.continuation_token,
        )


def test_global_search_bypasses_bucket_and_locates_canonical_item():
    entities = [_entity(f"device:{index:03d}", ROOT_SCOPE_ID) for index in range(201)]
    entities[-1]["name"] = "Needle Router"
    tree = _tree([_scope(ROOT_SCOPE_ID)], entities)
    projection = project_scope(tree, _relations(), ROOT_SCOPE_ID)
    assert all(item.get("ref_id") != "device:200" for item in projection.nodes)

    matches = search_canonical(tree, "needle")

    assert len(matches) == 1
    match = matches[0]
    assert match["canonical_id"] == "device:200"
    assert match["scope_id"] == ROOT_SCOPE_ID
    assert match["bucket_id"] == projection.buckets[0]["id"]


def test_global_search_covers_scopes_segments_and_memberships():
    child = "scope:site:needle-site"
    scopes = [_scope(ROOT_SCOPE_ID), _scope(child, ROOT_SCOPE_ID, name="Needle Site")]
    entities = [
        _entity("network:needle-segment", child, kind="network_segment"),
        _entity("device:member", ROOT_SCOPE_ID, memberships=(child,)),
    ]
    tree = _tree(scopes, entities)

    scope_match = search_canonical(tree, "Needle Site")[0]
    segment_match = search_canonical(tree, "needle-segment")[0]
    member_match = search_canonical(tree, "device:member")[0]

    assert scope_match["kind"] == "scope"
    assert segment_match["kind"] == "network_segment"
    assert segment_match["scope_id"] == child
    assert member_match["scope_id"] == ROOT_SCOPE_ID


def test_overflow_aggregated_edges_never_reference_hidden_nodes():
    child = "scope:site:overflow"
    scopes = [_scope(ROOT_SCOPE_ID), _scope(child, ROOT_SCOPE_ID)]
    entities = [
        *[_entity(f"device:{index:03d}", ROOT_SCOPE_ID) for index in range(201)],
        _entity("device:child", child),
    ]
    relation = _relation("relation:cross", "device:000", "device:child")

    result = project_scope(_tree(scopes, entities), _relations(relation), ROOT_SCOPE_ID)
    node_ids = {item["id"] for item in result.nodes}
    endpoint_ids = {
        endpoint["ref_id"] for edge in result.edges for endpoint in edge["endpoints"]
    }

    assert endpoint_ids <= node_ids


@pytest.mark.parametrize("case_name", ["adc_lb", "cisco", "fortinet", "h3c", "huawei"])
def test_projection_preserves_frozen_golden_scope_summaries(case_name):
    case = json.loads((FIXTURE_ROOT / case_name / "full-golden-v1.json").read_text())
    graph = case["expected_presentation_graph"]
    tree = _tree(graph["scopes"], graph["entities"])
    result = project_scope(
        tree, RelationshipResult(tuple(graph["relations"]), ()), ROOT_SCOPE_ID
    )

    expected = next(item for item in graph["scopes"] if item["id"] == ROOT_SCOPE_ID)
    assert result.current_scope["summary"] == expected["summary"]


def test_projection_objects_never_mutate_canonical_graph():
    child = "scope:site:child"
    scopes = [_scope(ROOT_SCOPE_ID), _scope(child, ROOT_SCOPE_ID)]
    entities = [_entity("device:child", child)]
    tree = _tree(scopes, entities)
    before = deepcopy(tree.as_graph())

    result = project_scope(tree, _relations(), ROOT_SCOPE_ID)

    assert any(item["projection_kind"] == "scope_ref" for item in result.nodes)
    assert tree.as_graph() == before
    canonical_ids = {item["id"] for item in tree.entities}
    assert not any(
        identifier.startswith(("entity-ref:", "scope-ref:", "view-bucket:"))
        for identifier in canonical_ids
    )
