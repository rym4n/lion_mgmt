from __future__ import annotations

import json
import re
from copy import deepcopy
from ipaddress import ip_interface, ip_network
from itertools import pairwise
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from services.network_topology.canonicalization import (
    CanonicalizationError,
    canonicalize_observation_data,
    reject_credential_blocks,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "network_topology"
FIXTURE_PATHS = tuple(sorted(FIXTURE_ROOT.rglob("*.json")))
DOCUMENTATION_NETWORKS = (
    ip_network("192.0.2.0/24"),
    ip_network("198.51.100.0/24"),
    ip_network("203.0.113.0/24"),
    ip_network("2001:db8::/32"),
)
PROTOCOL_SENTINELS = frozenset({"0.0.0.0", "255.255.255.255", "::"})
DEFAULT_ROUTES = frozenset({("0.0.0.0", 0), ("::", 0)})
UNIVERSAL_MAC_SENTINELS = frozenset({bytes(6), bytes.fromhex("ffffffffffff")})
EXPECTED_REJECTION_FIELDS = frozenset(
    {"content_type", "data", "control_data", "expected_error_contains"}
)
CREDENTIAL_REJECTION_MARKER = "Credential-bearing"
IPV4_RE = re.compile(
    r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:/[0-9]{1,2})?(?![0-9.])"
)
IPV6_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}"
    r"[0-9A-Fa-f]{0,4}(?:/[0-9]{1,3})?(?![0-9A-Fa-f:])"
)
COLON_MAC_RE = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])"
)
DOTTED_MAC_RE = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}(?![0-9a-f])"
)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY(?: BLOCK)?-----", re.IGNORECASE
)
CONTENT_MARKER_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:syn|synth|synthetic|redacted|example|sample|dummy|"
    r"placeholder|fictional|test(?:[-_ ]?only)?)(?![a-z0-9])"
)
RAW_IDENTITY_DECLARATION_RE = re.compile(
    r"""
    (?<![a-z0-9_])(?:ip\s+)?["']?
    (?:
        host(?:[-_ ]?name)?|fqdn|identity(?:[-_ ]?name)?|
        serial(?:[-_ ]?(?:id|no|number))?|domain(?:[-_ ]?name)?|
        (?:account|circuit|customer|org|organisation|organization|site|tenant)
        (?:[-_ ]?(?:code|id|identifier|key|name|no|number|ref|reference))?
    )
    ["']?\s*(?:[:=]|\s)\s*["']?
    (?P<value>[a-z0-9][^\s;,#"']*)
    """,
    re.IGNORECASE | re.VERBOSE,
)
NEUTRAL_CONTENT_VALUES = frozenset(
    {
        "",
        "localhost",
        "n/a",
        "none",
        "not_applicable",
        "not_collected",
        "not_supported",
        "null",
        "unknown",
        "unavailable",
        "unset",
    }
)
IDENTITY_ANCHORS = frozenset(
    {
        "account",
        "circuit",
        "customer",
        "domain",
        "identity",
        "org",
        "organisation",
        "organization",
        "site",
        "tenant",
    }
)
IDENTITY_SUFFIXES = frozenset(
    {"code", "id", "identifier", "key", "name", "no", "number", "ref", "reference"}
)


def _fixture_id(path: Path) -> str:
    return path.relative_to(FIXTURE_ROOT).as_posix()


def _parse_ip_literal(candidate: str):
    try:
        return ip_interface(candidate)
    except ValueError:
        return None


def _iter_ip_literals(value: str):
    seen: set[str] = set()
    for pattern in (IPV4_RE, IPV6_RE):
        for match in pattern.finditer(value):
            candidate = match.group(0)
            parsed = _parse_ip_literal(candidate)
            if parsed is not None and candidate not in seen:
                seen.add(candidate)
                yield candidate, parsed


def _is_allowed_ip(parsed) -> bool:
    address = parsed.ip
    address_prefix = (str(address), parsed.network.prefixlen)
    if address_prefix in DEFAULT_ROUTES:
        return True
    if str(address) in PROTOCOL_SENTINELS:
        return parsed.network.prefixlen == address.max_prefixlen
    return any(
        parsed.version == network.version and parsed.network.subnet_of(network)
        for network in DOCUMENTATION_NETWORKS
    )


def _assert_ip_hygiene(value: str, location: str) -> None:
    for literal, parsed in _iter_ip_literals(value):
        assert _is_allowed_ip(parsed), (
            f"{location}: non-documentation IP literal is forbidden: {literal}"
        )


def _parse_mac_literal(literal: str) -> bytes:
    return bytes.fromhex(literal.replace(":", "").replace("-", "").replace(".", ""))


def _iter_mac_literals(value: str):
    for pattern in (COLON_MAC_RE, DOTTED_MAC_RE):
        for match in pattern.finditer(value):
            literal = match.group(0)
            yield literal, _parse_mac_literal(literal)


def _assert_mac_hygiene(value: str, location: str) -> None:
    for literal, octets in _iter_mac_literals(value):
        locally_administered = bool(octets[0] & 0x02)
        assert locally_administered or octets in UNIVERSAL_MAC_SENTINELS, (
            f"{location}: globally administered MAC literal is forbidden: {literal}"
        )


def _assert_literal_hygiene(value: Any, location: str) -> None:
    if not isinstance(value, str):
        return
    assert not PRIVATE_KEY_BLOCK_RE.search(value), (
        f"{location}: PEM/private key material is forbidden"
    )
    _assert_ip_hygiene(value, location)
    _assert_mac_hygiene(value, location)


def _normalize_path_name(value: str) -> str:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^a-z0-9]+", "_", snake_case.lower()).strip("_")


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in _normalize_path_name(value).split("_") if part)


def _is_identity_name(value: str) -> bool:
    parts = _path_parts(value)
    compact = "".join(parts)
    if compact in {"fqdn", "hostname", "hostnames", "serial", "serialnumber"}:
        return True
    if {"fqdn", "hostname", "serial"}.intersection(parts):
        return True
    if any(left == "host" and right == "name" for left, right in pairwise(parts)):
        return True
    anchors = IDENTITY_ANCHORS.intersection(parts)
    return bool(anchors) and (len(parts) == 1 or parts[-1] in IDENTITY_SUFFIXES)


def _is_identity_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    if _is_identity_name(path[-1]):
        return True
    last_parts = _path_parts(path[-1])
    ancestor_parts = tuple(_path_parts(item) for item in path[:-1])
    protected = IDENTITY_ANCHORS - {"identity"}
    has_anchor = any(protected.intersection(parts) for parts in ancestor_parts)
    has_identity = any(
        parts == ("identity",) or ("identity" in parts and len(last_parts) == 1)
        for parts in ancestor_parts
    )
    return (
        bool(last_parts)
        and last_parts[-1] in IDENTITY_SUFFIXES
        and (has_anchor or has_identity)
    )


def _is_raw_content_name(value: str) -> bool:
    parts = _path_parts(value)
    if not parts:
        return False
    raw_context = {"banner", "config", "configuration", "raw"}.intersection(parts)
    payload_names = {
        "blob",
        "body",
        "content",
        "data",
        "document",
        "output",
        "payload",
        "text",
        "value",
    }
    digest_names = {"checksum", "digest", "hash", "md5", "sha1", "sha256", "sha512"}
    if parts[-1] in digest_names and len(raw_context) <= 1:
        return False
    if parts == ("raw",):
        return True
    if parts[-1] in {"banner", "config", "configuration"}:
        return True
    return bool(raw_context) and (
        bool(payload_names.intersection(parts)) or len(raw_context) > 1
    )


def _is_raw_content_path(path: tuple[str, ...]) -> bool:
    return any(_is_raw_content_name(item) for item in path)


def _is_marked_or_neutral(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    normalized = re.sub(r"[\s-]+", "_", stripped.lower())
    punctuation_only = not any(character.isalnum() for character in stripped)
    return (
        normalized in NEUTRAL_CONTENT_VALUES
        or punctuation_only
        or CONTENT_MARKER_RE.search(stripped) is not None
    )


def _assert_marked_content(value: Any, location: str, category: str) -> None:
    assert _is_marked_or_neutral(value), (
        f"{location}: {category} must be explicitly synthetic/redacted/example data"
    )


def _iter_raw_identity_values(value: str):
    for line_number, line in enumerate(value.splitlines(), start=1):
        for match in RAW_IDENTITY_DECLARATION_RE.finditer(line):
            yield line_number, match.group("value")


def _xml_local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _iter_xml_identity_values(root: ElementTree.Element):
    stack = [(root, ())]
    while stack:
        element, parent_path = stack.pop()
        path = (*parent_path, _xml_local_name(element.tag))
        if _is_identity_path(path) and element.text:
            yield element.text
        for name, value in element.attrib.items():
            if _is_identity_path((*path, _xml_local_name(name))):
                yield value
        stack.extend((child, path) for child in reversed(element))


def _assert_raw_content(value: Any, location: str, category: str) -> None:
    _assert_marked_content(value, location, category)
    if not isinstance(value, str):
        return
    if value.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(value)
        except ElementTree.ParseError as exc:
            raise AssertionError(
                f"{location}: XML raw content must be well-formed"
            ) from exc
        for declared_value in _iter_xml_identity_values(root):
            _assert_marked_content(declared_value, location, "raw identity declaration")
        return
    for line_number, declared_value in _iter_raw_identity_values(value):
        _assert_marked_content(
            declared_value,
            f"{location}:line-{line_number}",
            "raw identity declaration",
        )


def _is_neutral_network_domain(value: Any, path: tuple[str, ...]) -> bool:
    if not isinstance(value, str) or not path:
        return False
    network_domain_fields = {"network_domain", "network_domain_id"}
    return (
        _normalize_path_name(path[-1]) in network_domain_fields
        and value.strip().lower() == "public"
    )


def _assert_path_content_hygiene(
    value: Any, path: tuple[str, ...], location: str
) -> None:
    if _is_identity_path(path) and not _is_neutral_network_domain(value, path):
        _assert_marked_content(value, location, "identity content")
    if _is_raw_content_path(path):
        _assert_raw_content(value, location, "configuration/raw content")


def _scan_sensitive_content(value: Any, location: str, category: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _scan_sensitive_content(child, f"{location}.{key}", category)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_sensitive_content(child, f"{location}[{index}]", category)
        return
    _assert_raw_content(value, location, category)


def _contains_raw_indicator(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = set(_path_parts(value))
    return bool(parts.intersection({"banner", "config", "configuration", "raw"}))


def _is_raw_observation(value: Any) -> bool:
    if not isinstance(value, dict) or "data" not in value:
        return False
    indicators = (
        value.get("kind"),
        value.get("observation_kind"),
        value.get("data_schema"),
    )
    if any(_contains_raw_indicator(indicator) for indicator in indicators):
        return True
    observation_shape = "kind" in value or "observation_id" in value
    return observation_shape and value.get("content_type") in {
        "application/xml",
        "text/plain",
    }


def _scan_literal_value(value: Any, location: str, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        if _is_raw_observation(value):
            _scan_sensitive_content(
                value["data"], f"{location}.data", "observation configuration/raw data"
            )
        for key, child in value.items():
            _assert_literal_hygiene(str(key), f"{location}.<key>")
            child_path = (*path, str(key))
            _scan_literal_value(child, f"{location}.{key}", child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_literal_value(child, f"{location}[{index}]", path)
        return
    _assert_path_content_hygiene(value, path, location)
    _assert_literal_hygiene(value, location)


def _has_expected_rejection_shape(value: Any) -> bool:
    return isinstance(value, dict) and EXPECTED_REJECTION_FIELDS <= value.keys()


def _is_expected_rejection(value: dict[str, Any]) -> bool:
    return (
        isinstance(value["content_type"], str)
        and value["expected_error_contains"] == CREDENTIAL_REJECTION_MARKER
        and value["data"] != value["control_data"]
    )


def _assert_expected_rejection(value: dict[str, Any], location: str) -> None:
    content_type = value["content_type"]
    expected_error = value["expected_error_contains"]
    _scan_literal_value(
        value["data"], f"{location}.data", ("expected_rejection", "data")
    )
    try:
        canonicalize_observation_data(content_type, value["data"])
    except CanonicalizationError as exc:
        assert CREDENTIAL_REJECTION_MARKER in str(exc) and expected_error in str(exc), (
            f"{location}: expected rejection did not match canonical scanner"
        )
    else:
        raise AssertionError(f"{location}: expected credential rejection was accepted")
    canonicalize_observation_data(content_type, value["control_data"])


def _scan_fixture_value(value: Any, location: str, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        rejection_shape = _has_expected_rejection_shape(value)
        if rejection_shape:
            assert _is_expected_rejection(value), (
                f"{location}: invalid expected credential rejection contract"
            )
            _assert_expected_rejection(value, location)
        if _is_raw_observation(value):
            _scan_sensitive_content(
                value["data"], f"{location}.data", "observation configuration/raw data"
            )
        for key, child in value.items():
            _assert_literal_hygiene(str(key), f"{location}.<key>")
            reject_credential_blocks({str(key): None})
            if not (rejection_shape and key == "data"):
                child_path = (*path, str(key))
                _scan_fixture_value(child, f"{location}.{key}", child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_fixture_value(child, f"{location}[{index}]", path)
        return
    _assert_path_content_hygiene(value, path, location)
    _assert_literal_hygiene(value, location)
    reject_credential_blocks(value)


def _assert_fixture_hygiene(document: Any, location: str = "fixture") -> None:
    assert isinstance(document, dict), f"{location}: fixture root must be an object"
    assert document.get("synthetic_redacted") is True, (
        f"{location}: root synthetic_redacted must explicitly be true"
    )
    _scan_fixture_value(document, location)


def _safe_document() -> dict[str, Any]:
    return {
        "synthetic_redacted": True,
        "description": "Synthetic topology acceptance fixture",
    }


def _raw_observation(data: Any, content_type: str = "text/plain") -> dict[str, Any]:
    return {
        "observation_id": "observation-synthetic-001",
        "kind": "legacy.running_config",
        "content_type": content_type,
        "data": data,
    }


def test_fixture_corpus_is_nonempty() -> None:
    assert FIXTURE_PATHS, "network-topology fixture corpus must not be empty"


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=_fixture_id)
def test_every_network_topology_fixture_is_synthetic_and_redacted(
    fixture_path: Path,
) -> None:
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    _assert_fixture_hygiene(document, _fixture_id(fixture_path))


def test_documentation_and_protocol_controls_are_allowed() -> None:
    document = _safe_document()
    document["controls"] = [
        "192.0.2.10/24",
        "198.51.100.64/26",
        "203.0.113.9",
        "2001:db8::10/64",
        "0.0.0.0/0",
        "::/0",
        "0.0.0.0",
        "::",
        "02:00:00:00:00:10",
        "00:00:00:00:00:00",
        "ff:ff:ff:ff:ff:ff",
    ]
    _assert_fixture_hygiene(document)


def test_expected_credential_rejection_contract_is_allowed() -> None:
    document = _safe_document()
    document["negative"] = {
        "content_type": "text/plain",
        "data": "username synthetic-user\npassword SYNTHETIC-NOT-A-SECRET",
        "control_data": "description synthetic credential rotation policy",
        "expected_error_contains": "Credential-bearing",
    }
    _assert_fixture_hygiene(document)


def test_missing_root_marker_mutation_fails_closed() -> None:
    document = _safe_document()
    document.pop("synthetic_redacted")
    with pytest.raises(AssertionError, match="synthetic_redacted"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    "literal",
    [
        "10.23.45.67",
        "93.184.216.34",
        "fd12:3456:789a::10",
        "2606:4700:4700::1111",
    ],
)
def test_real_ip_literal_mutations_fail_closed(literal: str) -> None:
    document = _safe_document()
    document["address"] = literal
    with pytest.raises(AssertionError, match="IP literal"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    "literal",
    ["192.0.2.10/8", "2001:db8::10/16", "0.0.0.0/1", "::/1"],
)
def test_allowed_address_with_escaping_prefix_mutations_fail_closed(
    literal: str,
) -> None:
    document = _safe_document()
    document["address"] = literal
    with pytest.raises(AssertionError, match="IP literal"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    "literal",
    ["00:11:22:33:44:55", "00-11-22-33-44-55", "0011.2233.4455"],
)
def test_globally_administered_mac_mutations_fail_closed(literal: str) -> None:
    document = _safe_document()
    document["mac"] = literal
    with pytest.raises(AssertionError, match="MAC literal"):
        _assert_fixture_hygiene(document)


def test_private_key_block_mutation_fails_closed() -> None:
    document = _safe_document()
    document["material"] = (
        "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-NOT-A-KEY\n-----END PRIVATE KEY-----"
    )
    with pytest.raises(AssertionError, match="private key material"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    "leak",
    [
        {"api_token": "SYNTHETIC-NOT-A-TOKEN"},
        {"private_key": "SYNTHETIC-NOT-A-KEY"},
        "password SYNTHETIC-NOT-A-PASSWORD",
        "Authorization: Bearer SYNTHETIC-NOT-A-TOKEN",
        "set account secret SYNTHETIC-NOT-A-SECRET",
    ],
)
def test_credential_scanner_mutations_fail_closed(leak: Any) -> None:
    document = _safe_document()
    document["payload"] = leak
    with pytest.raises(CanonicalizationError, match="Credential-bearing"):
        _assert_fixture_hygiene(document)


def test_misplaced_expected_rejection_data_fails_closed() -> None:
    document = _safe_document()
    misplaced = {
        "content_type": "text/plain",
        "data": "password SYNTHETIC-NOT-A-PASSWORD",
        "expected_error_contains": "Credential-bearing",
    }
    document["negative"] = deepcopy(misplaced)
    with pytest.raises(CanonicalizationError, match="Credential-bearing"):
        _assert_fixture_hygiene(document)


def test_expected_rejection_cannot_hide_nested_real_ip() -> None:
    document = _safe_document()
    document["negative"] = {
        "content_type": "application/json",
        "data": {"password": "SYNTHETIC", "address": "10.23.45.67"},
        "control_data": {"description": "synthetic safe control"},
        "expected_error_contains": "Credential-bearing",
    }
    with pytest.raises(AssertionError, match="IP literal"):
        _assert_fixture_hygiene(document)


def test_noncredential_expected_error_cannot_bypass_scanner() -> None:
    document = _safe_document()
    document["negative"] = {
        "content_type": "text/plain",
        "data": "password SYNTHETIC-NOT-A-PASSWORD",
        "control_data": "description synthetic safe control",
        "expected_error_contains": "Unsupported observation",
    }
    with pytest.raises(AssertionError, match="invalid expected credential"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    ("literal_key", "expected_error"),
    [("93.184.216.34", "IP literal"), ("00:11:22:33:44:55", "MAC literal")],
)
def test_literal_key_mutations_fail_closed(
    literal_key: str, expected_error: str
) -> None:
    document = _safe_document()
    document["payload"] = {literal_key: "synthetic value"}
    with pytest.raises(AssertionError, match=expected_error):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    ("literal_key", "expected_error"),
    [("10.23.45.67", "IP literal"), ("00:11:22:33:44:55", "MAC literal")],
)
def test_expected_rejection_cannot_hide_literal_keys(
    literal_key: str, expected_error: str
) -> None:
    document = _safe_document()
    document["negative"] = {
        "content_type": "application/json",
        "data": {literal_key: "synthetic value", "password": "SYNTHETIC"},
        "control_data": {"description": "synthetic safe control"},
        "expected_error_contains": "Credential-bearing",
    }
    with pytest.raises(AssertionError, match=expected_error):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    "leak",
    [
        {"hostname": "customer-core-prod-01"},
        {"serial": "CUSTOMER-SERIAL-12345"},
        {"site_id": "dc-east-prod"},
        {"organization_id": "acme-production"},
        {"domain_name": "corp.internal"},
        {"account_id": "account-1001"},
        {"tenant_id": "tenant-production"},
        {"customer_id": "customer-1001"},
        {"circuit_id": "MPLS-12345"},
        {"customer_hostname": "customer-core-prod-01"},
        {"identity": {"name": "customer-core-prod-01"}},
        {"customer_details": {"name": "customer-core-prod-01"}},
        {"external_customer": {"name": "customer-core-prod-01"}},
        {"billing_account": {"name": "account-1001"}},
        {"network_site": {"code": "dc-east-prod"}},
        {"device_identity": {"name": "customer-core-prod-01"}},
        {"hostname": "public"},
        {"serial": "public"},
    ],
)
def test_customer_identity_content_mutations_fail_closed(
    leak: dict[str, Any],
) -> None:
    document = _safe_document()
    document["payload"] = leak
    with pytest.raises(AssertionError, match="identity content"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    "leak",
    [
        {"running_config": "hostname customer-core-prod-01\ninterface eth0"},
        {"configuration": {"line": "hostname customer-core-prod-01"}},
        {"banner": "Customer production network"},
        {"raw_text": "customer production topology export"},
        {"raw_payload": "hostname customer-core-prod-01"},
        {"payload_raw": "hostname customer-core-prod-01"},
        {"config_raw": "hostname customer-core-prod-01"},
        {"config_raw_hash": "hostname customer-core-prod-01"},
    ],
)
def test_configuration_and_raw_content_mutations_fail_closed(
    leak: dict[str, Any],
) -> None:
    document = _safe_document()
    document["payload"] = leak
    with pytest.raises(AssertionError, match="configuration/raw content"):
        _assert_fixture_hygiene(document)


@pytest.mark.parametrize(
    ("target", "raw_text"),
    [
        ("o", "hostname customer-core-prod-01\ninterface eth0"),
        ("r", "! synthetic fixture\nhostname customer-core-prod-01"),
        ("r", "description synthetic fixture\nip domain name corp.internal"),
        ("o", "! synthetic fixture\nhostname customer-core-prod-01"),
        ("o", "description synthetic fixture\nip domain name corp.internal"),
        (
            "o",
            '<fixture synthetic="true"><hostname>customer-core-prod-01</hostname></fixture>',
        ),
        (
            "o",
            (
                "<!-- synthetic fixture --><fixture><serial-number>"
                "CUSTOMER-SERIAL-12345</serial-number></fixture>"
            ),
        ),
    ],
)
def test_raw_marker_cannot_hide_customer_identity_declarations(
    target: str, raw_text: str
) -> None:
    document = _safe_document()
    target = {"o": "observation", "r": "running_config"}[target]
    content_type = (
        "application/xml" if raw_text.lstrip().startswith("<") else "text/plain"
    )
    document[target] = (
        _raw_observation(raw_text, content_type)
        if target == "observation"
        else raw_text
    )
    expected_error = "raw identity declaration|observation configuration/raw data"
    with pytest.raises(AssertionError, match=expected_error):
        _assert_fixture_hygiene(document)


def test_expected_rejection_cannot_hide_customer_configuration() -> None:
    document = _safe_document()
    document["negative"] = {
        "content_type": "application/json",
        "data": {
            "password": "SYNTHETIC",
            "running_config": "hostname customer-core-prod-01",
        },
        "control_data": {"description": "synthetic safe control"},
        "expected_error_contains": "Credential-bearing",
    }
    with pytest.raises(AssertionError, match="configuration/raw content"):
        _assert_fixture_hygiene(document)


def test_marked_and_neutral_identity_configuration_controls_are_allowed() -> None:
    document = _safe_document()
    document["identities"] = {
        "hostname": "SYNTHETIC-CORE-01",
        "serial": "[REDACTED]",
        "site_id": "EXAMPLE-SITE",
        "organization_id": "SYN-ORG-001",
        "domain_name": "example.invalid",
        "account_id": "SAMPLE-ACCOUNT",
        "tenant_id": None,
        "customer_id": "SYNTH-CUSTOMER",
        "circuit_id": "SYN-CIRCUIT-001",
        "network_domain_id": "public",
    }
    document["running_config"] = (
        "! SYNTHETIC CONFIG\nhostname SYN-CORE-01\ninterface eth0\n"
        "ip domain name example.invalid"
    )
    document["configuration"] = {"line": "SAMPLE CONFIGURATION ONLY"}
    document["banner"] = "[REDACTED]"
    document["raw_text"] = "EXAMPLE raw topology output"
    document["digests"] = {"raw_sha256": "abc123", "config_hash": "def456"}
    document["observation"] = _raw_observation(
        "! SAMPLE CONFIG\nhostname EXAMPLE-CORE-01\ninterface eth0\n"
        "ip domain name example.invalid"
    )
    document["xml_observation"] = _raw_observation(
        '<fixture synthetic="true"><hostname>SYNTHETIC-CORE-01</hostname></fixture>',
        "application/xml",
    )
    _assert_fixture_hygiene(document)


def test_relationship_derived_identity_keys_are_neutral_controls() -> None:
    document = _safe_document()
    document["relation"] = {
        "multi_edge_identity": {
            "endpoint_key": "device:node-1|network:segment-1:198.51.100.0-24",
            "attachment_key": "interface:node-1:port1",
        }
    }
    _assert_fixture_hygiene(document)
