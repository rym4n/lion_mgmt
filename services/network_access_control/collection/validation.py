"""Schema and evidence checks shared by agent submission and the normalize stage."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).with_name("schemas") / "policy-normalized-v1.schema.json"
MAX_LEAF_NODES = 100_000
_WHITESPACE = re.compile(r"\s+")
SEPARATOR = "\x00"


@lru_cache(maxsize=1)
def document_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(document_schema())


def squash(text: str) -> str:
    return _WHITESPACE.sub("", text)


def evidence_haystack(observations: list[dict]) -> str:
    """Return serialized outputs and string leaves, whitespace removed, NUL-separated."""
    corpus: list[str] = []
    for observation in observations:
        output = (observation.get("data") or {}).get("output")
        corpus.append(squash(json.dumps(output, ensure_ascii=False)))
        stack, visited = [output], 0
        while stack and visited < MAX_LEAF_NODES:
            value = stack.pop()
            visited += 1
            if isinstance(value, str):
                corpus.append(squash(value))
            elif isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return SEPARATOR.join(text for text in corpus if text)


def validate_document(document: Any, *, resource_id: str, observations: list[dict], max_rules: int) -> list[str]:
    if not isinstance(document, dict):
        return ["document must be an object"]
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
              for e in sorted(_validator().iter_errors(document), key=lambda e: list(e.absolute_path))[:20]]
    if errors:
        return errors
    if document["resource_id"] != resource_id:
        return ["resource_id does not match the job resource"]
    policies = document["policies"]
    if len(policies) > max_rules:
        return [f"policies exceed max_rules_per_device ({max_rules})"]
    haystack = evidence_haystack(observations)
    seen: set[str] = set()
    for index, item in enumerate(policies):
        key = item["policy_key"]
        if key in seen:
            errors.append(f"policies/{index}/policy_key duplicated: {key[:64]}")
        seen.add(key)
        needle = squash(item["raw_text"])
        if not needle or SEPARATOR in needle or needle not in haystack:
            errors.append(f"policies/{index}/raw_text not found in device output")
        if len(errors) >= 20:
            break
    return errors
