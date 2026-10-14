"""Convert action output into contract-validated topology observations."""
from __future__ import annotations

from typing import Any

import orjson

from services.network_topology import contracts
from services.network_topology.canonicalization import canonicalize_json, sha256_uri
from services.network_topology.collection.errors import CollectionError
from services.network_topology.redaction import scrub_action_output

TRUNCATION_MARKER = "...(已截断，原始 "
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
RAW_KIND = "raw.action_output"
RAW_SCHEMA = "aioe.network-topology.observation/raw.action_output/v1"


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


def _hashed(observation: dict[str, Any]) -> dict[str, Any]:
    digest = sha256_uri(canonicalize_json(observation["data"]))
    return {**observation, "raw_sha256": digest, "content_sha256": digest}


def raw_observation(*, observation_id, app_name, action_func, app_version, resource_id, output, observed_at):
    output = scrub_action_output(output)
    observation = _hashed({
        "observation_id": observation_id, "kind": RAW_KIND,
        "observation_status": "success", "source_method": "aioe_action",
        "content_type": "application/json", "data_schema": RAW_SCHEMA,
        "fact_plane": "operational", "command_or_api": f"{app_name}.{action_func}"[:512],
        "observed_at": observed_at,
        "data": {"app_name": app_name, "action_func": action_func,
                 "app_version": app_version or "", "resource_id": resource_id, "output": output},
    })
    contracts.validate_contract(observation, RAW_SCHEMA)
    return observation


def _kind_schemas() -> dict[str, str]:
    registry = contracts.load_registry("aioe.network-topology.registry/observation-kinds/v1")
    return {item["kind"]: item["schema_uri"] for item in registry["kinds"]}


def typed_observations(output, *, resource_id, item_id, observed_at) -> list[dict[str, Any]]:
    records = ((output or {}).get("data") or {}).get("records") if isinstance(output, dict) else None
    if not isinstance(records, list):
        return []
    schemas = _kind_schemas()
    result = []
    for index, record in enumerate(records):
        kinds = record.get("kinds") if isinstance(record, dict) else None
        if not kinds or not isinstance(kinds[0], str) or kinds[0] not in schemas or kinds[0] == RAW_KIND:
            continue
        candidate = _hashed({
            "observation_id": f"{item_id}:{resource_id}:{index}", "kind": kinds[0],
            "observation_status": "success", "source_method": "vendor_api",
            "content_type": "application/json", "data_schema": schemas[kinds[0]],
            "command_or_api": str(record.get("command_or_api") or kinds[0])[:512],
            "observed_at": observed_at, "data": record.get("data"),
        })
        # The packaged schema owns the allowed planes; validate each declared
        # option so its structural constraints remain authoritative as well.
        schema = contracts.load_schema(schemas[kinds[0]])
        for plane in schema.get("x-fact-planes", []):
            candidate["fact_plane"] = plane
            try:
                contracts.validate_contract(candidate, schemas[kinds[0]])
            except contracts.ContractError:
                continue
            result.append(candidate)
            break
    return result
