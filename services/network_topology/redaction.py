"""Credential removal at the topology payload persistence boundary."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from xml.etree import ElementTree

from services.network_topology.canonicalization import (
    CanonicalizationError,
    _canonicalize_xml_engine,
    _validate_xml_text,
    canonicalize_json,
    canonicalize_text,
    sha256_uri,
)
from services.network_topology import canonicalization


REDACTION_MARKER = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(^|[^a-z0-9])(authorization|cookie|credential|password|passwd|private.?key|"
    r"psk|pre.?shared.?key|secret|snmp.?community|tacacs.?key|radius.?key|token|"
    r"username)([^a-z0-9]|$)",
    re.IGNORECASE,
)
_SENSITIVE_LINE = re.compile(
    r"(?im)^.*(?:authorization\s*:|cookie\s*:|api[-_ ]?token|password|passwd|"
    r"private[-_ ]?key|pre[-_ ]?shared[-_ ]?key|\bpsk\b|radius[-_ ]?key|"
    r"snmp(?:-server)?\s+community|tacacs\S*\s+key|username\s+\S+|"
    r"\bsecret\b).*(?:\r?\n|$)"
)
_OBSERVATION_HASH_FIELDS = frozenset(
    {"content_type", "data", "raw_sha256", "content_sha256"}
)


def scrub_action_output(value: Any) -> Any:
    """Recursively remove credential-bearing keys and redact sensitive lines."""
    if isinstance(value, dict):
        return {
            key: scrub_action_output(child)
            for key, child in value.items()
            if not _SENSITIVE_KEY.search(str(key))
            and not canonicalization.SENSITIVE_NAME.search(canonicalization._normalize_name(str(key)))
        }
    if isinstance(value, list):
        return [scrub_action_output(child) for child in value]
    if isinstance(value, str):
        text = canonicalization.SENSITIVE_TEXT.sub(f"{REDACTION_MARKER}\n", value)
        text = canonicalization.SENSITIVE_XML.sub(REDACTION_MARKER, text)
        return _SENSITIVE_LINE.sub(f"{REDACTION_MARKER}\n", text)
    return value


def validate_then_redact(envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate raw observations, then return a detached credential-free envelope."""
    if not isinstance(envelope, dict):
        raise CanonicalizationError("Snapshot envelope must be an object")
    _validate_observations(envelope)
    return _redact_value(deepcopy(envelope))


def _validate_observations(value: Any) -> None:
    if isinstance(value, dict):
        if _is_observation_candidate(value):
            missing = _OBSERVATION_HASH_FIELDS - value.keys()
            if missing:
                raise CanonicalizationError("Observation hash fields are incomplete")
            _validate_observation_hashes(value)
        for child in value.values():
            _validate_observations(child)
        return
    if isinstance(value, list):
        for child in value:
            _validate_observations(child)


def _is_observation_candidate(value: dict[str, Any]) -> bool:
    fields = _OBSERVATION_HASH_FIELDS & value.keys()
    return "observation_id" in value and bool(fields)


def _validate_observation_hashes(observation: dict[str, Any]) -> None:
    content_type = observation["content_type"]
    data = observation["data"]
    raw = canonicalize_json(data) if content_type == "application/json" else _raw_text(data)
    canonical = _canonical_content(content_type, data)
    _require_hash("raw_sha256", observation["raw_sha256"], raw)
    _require_hash("content_sha256", observation["content_sha256"], canonical)


def _canonical_content(content_type: str, data: Any) -> bytes:
    if content_type == "application/json":
        return canonicalize_json(data)
    if content_type == "text/plain":
        return canonicalize_text(data)
    if content_type == "application/xml":
        _validate_xml_text(data)
        return _canonicalize_xml_engine(data, 2)
    raise CanonicalizationError(f"Unsupported observation content type: {content_type}")


def _raw_text(value: Any) -> bytes:
    if not isinstance(value, str):
        raise CanonicalizationError("Text/XML observation data must be a string")
    return value.encode()


def _require_hash(name: str, expected: Any, value: bytes) -> None:
    if expected != sha256_uri(value):
        raise CanonicalizationError(f"{name} mismatch")


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(child) for child in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    return _SENSITIVE_LINE.sub(f"{REDACTION_MARKER}\n", value).rstrip("\n")


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    result = {}
    observation = _OBSERVATION_HASH_FIELDS <= value.keys()
    for key, child in value.items():
        if _SENSITIVE_KEY.search(str(key)):
            continue
        if observation and key == "data":
            result[key] = _redact_observation_data(value["content_type"], child)
        else:
            result[key] = _redact_value(child)
    return result


def _redact_observation_data(content_type: str, data: Any) -> Any:
    if content_type == "application/json":
        return _redact_value(data)
    if content_type == "text/plain":
        return _redact_text(data)
    if content_type == "application/xml":
        return _redact_xml(data)
    raise CanonicalizationError(f"Unsupported observation content type: {content_type}")


def _redact_xml(value: str) -> str:
    _validate_xml_text(value)
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError as exc:
        raise CanonicalizationError("XML observation data is not well formed") from exc
    if _SENSITIVE_KEY.search(_local_name(root.tag)):
        return REDACTION_MARKER
    _redact_xml_element(root)
    return ElementTree.tostring(root, encoding="unicode")


def _redact_xml_element(element: ElementTree.Element) -> None:
    for name in tuple(element.attrib):
        if _SENSITIVE_KEY.search(_local_name(name)):
            del element.attrib[name]
    element.text = _redact_optional_text(element.text)
    element.tail = _redact_optional_text(element.tail)
    for child in tuple(element):
        if _SENSITIVE_KEY.search(_local_name(child.tag)):
            element.remove(child)
        else:
            _redact_xml_element(child)


def _redact_optional_text(value: str | None) -> str | None:
    return _redact_text(value) if value is not None else None


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]
