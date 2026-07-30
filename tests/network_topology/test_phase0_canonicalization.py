from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from services.network_topology.canonicalization import (
    JCS_IMPLEMENTATION,
    MANIFEST_HASH_DOMAIN,
    XML_C14N_ALGORITHM_URI,
    XML_C14N_IMPLEMENTATION,
    CanonicalizationError,
    _canonicalize_xml_subset,
    canonicalize_and_hash_manifest,
    canonicalize_and_hash_snapshot,
    canonicalize_json,
    canonicalize_observation_data,
    canonicalize_text,
    canonicalize_xml,
    sha256_uri,
    validate_observation_hashes,
)
from services.network_topology.evidence_locator import (
    LocatorError,
    resolve_json_pointer,
    resolve_line_range,
    resolve_restricted_xpath,
)

# W3C Canonical XML 1.1 sections 3.3 and 3.4, reduced only by removing DTD data.
XML_C14N11_VECTORS = (
    (
        (
            '<doc><e1   /><e2 ></e2><e3 name = "elem3" id="elem3"/>'
            '<e5 a:attr="out" b:attr="sorted" attr2="all" attr="I\'m" '
            'xmlns:b="http://www.ietf.org" xmlns:a="http://www.w3.org" '
            'xmlns="http://example.org"/></doc>'
        ),
        (
            '<doc><e1></e1><e2></e2><e3 id="elem3" name="elem3"></e3>'
            '<e5 xmlns="http://example.org" xmlns:a="http://www.w3.org" '
            'xmlns:b="http://www.ietf.org" attr="I\'m" attr2="all" '
            'b:attr="sorted" a:attr="out"></e5></doc>'
        ),
    ),
    (
        (
            "<doc><text>First line&#x0d;&#10;Second line</text><value>&#x32;</value>"
            '<compute><![CDATA[value>"0" && value<"10" ?"valid":"error"]]>'
            '</compute><compute expr=\'value>"0" &amp;&amp; value&lt;"10" '
            '?"valid":"error"\'>valid</compute>'
            "<norm attr=' &apos;   &#x20;&#13;&#xa;&#9;   &apos; '/></doc>"
        ),
        (
            "<doc><text>First line&#xD;\nSecond line</text><value>2</value>"
            '<compute>value&gt;"0" &amp;&amp; value&lt;"10" ?"valid":"error"</compute>'
            '<compute expr="value>&quot;0&quot; &amp;&amp; value&lt;&quot;10&quot; '
            '?&quot;valid&quot;:&quot;error&quot;">valid</compute>'
            "<norm attr=\" '    &#xD;&#xA;&#x9;   ' \"></norm></doc>"
        ),
    ),
)
XML_C14N_VERSION_SPLIT_SOURCE = '<root xml:id="outer"><target/></root>'
XML_C14N_VERSION_SPLIT_XPATH = (
    "//target | //target/@* | //target/namespace::* | //target/node()"
)
GOLDEN_FILES = tuple(
    sorted(
        (Path(__file__).parents[1] / "fixtures" / "network_topology").glob(
            "*/full-golden-v1.json"
        )
    )
)
MANIFEST_FIELD_CHANGES = (
    (("manifest_id",), "manifest-2"),
    (("profile_revision",), 2),
    (("collection_sequence",), 2),
    (("base_graph_revision",), 1),
    (("base_publication_revision",), 1),
    (("issued_at",), "2026-07-28T00:00:01Z"),
    (("expires_at",), "2026-07-28T00:06:00Z"),
    (("expected_resources", 0, "resource_id"), "resource-y"),
    (("expected_resources", 0, "capability_profile"), "profile-2"),
    (("expected_resources", 0, "required_kinds"), ["system.identity"]),
    (("expected_resources", 0, "optional_kinds"), ["wan.sdwan"]),
)


def stored_snapshot() -> dict:
    return {
        "schema_version": "aioe.network-topology.collection/v2",
        "snapshot_id": "snapshot-中文",
        "collection_started_at": "2026-07-28T00:00:00Z",
        "collection_ended_at": "2026-07-28T00:01:00Z",
        "collector": {"run_id": "run-1", "playbook_id": "playbook-1"},
        "target_manifest": {
            "expected_resources": [
                {
                    "resource_id": "resource-z",
                    "required_kinds": ["system.identity", "interface.inventory"],
                    "optional_kinds": ["wan.sdwan", "overlay.tunnels"],
                },
                {
                    "resource_id": "resource-a",
                    "required_kinds": [],
                    "optional_kinds": [],
                },
                {
                    "resource_id": "resource-y",
                    "required_kinds": [],
                    "optional_kinds": [],
                },
            ]
        },
        "authoritative_context": authoritative_context(),
        "sources": [
            source(
                "z.collect",
                [
                    ("resource-z", ["observation-z", "observation-a"]),
                    ("resource-y", ["observation-y"]),
                ],
            ),
            source("a.collect", [("resource-a", ["observation-b"])]),
        ],
    }


def production_manifest() -> dict[str, Any]:
    return {
        "manifest_id": "manifest-1",
        "profile_revision": 1,
        "collection_sequence": 1,
        "base_graph_revision": 0,
        "base_publication_revision": 0,
        "issued_at": "2026-07-28T00:00:00Z",
        "expires_at": "2026-07-28T00:05:00Z",
        "expected_resources": [
            {
                "resource_id": "resource-z",
                "capability_profile": "profile-z",
                "required_kinds": ["system.identity", "interface.inventory"],
                "optional_kinds": ["wan.sdwan", "overlay.tunnels"],
            },
            {
                "resource_id": "resource-a",
                "capability_profile": "profile-a",
                "required_kinds": ["system.identity", "interface.inventory"],
                "optional_kinds": [],
            },
        ],
    }


def replace_at_path(
    value: dict[str, Any], path: tuple[str | int, ...], new: Any
) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = new


def authoritative_context() -> dict:
    return {
        "scopes": [{"scope_id": "scope-z"}, {"scope_id": "scope-a"}],
        "subnets": authoritative_records("subnet"),
        "external_networks": authoritative_records("external"),
        "circuits": authoritative_records("circuit"),
        "cloud_networks": authoritative_records("cloud"),
    }


def authoritative_records(prefix: str) -> list[dict]:
    return [
        {"record_id": f"{prefix}-z", "scope_ids": ["scope-z", "scope-a"]},
        {"record_id": f"{prefix}-a", "scope_ids": []},
    ]


def resource(resource_id: str, observation_ids: list[str]) -> dict:
    return {
        "resource_id": resource_id,
        "asset_context": {"scope_ids": ["scope-z", "scope-a"]},
        "result": {
            "observations": [
                {
                    "observation_id": observation_id,
                    "data": {"值": observation_id},
                }
                for observation_id in observation_ids
            ]
        },
    }


def source(action_id: str, resources: list[tuple[str, list[str]]]) -> dict:
    return {
        "action_id": action_id,
        "action_version": "1.0.0",
        "resources": [resource(resource_id, ids) for resource_id, ids in resources],
    }


def reverse_collection_inputs(snapshot: dict) -> dict:
    value = deepcopy(snapshot)
    value["sources"].reverse()
    for source_item in value["sources"]:
        source_item["resources"].reverse()
        for resource in source_item["resources"]:
            resource["result"]["observations"].reverse()
    return value


def reverse_stable_arrays(snapshot: dict) -> dict:
    value = reverse_collection_inputs(snapshot)
    value["target_manifest"]["expected_resources"].reverse()
    for records in value["authoritative_context"].values():
        records.reverse()
    return value


def test_rfc8785_jcs_known_answer_uses_utf16_key_order_and_ecmascript_numbers() -> None:
    value = {"\ue000": 2, "😀": 1, "number": 1e-7, "minus_zero": -0.0}
    expected = '{"minus_zero":0,"number":1e-7,"😀":1,"\ue000":2}'.encode()

    assert JCS_IMPLEMENTATION == "rfc8785==0.1.4"
    assert canonicalize_json(value) == expected


def test_manifest_hash_projection_is_versioned_and_reordering_is_stable() -> None:
    manifest = production_manifest()
    reordered = deepcopy(manifest)
    reordered["expected_resources"].reverse()
    for resource_item in reordered["expected_resources"]:
        resource_item["required_kinds"].reverse()
        resource_item["optional_kinds"].reverse()

    direct = canonicalize_and_hash_manifest(manifest)
    reverse = canonicalize_and_hash_manifest(reordered)

    assert direct.manifest_hash == reverse.manifest_hash
    assert direct.projection["hash_domain"] == MANIFEST_HASH_DOMAIN
    assert set(direct.projection) == {"hash_domain", "target_manifest"}
    resources = direct.projection["target_manifest"]["expected_resources"]
    assert [item["resource_id"] for item in resources] == ["resource-a", "resource-z"]
    assert resources[1]["required_kinds"] == ["interface.inventory", "system.identity"]


@pytest.mark.parametrize(("path", "new_value"), MANIFEST_FIELD_CHANGES)
def test_each_immutable_manifest_field_changes_hash(
    path: tuple[str | int, ...], new_value: Any
) -> None:
    baseline = canonicalize_and_hash_manifest(production_manifest())
    changed = production_manifest()
    replace_at_path(changed, path, new_value)

    assert (
        canonicalize_and_hash_manifest(changed).manifest_hash != baseline.manifest_hash
    )


def test_manifest_hash_can_be_generated_or_verified_but_not_mismatched() -> None:
    manifest = production_manifest()
    generated = canonicalize_and_hash_manifest(manifest)
    supplied = {**manifest, "manifest_hash": generated.manifest_hash}

    assert canonicalize_and_hash_manifest(supplied) == generated

    supplied["manifest_hash"] = sha256_uri(b"wrong")
    with pytest.raises(CanonicalizationError, match="manifest_hash mismatch"):
        canonicalize_and_hash_manifest(supplied)


@pytest.mark.parametrize("field", tuple(production_manifest()))
def test_manifest_hash_rejects_missing_fields(field: str) -> None:
    manifest = production_manifest()
    manifest.pop(field)
    with pytest.raises(CanonicalizationError, match="fields are invalid"):
        canonicalize_and_hash_manifest(manifest)


def test_manifest_hash_rejects_unknown_root_and_resource_fields() -> None:
    root_extra = {**production_manifest(), "tenant_id": "forbidden"}
    with pytest.raises(CanonicalizationError, match="unknown=.*tenant_id"):
        canonicalize_and_hash_manifest(root_extra)

    resource_extra = production_manifest()
    resource_extra["expected_resources"][0]["vendor"] = "forbidden"
    with pytest.raises(CanonicalizationError, match="unknown=.*vendor"):
        canonicalize_and_hash_manifest(resource_extra)


@pytest.mark.parametrize(
    "timestamp", ("2026-07-28T00:00:00+00:00", "2026-02-30T00:00:00Z")
)
def test_manifest_hash_requires_real_canonical_utc_timestamps(timestamp: str) -> None:
    manifest = production_manifest()
    manifest["expires_at"] = timestamp
    with pytest.raises(CanonicalizationError, match="expires_at"):
        canonicalize_and_hash_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "new_value"),
    (
        ("profile_revision", 2),
        ("issued_at", "2026-07-28T00:00:01Z"),
        ("expires_at", "2026-07-28T00:06:00Z"),
    ),
)
def test_manifest_change_alters_manifest_and_container_hashes(
    field: str, new_value: Any
) -> None:
    snapshot = stored_snapshot()
    manifest = production_manifest()
    baseline_manifest = canonicalize_and_hash_manifest(manifest)
    manifest["manifest_hash"] = baseline_manifest.manifest_hash
    snapshot["target_manifest"] = manifest
    baseline = canonicalize_and_hash_snapshot(snapshot)

    changed = deepcopy(snapshot)
    changed_manifest = changed["target_manifest"]
    changed_manifest.pop("manifest_hash")
    changed_manifest[field] = new_value
    changed_result = canonicalize_and_hash_manifest(changed_manifest)
    changed_manifest["manifest_hash"] = changed_result.manifest_hash

    assert changed_result.manifest_hash != baseline_manifest.manifest_hash
    assert canonicalize_and_hash_snapshot(changed).payload_hash != baseline.payload_hash


def test_text_normalization_removes_bom_and_normalizes_all_newlines() -> None:
    raw = "\ufeff第一行\r\n第二行\r第三行\n"
    expected = "第一行\n第二行\n第三行\n".encode()

    assert canonicalize_text(raw) == expected


@pytest.mark.parametrize(("source_xml", "expected"), XML_C14N11_VECTORS)
def test_xml_c14n11_matches_w3c_known_answer_vectors(
    source_xml: str, expected: str
) -> None:
    assert XML_C14N_IMPLEMENTATION.startswith("xmlsec==1.3.17")
    assert XML_C14N_ALGORITHM_URI == "http://www.w3.org/2006/12/xml-c14n11"
    assert canonicalize_xml(source_xml) == expected.encode()


def test_xml_c14n11_does_not_inherit_xml_id_like_c14n10() -> None:
    c14n10 = _canonicalize_xml_subset(
        XML_C14N_VERSION_SPLIT_SOURCE, XML_C14N_VERSION_SPLIT_XPATH, 0
    )
    c14n11 = _canonicalize_xml_subset(
        XML_C14N_VERSION_SPLIT_SOURCE, XML_C14N_VERSION_SPLIT_XPATH, 2
    )

    assert c14n10 == b'<target xml:id="outer"></target>'
    assert c14n11 == b"<target></target>"
    assert canonicalize_xml(XML_C14N_VERSION_SPLIT_SOURCE) == (
        b'<root xml:id="outer"><target></target></root>'
    )


def test_xml_c14n11_normalizes_newlines_and_preserves_unicode_namespaces() -> None:
    crlf = '<r:root xmlns:r="urn:router">核心\r\n交换机\r</r:root>'
    lf = '<r:root xmlns:r="urn:router">核心\n交换机\n</r:root>'

    assert canonicalize_xml(crlf) == canonicalize_xml(lf)
    assert b'xmlns:r="urn:router"' in canonicalize_xml(crlf)


def test_observation_hashes_use_raw_and_canonical_payloads() -> None:
    raw = "\ufeffalpha\r\nbeta\r"
    canonical = b"alpha\nbeta\n"
    observation = {
        "content_type": "text/plain",
        "data": raw,
        "raw_sha256": sha256_uri(raw.encode()),
        "content_sha256": sha256_uri(canonical),
    }

    assert validate_observation_hashes(observation) == canonical

    bad_raw = {**observation, "raw_sha256": sha256_uri(b"wrong")}
    with pytest.raises(CanonicalizationError, match="raw_sha256 mismatch"):
        validate_observation_hashes(bad_raw)

    bad_content = {**observation, "content_sha256": sha256_uri(b"wrong")}
    with pytest.raises(CanonicalizationError, match="content_sha256 mismatch"):
        validate_observation_hashes(bad_content)


def test_snapshot_reordering_is_stable_and_business_arrays_are_sorted() -> None:
    direct = canonicalize_and_hash_snapshot(stored_snapshot())
    reordered = canonicalize_and_hash_snapshot(reverse_stable_arrays(stored_snapshot()))

    assert direct.payload_hash == reordered.payload_hash
    assert direct.canonical_bytes == reordered.canonical_bytes
    assert [item["action_id"] for item in direct.normalized["sources"]] == [
        "a.collect",
        "z.collect",
    ]
    z_source = next(
        item
        for item in direct.normalized["sources"]
        if item["action_id"] == "z.collect"
    )
    assert [item["resource_id"] for item in z_source["resources"]] == [
        "resource-y",
        "resource-z",
    ]
    z_resource = next(
        item for item in z_source["resources"] if item["resource_id"] == "resource-z"
    )
    assert [
        item["observation_id"] for item in z_resource["result"]["observations"]
    ] == ["observation-a", "observation-z"]
    context = direct.normalized["authoritative_context"]
    assert [item["scope_id"] for item in context["scopes"]] == ["scope-a", "scope-z"]
    assert [item["record_id"] for item in context["subnets"]] == [
        "subnet-a",
        "subnet-z",
    ]


def test_payload_hash_includes_run_and_collection_time_but_excludes_transport() -> None:
    baseline = stored_snapshot()
    changed_run = deepcopy(baseline)
    changed_run["collector"]["run_id"] = "run-2"
    changed_time = {**baseline, "collection_started_at": "2026-07-29T00:00:00Z"}
    transport_only = {
        **baseline,
        "payload_hash": "untrusted",
        "received_at": "later",
        "upload_session_id": "session-2",
        "chunk_sequence": 99,
        "chunk_index": 3,
        "transport_attempt": 2,
        "transport_retry_count": 1,
    }

    original_hash = canonicalize_and_hash_snapshot(baseline).payload_hash
    assert canonicalize_and_hash_snapshot(changed_run).payload_hash != original_hash
    assert canonicalize_and_hash_snapshot(changed_time).payload_hash != original_hash
    assert canonicalize_and_hash_snapshot(transport_only).payload_hash == original_hash


def test_direct_and_chunk_equivalent_envelopes_have_same_payload_hash() -> None:
    direct = stored_snapshot()
    chunked = deepcopy(direct)
    chunks = chunked["sources"][0]["resources"][0]["result"].pop("observations")
    chunked["sources"][0]["resources"][0]["result"]["observations"] = list(
        reversed(chunks)
    )

    direct_result = canonicalize_and_hash_snapshot(direct)
    chunk_result = canonicalize_and_hash_snapshot(chunked)
    assert direct_result.payload_hash == chunk_result.payload_hash
    assert direct_result.canonical_bytes == chunk_result.canonical_bytes


@pytest.mark.parametrize(
    "fixture_path", GOLDEN_FILES, ids=lambda path: path.parent.name
)
def test_phase0_reordered_input_preserves_frozen_output_hash_contract(
    fixture_path: Path,
) -> None:
    case = json.loads(fixture_path.read_text(encoding="utf-8"))
    reordered = reverse_collection_inputs(case["collection"])
    hashes = case["canonical_hashes"]
    # Phase 0 freezes static vectors; Phase 3 parser/projector determinism is unproven.
    vectors = {
        (
            canonicalize_and_hash_snapshot(collection).payload_hash,
            sha256_uri(canonicalize_json(case["expected_facts"])),
            sha256_uri(canonicalize_json(case["expected_presentation_graph"])),
        )
        for collection in (case["collection"], reordered)
    }
    assert vectors == {
        (
            hashes["snapshot"],
            hashes["facts"],
            hashes["graph"],
        )
    }


@pytest.mark.parametrize(
    "data",
    [
        {"api_token": "secret"},
        {"nested": {"snmp-community": "private"}},
        "username admin\npassword secret\ninterface Ethernet1",
        "Authorization: Bearer secret",
        "<config><password>secret</password></config>",
        "<api-token>secret</api-token>",
        '<config api_token="secret"/>',
        "set system login user admin encrypted-password secret",
        "set service account secret value",
        "snmp community private ro",
    ],
)
def test_credential_blocks_are_rejected(data: object) -> None:
    content_type = "application/json" if isinstance(data, dict) else "text/plain"
    with pytest.raises(CanonicalizationError, match="Credential-bearing"):
        canonicalize_observation_data(content_type, data)


def test_credential_scan_allows_non_secret_descriptions() -> None:
    text = "This description mentions password rotation and secret handling."
    xml = "<description>API token and password rotation policy</description>"

    assert canonicalize_observation_data("text/plain", text) == text.encode()
    assert canonicalize_observation_data("application/xml", xml) == xml.encode()


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("authoritative_context", "subnets", 0),
        ("sources", 0, "resources", 0, "asset_context"),
    ],
)
def test_stored_snapshot_rejects_credentials_at_every_envelope_level(
    path: tuple[str | int, ...],
) -> None:
    snapshot = stored_snapshot()
    target = snapshot
    for part in path:
        target = target[part]
    target["api_token"] = "secret"

    with pytest.raises(CanonicalizationError, match="Credential-bearing"):
        canonicalize_and_hash_snapshot(snapshot)


def test_line_locator_is_one_based_closed_and_bounded() -> None:
    content = "alpha\nbeta\ngamma\n"
    assert resolve_line_range(content, 1, 2) == "alpha\nbeta"
    assert resolve_line_range(content, 3, 3) == "gamma"

    for start, end in ((0, 1), (2, 1), (1, 4), (True, 1)):
        with pytest.raises(LocatorError):
            resolve_line_range(content, start, end)


def test_json_pointer_supports_rfc6901_escapes_and_strict_array_indexes() -> None:
    document = {"a/b": {"~key": [{"value": "evidence"}]}}
    assert resolve_json_pointer(document, "/a~1b/~0key/0/value") == "evidence"
    assert resolve_json_pointer(document, "") is document

    for pointer in ("a/b", "/a~2b", "/missing", "/a~1b/~0key/00", "/a~1b/~0key/-"):
        with pytest.raises(LocatorError):
            resolve_json_pointer(document, pointer)


def test_restricted_xpath_resolves_namespaced_absolute_child_paths() -> None:
    xml = (
        '<r:root xmlns:r="urn:router"><r:item id="1"/>'
        '<r:item id="2"><r:name>核心</r:name></r:item></r:root>'
    )
    result = resolve_restricted_xpath(
        xml, "/r:root/r:item[2]/r:name", {"r": "urn:router"}
    )
    attribute = resolve_restricted_xpath(
        xml, "/r:root/r:item[2]/@id", {"r": "urn:router"}
    )

    assert result[0].text == "核心"
    assert attribute == ["2"]


@pytest.mark.parametrize(
    "path",
    [
        "//r:item",
        "/r:root/../r:item",
        "/r:root/*",
        "/r:root/r:item/text()",
        "/r:root/r:item | /r:root",
        "/descendant::r:item",
    ],
)
def test_restricted_xpath_rejects_unsafe_constructs(path: str) -> None:
    xml = '<r:root xmlns:r="urn:router"><r:item/></r:root>'
    with pytest.raises(LocatorError, match="restricted"):
        resolve_restricted_xpath(xml, path, {"r": "urn:router"})


def test_restricted_xpath_rejects_missing_namespaces_and_dtds() -> None:
    xml = '<r:root xmlns:r="urn:router"><r:item/></r:root>'
    with pytest.raises(LocatorError, match="undeclared"):
        resolve_restricted_xpath(xml, "/r:root/r:item", {})

    with pytest.raises(LocatorError, match="DTD"):
        resolve_restricted_xpath(
            '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><root/>',
            "/root",
            {},
        )
