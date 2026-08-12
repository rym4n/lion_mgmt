"""Strict evidence locator validation and resolution."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree


XPATH_NAME = r"(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*"
XPATH_STEP = rf"{XPATH_NAME}(?:\[[1-9][0-9]*\])?"
RESTRICTED_XPATH = re.compile(
    rf"^/(?:{XPATH_STEP})(?:/{XPATH_STEP})*(?:/@{XPATH_NAME})?$"
)
NAMESPACE_PREFIX = re.compile(r"^[A-Za-z_][\w.-]*$")
ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*$")


class LocatorError(ValueError):
    """Raised when evidence cannot be safely and exactly located."""


def resolve_evidence(content: Any, locator: dict[str, Any]) -> Any:
    locator_type = locator.get("type")
    if locator_type == "lines":
        return resolve_line_range(content, locator.get("start"), locator.get("end"))
    if locator_type == "json_pointer":
        return resolve_json_pointer(content, locator.get("pointer"))
    if locator_type == "xpath":
        return resolve_restricted_xpath(
            content, locator.get("path"), locator.get("namespaces")
        )
    raise LocatorError("Unknown evidence locator type")


def resolve_line_range(content: str, start: Any, end: Any) -> str:
    if not isinstance(content, str):
        raise LocatorError("Line evidence requires text content")
    if not _is_positive_int(start) or not _is_positive_int(end) or start > end:
        raise LocatorError("Line range must be a 1-based closed interval")
    lines = content.splitlines()
    if end > len(lines):
        raise LocatorError("Line range exceeds observation content")
    return "\n".join(lines[start - 1 : end])


def resolve_json_pointer(document: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or len(pointer) > 1024:
        raise LocatorError("JSON Pointer must be a bounded string")
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise LocatorError("JSON Pointer must be empty or start with '/'")
    current = document
    for encoded in pointer[1:].split("/"):
        current = _resolve_pointer_token(current, _decode_pointer_token(encoded))
    return current


def resolve_restricted_xpath(xml: str, path: Any, namespaces: Any) -> list[Any]:
    if not isinstance(xml, str):
        raise LocatorError("XPath evidence requires XML text")
    if (
        not isinstance(path, str)
        or len(path) > 1024
        or not RESTRICTED_XPATH.fullmatch(path)
    ):
        raise LocatorError(
            "XPath is outside the restricted absolute child-path grammar"
        )
    checked_namespaces = _validate_namespaces(namespaces)
    used_prefixes = set(re.findall(r"(?:^|/|@)([A-Za-z_][\w.-]*):", path))
    if not used_prefixes.issubset(checked_namespaces):
        raise LocatorError("XPath uses an undeclared namespace prefix")
    root = _parse_xml(xml)
    try:
        result = root.getroottree().xpath(path, namespaces=checked_namespaces)
    except etree.XPathError as exc:
        raise LocatorError("XPath could not be evaluated") from exc
    if not result:
        raise LocatorError("XPath did not resolve evidence")
    return result


def _resolve_pointer_token(current: Any, token: str) -> Any:
    if isinstance(current, dict):
        try:
            return current[token]
        except KeyError as exc:
            raise LocatorError("JSON Pointer object member does not exist") from exc
    if isinstance(current, list):
        if not ARRAY_INDEX.fullmatch(token):
            raise LocatorError("JSON Pointer array index is invalid")
        index = int(token)
        if index >= len(current):
            raise LocatorError("JSON Pointer array index is out of range")
        return current[index]
    raise LocatorError("JSON Pointer traverses a scalar value")


def _decode_pointer_token(token: str) -> str:
    if re.search(r"~(?:[^01]|$)", token):
        raise LocatorError("JSON Pointer contains an invalid escape")
    return token.replace("~1", "/").replace("~0", "~")


def _validate_namespaces(namespaces: Any) -> dict[str, str]:
    if not isinstance(namespaces, dict):
        raise LocatorError("XPath namespaces must be an object")
    for prefix, uri in namespaces.items():
        if not isinstance(prefix, str) or not NAMESPACE_PREFIX.fullmatch(prefix):
            raise LocatorError("XPath namespace prefix is invalid")
        if not isinstance(uri, str) or not uri:
            raise LocatorError("XPath namespace URI is invalid")
    return namespaces


def _parse_xml(xml: str) -> etree._Element:
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise LocatorError("DTD and entity declarations are forbidden")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        return etree.fromstring(xml.encode(), parser)
    except etree.XMLSyntaxError as exc:
        raise LocatorError("XML evidence is not well formed") from exc


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
