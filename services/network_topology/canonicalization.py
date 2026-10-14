"""Canonical payload and hash rules for topology collection snapshots."""

from __future__ import annotations

import ctypes
import re
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from typing import Any

import rfc8785
import xmlsec

JCS_IMPLEMENTATION = "rfc8785==0.1.4"
XML_C14N_IMPLEMENTATION = "xmlsec==1.3.17 bundled libxml2 xmlC14NDocDumpMemory mode=2"
XML_C14N_ALGORITHM_URI = "http://www.w3.org/2006/12/xml-c14n11"
SHA256_PREFIX = "sha256:"
MANIFEST_HASH_DOMAIN = "aioe.network-topology.manifest-hash/v1"
MANIFEST_FIELDS = (
    "manifest_id",
    "profile_revision",
    "collection_sequence",
    "base_graph_revision",
    "base_publication_revision",
    "issued_at",
    "expires_at",
    "expected_resources",
)
MANIFEST_RESOURCE_FIELDS = (
    "resource_id",
    "capability_profile",
    "required_kinds",
    "optional_kinds",
)
MANIFEST_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_XML_C14N_1_0 = 0
_XML_C14N_1_1 = 2
_XML_PARSE_SAFE = 32 | 64 | 2048  # NOERROR | NOWARNING | NONET
VOLATILE_ROOT_FIELDS = frozenset(
    {
        "payload_hash",
        "received_at",
        "upload_session_id",
        "chunk_sequence",
        "chunk_index",
        "transport_attempt",
        "transport_retry_count",
    }
)
STRING_SET_FIELDS = frozenset(
    {
        "collected_kinds",
        "collected_resource_ids",
        "expected_resource_ids",
        "failed_resource_ids",
        "management_ips",
        "optional_kinds",
        "required_kinds",
        "scope_ids",
        "tags",
    }
)
ARRAY_BUSINESS_KEYS: dict[str, Callable[[dict[str, Any]], tuple[str, ...]]] = {
    "sources": lambda item: (item["action_id"], item["action_version"]),
    "resources": lambda item: (item["resource_id"],),
    "observations": lambda item: (item["observation_id"],),
    "scopes": lambda item: (item["scope_id"],),
    "subnets": lambda item: (item["record_id"],),
    "external_networks": lambda item: (item["record_id"],),
    "circuits": lambda item: (item["record_id"],),
    "cloud_networks": lambda item: (item["record_id"],),
    "expected_resources": lambda item: (item["resource_id"],),
    "observation_coverage": lambda item: (item["resource_id"],),
}
SENSITIVE_NAME = re.compile(
    r"(^|_)(authorization|cookie|password|passwd|private_key|psk|"
    r"pre_shared_key|snmp_community|tacacs_key|radius_key|token|username)($|_)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"(?im)^\s*(?:(?:authorization|cookie)\s*:|"
    r"(?:api[-_ ]?token|password|passwd|encrypted[-_ ]?password|"
    r"private[-_ ]?key|pre[-_ ]?shared[-_ ]?key|psk|radius[-_ ]?key)"
    r"\b\s*(?:[:=]|\s)\s*\S+|snmp(?:-server|-agent)?\s+community\s+.*|"
    r"tacacs\S*\s+key\s+\S+|username\s+\S+|"
    r"set\s+.*\b(?:encrypted[-_ ]?password|password|secret)\s+\S+)"
)
SENSITIVE_XML = re.compile(
    r"(?i)(?:<(?:[A-Za-z_][\w.-]*:)?(?:authorization|cookie|api[-_]?token|"
    r"password|passwd|encrypted[-_]?password|private[-_]?key|"
    r"pre[-_]?shared[-_]?key|snmp[-_]?community|tacacs[-_]?key|token|"
    r"secret|username)(?:\s|/?>)|\s(?:[A-Za-z_][\w.-]*:)?"
    r"(?:authorization|cookie|api[-_]?token|password|passwd|"
    r"encrypted[-_]?password|private[-_]?key|pre[-_]?shared[-_]?key|"
    r"snmp[-_]?community|tacacs[-_]?key|token|secret|username)\s*=)"
)


class CanonicalizationError(ValueError):
    """Raised when content cannot enter the canonical snapshot boundary."""


@dataclass(frozen=True)
class CanonicalSnapshot:
    normalized: dict[str, Any]
    canonical_bytes: bytes
    payload_hash: str


@dataclass(frozen=True)
class CanonicalManifest:
    projection: dict[str, Any]
    canonical_bytes: bytes
    manifest_hash: str


class _XmlXPathObject(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("nodesetval", ctypes.c_void_p),
        ("boolval", ctypes.c_int),
        ("floatval", ctypes.c_double),
        ("stringval", ctypes.c_void_p),
        ("user", ctypes.c_void_p),
        ("index", ctypes.c_int),
        ("user2", ctypes.c_void_p),
        ("index2", ctypes.c_int),
    ]


def sha256_uri(value: bytes) -> str:
    return f"{SHA256_PREFIX}{sha256(value).hexdigest()}"


def canonicalize_json(value: Any) -> bytes:
    """Serialize JSON with the pinned RFC 8785 implementation."""
    try:
        return rfc8785.dumps(value)
    except (TypeError, ValueError, rfc8785.CanonicalizationError) as exc:
        raise CanonicalizationError(f"Value is not valid RFC 8785 JSON: {exc}") from exc


def canonicalize_text(value: str) -> bytes:
    if not isinstance(value, str):
        raise CanonicalizationError("Text observation data must be a string")
    value = value.removeprefix("\ufeff")
    return value.replace("\r\n", "\n").replace("\r", "\n").encode()


def canonicalize_xml(value: str) -> bytes:
    """Return W3C C14N 1.1 bytes from the pinned libxml2 implementation."""
    reject_credential_blocks(value)
    _validate_xml_text(value)
    return _canonicalize_xml_engine(value, _XML_C14N_1_1)


def canonicalize_observation_data(content_type: str, data: Any) -> bytes:
    reject_credential_blocks(data)
    if content_type == "application/json":
        return canonicalize_json(data)
    if content_type == "text/plain":
        return canonicalize_text(data)
    if content_type == "application/xml":
        return canonicalize_xml(data)
    raise CanonicalizationError(f"Unsupported observation content type: {content_type}")


def validate_observation_hashes(observation: dict[str, Any]) -> bytes:
    content_type = observation.get("content_type")
    data = observation.get("data")
    raw = (
        canonicalize_json(data)
        if content_type == "application/json"
        else _raw_text(data)
    )
    _require_hash("raw_sha256", observation.get("raw_sha256"), raw)
    canonical = canonicalize_observation_data(content_type, data)
    _require_hash("content_sha256", observation.get("content_sha256"), canonical)
    return canonical


def canonicalize_and_hash_snapshot(snapshot: dict[str, Any]) -> CanonicalSnapshot:
    """Return the one authoritative direct/chunk stored-envelope payload hash."""
    if not isinstance(snapshot, dict):
        raise CanonicalizationError("Stored snapshot envelope must be an object")
    reject_credential_blocks(snapshot)
    normalized = _normalize_snapshot_value(deepcopy(snapshot), (), None)
    canonical = canonicalize_json(normalized)
    return CanonicalSnapshot(normalized, canonical, sha256_uri(canonical))


def canonicalize_and_hash_manifest(manifest: dict[str, Any]) -> CanonicalManifest:
    """Hash one immutable server target Manifest using the versioned projection."""
    if not isinstance(manifest, dict):
        raise CanonicalizationError("Target manifest must be an object")
    normalized = _normalize_manifest(deepcopy(manifest))
    projection = {
        "hash_domain": MANIFEST_HASH_DOMAIN,
        "target_manifest": normalized,
    }
    canonical = canonicalize_json(projection)
    manifest_hash = sha256_uri(canonical)
    supplied_hash = manifest.get("manifest_hash")
    if supplied_hash is not None and supplied_hash != manifest_hash:
        raise CanonicalizationError("manifest_hash mismatch")
    return CanonicalManifest(projection, canonical, manifest_hash)


def _normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    _require_exact_fields(manifest, MANIFEST_FIELDS, {"manifest_hash"}, "Manifest")
    _require_non_empty_string(manifest["manifest_id"], "manifest_id")
    for field, minimum in (
        ("profile_revision", 1),
        ("collection_sequence", 1),
        ("base_graph_revision", 0),
        ("base_publication_revision", 0),
    ):
        _require_integer(manifest[field], field, minimum)
    for field in ("issued_at", "expires_at"):
        _require_utc_timestamp(manifest[field], field)
    resources = manifest["expected_resources"]
    if not isinstance(resources, list) or not resources:
        raise CanonicalizationError("expected_resources must be a non-empty array")
    normalized_resources = [_normalize_manifest_resource(item) for item in resources]
    resource_ids = [item["resource_id"] for item in normalized_resources]
    if len(resource_ids) != len(set(resource_ids)):
        raise CanonicalizationError("expected_resources resource_id must be unique")
    normalized = {field: manifest[field] for field in MANIFEST_FIELDS}
    normalized["expected_resources"] = sorted(
        normalized_resources, key=lambda item: item["resource_id"].encode()
    )
    return normalized


def _normalize_manifest_resource(resource: Any) -> dict[str, Any]:
    if not isinstance(resource, dict):
        raise CanonicalizationError("Manifest expected resource must be an object")
    _require_exact_fields(resource, MANIFEST_RESOURCE_FIELDS, set(), "Resource")
    for field in ("resource_id", "capability_profile"):
        _require_non_empty_string(resource[field], field)
    return {
        "resource_id": resource["resource_id"],
        "capability_profile": resource["capability_profile"],
        "required_kinds": _normalize_string_set(resource["required_kinds"]),
        "optional_kinds": _normalize_string_set(resource["optional_kinds"]),
    }


def _require_exact_fields(
    value: dict[str, Any], required: tuple[str, ...], optional: set[str], label: str
) -> None:
    actual = set(value)
    missing = set(required) - actual
    unknown = actual - set(required) - optional
    if missing or unknown:
        raise CanonicalizationError(
            f"{label} fields are invalid: missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )


def _require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise CanonicalizationError(f"{field} must be a non-empty string")


def _require_integer(value: Any, field: str, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise CanonicalizationError(f"{field} must be an integer >= {minimum}")


def _require_utc_timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str) or MANIFEST_UTC_TIMESTAMP.fullmatch(value) is None:
        raise CanonicalizationError(f"{field} must use canonical UTC Z seconds")
    try:
        datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise CanonicalizationError(f"{field} is not a real UTC timestamp") from exc


def _normalize_string_set(value: Any) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CanonicalizationError("Manifest kind sets must contain strings")
    if len(value) != len(set(value)):
        raise CanonicalizationError("Manifest kind sets must be unique")
    return sorted(value, key=lambda item: item.encode())


def reject_credential_blocks(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SENSITIVE_NAME.search(_normalize_name(str(key))):
                raise CanonicalizationError("Credential-bearing field is forbidden")
            reject_credential_blocks(child)
    elif isinstance(value, list):
        for child in value:
            reject_credential_blocks(child)
    elif isinstance(value, str) and (
        SENSITIVE_TEXT.search(value) or SENSITIVE_XML.search(value)
    ):
        raise CanonicalizationError("Credential-bearing text block is forbidden")


def _normalize_snapshot_value(
    value: Any, path: tuple[str, ...], field: str | None
) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_snapshot_value(child, (*path, key), key)
            for key, child in value.items()
            if path or key not in VOLATILE_ROOT_FIELDS
        }
    if isinstance(value, list):
        items = [_normalize_snapshot_value(item, path, field) for item in value]
        return _sort_snapshot_array(items, field)
    return value


def _sort_snapshot_array(items: list[Any], field: str | None) -> list[Any]:
    if field in STRING_SET_FIELDS and all(isinstance(item, str) for item in items):
        return sorted(items, key=lambda item: item.encode())
    key = ARRAY_BUSINESS_KEYS.get(field or "")
    if key is None:
        return items
    try:
        return sorted(items, key=key)
    except (KeyError, TypeError) as exc:
        raise CanonicalizationError(f"Invalid {field} business sort key") from exc


def _raw_text(value: Any) -> bytes:
    if not isinstance(value, str):
        raise CanonicalizationError("Text/XML observation data must be a string")
    return value.encode()


def _validate_xml_text(value: Any) -> None:
    if not isinstance(value, str):
        raise CanonicalizationError("XML observation data must be a string")
    if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
        raise CanonicalizationError("XML DTD and entity declarations are forbidden")


def _canonicalize_xml_subset(value: str, xpath: str, mode: int) -> bytes:
    """Canonicalize a node-set for C14N 1.0/1.1 conformance vectors."""
    _validate_xml_text(value)
    if mode not in {_XML_C14N_1_0, _XML_C14N_1_1}:
        raise CanonicalizationError("Unsupported XML canonicalization mode")
    return _canonicalize_xml_engine(value, mode, xpath)


def _canonicalize_xml_engine(value: str, mode: int, xpath: str | None = None) -> bytes:
    bindings = _load_xmlsec_libxml2()
    lib = bindings[0]
    raw = value.encode()
    document = lib.xmlReadMemory(raw, len(raw), None, b"UTF-8", _XML_PARSE_SAFE)
    if not document:
        raise CanonicalizationError("XML observation data is not well formed")
    try:
        nodes = _xpath_nodes(lib, document, xpath) if xpath else nullcontext(None)
        with nodes as node_set:
            return _dump_canonical_xml(bindings, document, node_set, mode)
    finally:
        lib.xmlFreeDoc(document)


@contextmanager
def _xpath_nodes(lib: Any, document: Any, xpath: str):
    context = lib.xmlXPathNewContext(document)
    if not context:
        raise CanonicalizationError("XML XPath context allocation failed")
    expression = lib.xmlXPathEvalExpression(xpath.encode(), context)
    try:
        if not expression or not expression.contents.nodesetval:
            raise CanonicalizationError("XML subset XPath did not resolve nodes")
        yield expression.contents.nodesetval
    finally:
        if expression:
            lib.xmlXPathFreeObject(expression)
        lib.xmlXPathFreeContext(context)


def _dump_canonical_xml(
    bindings: tuple[Any, Callable[[Any], None]],
    document: Any,
    node_set: Any,
    mode: int,
) -> bytes:
    lib, xml_free = bindings
    output = ctypes.c_void_p()
    size = lib.xmlC14NDocDumpMemory(
        document, node_set, mode, None, 0, ctypes.byref(output)
    )
    if size < 0 or not output.value:
        raise CanonicalizationError("W3C XML canonicalization failed")
    try:
        return ctypes.string_at(output, size)
    finally:
        xml_free(output)


@lru_cache(maxsize=1)
def _load_xmlsec_libxml2() -> tuple[Any, Callable[[Any], None]]:
    try:
        lib = ctypes.CDLL(xmlsec.__file__)
        _configure_libxml2(lib)
        xml_free = ctypes.CFUNCTYPE(None, ctypes.c_void_p).in_dll(lib, "xmlFree")
    except (AttributeError, OSError, ValueError) as exc:
        raise CanonicalizationError(
            "Pinned xmlsec does not expose the required C14N 1.1 API"
        ) from exc
    return lib, xml_free


def _configure_libxml2(lib: Any) -> None:
    lib.xmlReadMemory.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    lib.xmlReadMemory.restype = ctypes.c_void_p
    lib.xmlFreeDoc.argtypes = [ctypes.c_void_p]
    lib.xmlFreeDoc.restype = None
    _configure_xpath_api(lib)
    _configure_c14n_api(lib)


def _configure_xpath_api(lib: Any) -> None:
    lib.xmlXPathNewContext.argtypes = [ctypes.c_void_p]
    lib.xmlXPathNewContext.restype = ctypes.c_void_p
    lib.xmlXPathEvalExpression.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    lib.xmlXPathEvalExpression.restype = ctypes.POINTER(_XmlXPathObject)
    lib.xmlXPathFreeObject.argtypes = [ctypes.POINTER(_XmlXPathObject)]
    lib.xmlXPathFreeObject.restype = None
    lib.xmlXPathFreeContext.argtypes = [ctypes.c_void_p]
    lib.xmlXPathFreeContext.restype = None


def _configure_c14n_api(lib: Any) -> None:
    lib.xmlC14NDocDumpMemory.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.xmlC14NDocDumpMemory.restype = ctypes.c_int


def _require_hash(name: str, expected: Any, value: bytes) -> None:
    actual = sha256_uri(value)
    if expected != actual:
        raise CanonicalizationError(f"{name} mismatch")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
