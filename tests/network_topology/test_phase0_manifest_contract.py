from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from services.network_topology import contracts
from services.network_topology.canonicalization import canonicalize_and_hash_manifest

COLLECTION_URI = "aioe.network-topology.collection/v2"
RESPONSE_URI = "aioe.network-topology.api/manifest-response/v1"
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "network_topology"
FIXTURE_FILES = tuple(sorted(FIXTURE_ROOT.rglob("*.json")))


def schema_example(schema_uri: str) -> dict[str, Any]:
    return contracts.load_schema(schema_uri)["examples"][0]


def assert_rejected(payload: Any, schema_uri: str = RESPONSE_URI) -> None:
    with pytest.raises(contracts.ContractError):
        contracts.validate_contract(payload, schema_uri)


def iter_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_objects(child)


def discover_fixture_contracts() -> tuple[
    list[tuple[Path, dict[str, Any]]], list[tuple[Path, dict[str, Any]]]
]:
    collections = []
    manifests = []
    for path in FIXTURE_FILES:
        document = json.loads(path.read_text(encoding="utf-8"))
        for value in iter_objects(document):
            if value.get("schema_version") == COLLECTION_URI:
                collections.append((path, value))
            if {"manifest_id", "manifest_hash", "expected_resources"} <= set(value):
                manifests.append((path, value))
    return collections, manifests


def test_manifest_response_wraps_a_complete_target_manifest() -> None:
    payload = schema_example(RESPONSE_URI)
    contracts.validate_contract(payload, RESPONSE_URI)

    legacy_direct_data = deepcopy(payload)
    legacy_direct_data["data"] = legacy_direct_data["data"]["target_manifest"]
    assert_rejected(legacy_direct_data)

    missing_wrapper = deepcopy(payload)
    missing_wrapper["data"] = {}
    assert_rejected(missing_wrapper)


def test_manifest_response_example_uses_authoritative_hash() -> None:
    manifest = schema_example(RESPONSE_URI)["data"]["target_manifest"]
    result = canonicalize_and_hash_manifest(manifest)

    assert manifest["manifest_hash"] == result.manifest_hash


@pytest.mark.parametrize("field", ("profile_revision", "expires_at"))
def test_manifest_response_requires_profile_and_expiry(field: str) -> None:
    payload = schema_example(RESPONSE_URI)
    payload["data"]["target_manifest"].pop(field)
    assert_rejected(payload)


@pytest.mark.parametrize(
    "value",
    (
        "not-a-date",
        "2026-02-30T00:00:00Z",
        "2026-07-28 00:00:00Z",
        "2026-07-28T00:05:00+00:00",
    ),
)
def test_manifest_response_rejects_invalid_expiry(value: str) -> None:
    payload = schema_example(RESPONSE_URI)
    payload["data"]["target_manifest"]["expires_at"] = value
    assert_rejected(payload)


def test_manifest_response_rejects_invalid_revision_and_extra_fields() -> None:
    payload = schema_example(RESPONSE_URI)
    payload["data"]["target_manifest"]["profile_revision"] = 0
    assert_rejected(payload)

    payload = schema_example(RESPONSE_URI)
    payload["data"]["target_manifest"]["tenant_id"] = "forbidden"
    assert_rejected(payload)


def test_all_fixture_manifests_use_final_hash_and_profile_contract() -> None:
    collections, manifests = discover_fixture_contracts()
    assert FIXTURE_FILES
    assert collections
    assert manifests
    for _, manifest in manifests:
        assert "profile_revision" in manifest
        assert "expires_at" in manifest
        assert (
            canonicalize_and_hash_manifest(manifest).manifest_hash
            == manifest["manifest_hash"]
        )
    for _, collection in collections:
        manifest = collection["target_manifest"]
        assert manifest["profile_revision"] == collection["topology_profile_revision"]
        contracts.validate_collection_admission(collection)


@pytest.mark.parametrize("field", ("profile_revision", "expires_at"))
def test_collection_manifest_requires_profile_and_expiry(field: str) -> None:
    collection = schema_example(COLLECTION_URI)
    collection["target_manifest"].pop(field)
    assert_rejected(collection, COLLECTION_URI)
    with pytest.raises(contracts.ContractError):
        contracts.validate_collection_admission(collection)


def test_collection_admission_rejects_profile_revision_mismatch() -> None:
    collection = schema_example(COLLECTION_URI)
    collection["target_manifest"]["profile_revision"] += 1
    contracts.validate_contract(collection, COLLECTION_URI)
    with pytest.raises(
        contracts.ContractError,
        match="topology_profile_revision must equal target_manifest.profile_revision",
    ):
        contracts.validate_collection_admission(collection)


def test_collection_admission_accepts_matching_profile_revision() -> None:
    contracts.validate_collection_admission(schema_example(COLLECTION_URI))
