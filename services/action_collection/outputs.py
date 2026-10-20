"""Bounded, credential-free parsing of AIOE action output."""
from __future__ import annotations

from typing import Any

import orjson

from services.action_collection.errors import CollectionError
from services.network_topology.redaction import scrub_action_output

__all__ = ("MAX_OUTPUT_BYTES", "TRUNCATION_MARKER", "parse_action_output", "scrub_action_output")

TRUNCATION_MARKER = "...(已截断，原始 "
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def parse_action_output(result_text: str | None) -> Any:
    if not isinstance(result_text, str) or not result_text:
        raise CollectionError("COLLECTION_OUTPUT_NOT_JSON")
    if TRUNCATION_MARKER in result_text:
        raise CollectionError("COLLECTION_OUTPUT_TRUNCATED")
    if len(result_text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise CollectionError("COLLECTION_OUTPUT_TOO_LARGE")
    try:
        parsed = orjson.loads(result_text)
    except orjson.JSONDecodeError:
        raise CollectionError("COLLECTION_OUTPUT_NOT_JSON") from None
    return scrub_action_output(parsed)
