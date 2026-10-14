"""Versioned network-topology JSON contract loading and validation."""

import json
import re
from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

SCHEMA_ROOT = Path(__file__).with_name("schemas")
REGISTRY_ROOT = Path(__file__).with_name("registries")
SCHEMA_ID_BASE = "https://schemas.aioe.local/"
JSON_SCHEMA_DRAFT_URI = "https://json-schema.org/draft/2020-12/schema"
REGISTRY_URIS = {
    "aioe.network-topology.registry/observation-kinds/v1",
    "aioe.network-topology.registry/capability-profiles/v1",
    "aioe.network-topology.registry/parser-bundle/v1",
    "aioe.network-topology.registry/vendor-action-mapping/v1",
}
PARSER_BUNDLE_ID = "aioe.network-topology.parser-bundle/v1"
PARSER_REGISTRY_URI = "aioe.network-topology.registry/parser-bundle/v1"
REDACTION_VERSION = "aioe.network-topology.redaction/v1"
PARSER_STATUSES = {"contract_only", "planned", "ready"}
RFC3339_DATETIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
FORMAT_CHECKER = FormatChecker()
LLM_POLICY = {
    "prompt_policy_id": "aioe.network-topology.prompt/fact-candidates/v1",
    "model_selection": "deployment_allowlist",
    "tools_allowed": False,
    "network_allowed": False,
    "candidate_output_schema": "aioe.network-topology.llm-fact-candidates/v1",
    "chunk_input_schema": "aioe.network-topology.llm-chunk/v1",
    "formal_facts_schema": "aioe.network-topology.facts/v1",
}


@FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if RFC3339_DATETIME_RE.fullmatch(value) is None:
        return False
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


class ContractError(ValueError):
    """Raised when a contract URI or payload is not supported."""


def iter_schemas() -> Iterator[dict[str, Any]]:
    """Yield every packaged schema in a stable order."""
    for path in sorted(SCHEMA_ROOT.rglob("*.schema.json")):
        yield _read_json(path)


def iter_registries() -> Iterator[dict[str, Any]]:
    """Yield every packaged registry in a stable order."""
    for path in sorted(REGISTRY_ROOT.glob("*.json")):
        yield _read_json(path)


@lru_cache(maxsize=1)
def _schema_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for schema in iter_schemas():
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str) or not schema_id.startswith(SCHEMA_ID_BASE):
            raise ContractError("Schema $id must use the packaged absolute base URI")
        uri = schema_id.removeprefix(SCHEMA_ID_BASE)
        if schema.get("$schema") != JSON_SCHEMA_DRAFT_URI:
            raise ContractError(f"Schema {uri} must declare JSON Schema Draft 2020-12")
        if uri in catalog:
            raise ContractError(f"Duplicate contract URI: {uri}")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ContractError(f"Invalid schema {uri}: {exc.message}") from exc
        catalog[uri] = schema
    return catalog


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in _schema_catalog().values()
    ]
    return Registry().with_resources(resources)


def _iter_schema_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for keyword in ("$ref", "$dynamicRef"):
            ref = value.get(keyword)
            if isinstance(ref, str):
                yield ref
        for child in value.values():
            yield from _iter_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_schema_refs(child)


def _validate_schema_references() -> None:
    registry = _schema_registry()
    for schema_uri, schema in _schema_catalog().items():
        resolver = registry.resolver(schema["$id"])
        for ref in _iter_schema_refs(schema):
            try:
                resolver.lookup(ref)
            except Unresolvable as exc:
                raise ContractError(
                    f"Unresolvable schema reference in {schema_uri}: {ref}"
                ) from exc


def load_schema(schema_uri: str) -> dict[str, Any]:
    """Load a schema by its exact URI, rejecting aliases and unknown versions."""
    try:
        return deepcopy(_schema_catalog()[schema_uri])
    except KeyError as exc:
        raise ContractError(f"Unsupported schema URI: {schema_uri}") from exc


@lru_cache(maxsize=1)
def _registry_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for registry in iter_registries():
        uri = registry.get("schema_version")
        if not isinstance(uri, str) or uri not in REGISTRY_URIS:
            raise ContractError(f"Unsupported registry URI: {uri}")
        if uri in catalog:
            raise ContractError(f"Duplicate registry URI: {uri}")
        catalog[uri] = registry
    if set(catalog) != REGISTRY_URIS:
        raise ContractError("Packaged network-topology registry set is incomplete")
    return catalog


def load_registry(registry_uri: str) -> dict[str, Any]:
    """Load one exact registry version and reject aliases or unknown versions."""
    try:
        return deepcopy(_registry_catalog()[registry_uri])
    except KeyError as exc:
        raise ContractError(f"Unsupported registry URI: {registry_uri}") from exc


def validate_contract(payload: Any, schema_uri: str) -> None:
    """Validate a payload against one exact, packaged schema version."""
    schema = load_schema(schema_uri)
    validator = Draft202012Validator(
        schema,
        registry=_schema_registry(),
        format_checker=FORMAT_CHECKER,
    )
    try:
        validator.validate(payload)
    except ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ContractError(f"{schema_uri} at {location}: {exc.message}") from exc
    except Unresolvable as exc:
        raise ContractError(f"Unresolvable schema reference in {schema_uri}") from exc


def validate_collection_admission(payload: Any) -> None:
    """Validate the collection schema and its cross-field Profile binding."""
    schema_uri = "aioe.network-topology.collection/v2"
    validate_contract(payload, schema_uri)
    if (
        payload["topology_profile_revision"]
        != payload["target_manifest"]["profile_revision"]
    ):
        raise ContractError(
            "collection topology_profile_revision must equal "
            "target_manifest.profile_revision"
        )


def validate_versioned_contract(payload: Any) -> str:
    """Validate an envelope using its exact schema_version URI."""
    if not isinstance(payload, dict):
        raise ContractError("Versioned contract payload must be an object")
    schema_uri = payload.get("schema_version")
    if not isinstance(schema_uri, str) or not schema_uri:
        raise ContractError("Versioned contract payload is missing schema_version")
    validate_contract(payload, schema_uri)
    return schema_uri


def smoke_all_contracts() -> dict[str, int]:
    """Load all schemas and registries and validate embedded examples."""
    example_count = 0
    catalog = _schema_catalog()
    _validate_schema_references()
    for schema_uri in catalog:
        schema = load_schema(schema_uri)
        for example in schema.get("examples", []):
            validate_contract(example, schema_uri)
            example_count += 1
    registry_result = validate_registry_integrity()
    return {
        "schema_count": len(catalog),
        "example_count": example_count,
        "registry_count": registry_result["registry_count"],
    }


def validate_registry_integrity() -> dict[str, int]:
    """Fail closed when any packaged registry reference is invalid or ambiguous."""
    return deepcopy(_validated_registry_summary())


@lru_cache(maxsize=1)
def _validated_registry_summary() -> dict[str, int]:
    kinds_registry = load_registry(
        "aioe.network-topology.registry/observation-kinds/v1"
    )
    capability_registry = load_registry(
        "aioe.network-topology.registry/capability-profiles/v1"
    )
    parser_registry = load_registry("aioe.network-topology.registry/parser-bundle/v1")
    action_registry = load_registry(
        "aioe.network-topology.registry/vendor-action-mapping/v1"
    )
    kinds = _validate_observation_registry(kinds_registry)
    profiles = _validate_capability_registry(capability_registry, kinds)
    _validate_parser_registry(parser_registry, kinds)
    _validate_action_registry(action_registry, profiles, kinds)
    return {
        "registry_count": len(_registry_catalog()),
        "observation_kind_count": len(kinds),
        "capability_count": len(profiles),
    }


def resolve_capability(
    device_type: str, vendor: str, platform: str, os_family: str
) -> dict[str, Any]:
    """Resolve exactly one capability profile or fail closed."""
    validate_registry_integrity()
    registry = load_registry("aioe.network-topology.registry/capability-profiles/v1")
    values = (device_type, vendor, platform, os_family)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ContractError("Capability match fields must be non-empty strings")
    key = tuple(value.strip().lower() for value in values)
    runtime_profiles = [
        profile
        for profile in registry["profiles"]
        if profile.get("fixture_only") is not True
    ]
    return deepcopy(_select_capability(runtime_profiles, key))


def resolve_vendor_action(
    capability_profile_id: str, observation_kind: str, *, require_ready: bool = True
) -> dict[str, Any]:
    """Resolve a frozen action target, rejecting undelivered planned mappings."""
    validate_registry_integrity()
    registry = load_registry("aioe.network-topology.registry/vendor-action-mapping/v1")
    mapping = next(
        (
            item
            for item in registry["mappings"]
            if item["capability_profile_id"] == capability_profile_id
        ),
        None,
    )
    action_id = (
        None if mapping is None else mapping["kind_action_map"].get(observation_kind)
    )
    if mapping is None or action_id is None:
        raise ContractError("No vendor action mapping for capability and kind")
    if require_ready and mapping["delivery_status"] != "ready":
        raise ContractError("Observation action mapping is not delivered")
    return {
        **mapping,
        "observation_kind": observation_kind,
        "action_id": action_id,
    }


def _validate_observation_registry(registry: dict[str, Any]) -> set[str]:
    entries = registry.get("kinds")
    if not isinstance(entries, list) or not entries:
        raise ContractError("Observation registry must contain kinds")
    kinds = {entry.get("kind") for entry in entries}
    if None in kinds or len(kinds) != len(entries):
        raise ContractError("Observation kinds must be non-empty and unique")
    for entry in entries:
        schema = load_schema(entry.get("schema_uri"))
        constraints = schema.get("allOf", [{}, {}])[1].get("properties", {})
        if constraints.get("kind", {}).get("const") != entry["kind"]:
            raise ContractError(f"Observation kind/schema mismatch: {entry['kind']}")
        if not schema.get("x-max-bytes"):
            raise ContractError(f"Observation size limit missing: {entry['kind']}")
    extension = registry.get("vendor_extension", {})
    load_schema(extension.get("schema_uri"))
    if extension.get("parser_profile_required") is not True:
        raise ContractError("Vendor extensions must require an explicit parser profile")
    return kinds


def _validate_capability_registry(
    registry: dict[str, Any], kinds: set[str]
) -> dict[str, dict[str, Any]]:
    validate_contract(
        registry.get("default_profile"), "aioe.network-topology.profile/v1"
    )
    profiles = registry.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ContractError("Capability registry must contain profiles")
    by_id = {profile.get("id"): profile for profile in profiles}
    fields = ("device_type", "vendor", "platform", "os_family")
    match_keys = [
        tuple(str(profile.get("match", {}).get(field, "")).lower() for field in fields)
        for profile in profiles
    ]
    if (
        None in by_id
        or len(by_id) != len(profiles)
        or len(set(match_keys)) != len(profiles)
    ):
        raise ContractError("Capability IDs and match keys must be unique")
    for profile in profiles:
        match = profile.get("match", {})
        if set(match) != {"device_type", "vendor", "platform", "os_family"}:
            raise ContractError(f"Incomplete capability key: {profile['id']}")
        _validate_fixture_capability(profile, match)
        required = set(profile.get("required_kinds", []))
        optional = set(profile.get("optional_kinds", []))
        if profile["id"] == "aioe-action-collection/v1":
            if (
                match != {
                    "device_type": "aioe-action",
                    "vendor": "aioe-action",
                    "platform": "*",
                    "os_family": "*",
                }
                or profile.get("required_kinds") != ["raw.action_output"]
                or optional != kinds - {"legacy.running_config", "raw.action_output"}
            ):
                raise ContractError(
                    "Action collection capability must match its frozen contract"
                )
        elif not {"system.identity", "interface.inventory"}.issubset(required):
            raise ContractError(
                f"Identity/interfaces must be required: {profile['id']}"
            )
        if required & optional or not (required | optional) <= kinds:
            raise ContractError(f"Invalid capability kind reference: {profile['id']}")
    return by_id


def _validate_fixture_capability(
    profile: dict[str, Any], match: dict[str, Any]
) -> None:
    fixture_only = profile.get("fixture_only")
    if fixture_only is None:
        return
    if fixture_only is not True or match != {
        "device_type": "switch",
        "vendor": "synthetic",
        "platform": "contract-neutral",
        "os_family": "synthetic-os",
    }:
        raise ContractError("Fixture-only capability must remain vendor-neutral")


def _validate_parser_registry(registry: dict[str, Any], kinds: set[str]) -> None:
    if set(registry) != {
        "schema_version",
        "bundle_id",
        "redaction_version",
        "deterministic_parsers",
        "llm_policy",
    }:
        raise ContractError("Parser bundle fields are incomplete or unsupported")
    if registry.get("schema_version") != PARSER_REGISTRY_URI:
        raise ContractError("Parser registry version is not supported")
    if registry.get("bundle_id") != PARSER_BUNDLE_ID:
        raise ContractError("Parser bundle ID is not supported")
    if registry.get("redaction_version") != REDACTION_VERSION:
        raise ContractError("Parser redaction version is not supported")
    parsers = registry.get("deterministic_parsers", [])
    covered = [
        kind for parser in parsers for kind in parser.get("observation_kinds", [])
    ]
    parser_ids = {parser.get("parser_id") for parser in parsers}
    if set(covered) != kinds or len(covered) != len(set(covered)):
        raise ContractError("Every observation kind must have exactly one parser")
    if len(parser_ids) != len(parsers) or any(
        not _is_non_empty_string(parser_id) for parser_id in parser_ids
    ):
        raise ContractError("Parser IDs must be non-empty and unique")
    for parser in parsers:
        if set(parser) != {
            "parser_id",
            "parser_version",
            "implementation_status",
            "observation_kinds",
        }:
            raise ContractError("Deterministic parser fields are invalid")
        if (
            type(parser.get("parser_version")) is not int
            or parser["parser_version"] < 1
        ):
            raise ContractError("Every deterministic parser must freeze a version")
        if parser.get("implementation_status") not in PARSER_STATUSES:
            raise ContractError("Deterministic parser status is invalid")
    policy = registry.get("llm_policy", {})
    if policy != LLM_POLICY:
        raise ContractError("LLM parser policy does not match the frozen contract")
    for field in (
        "candidate_output_schema",
        "chunk_input_schema",
        "formal_facts_schema",
    ):
        load_schema(LLM_POLICY[field])


def _validate_action_registry(
    registry: dict[str, Any], profiles: dict[str, dict[str, Any]], kinds: set[str]
) -> None:
    mappings = registry.get("mappings", [])
    source = registry.get("audited_source", {})
    if (
        source.get("repository") != "../aioe_apps"
        or re.fullmatch(r"[0-9a-f]{40}", source.get("revision", "")) is None
        or source.get("semantic_versions_declared") is not False
        or source.get("observation_v2_compatible_actions") != 0
    ):
        raise ContractError("Vendor action audit evidence is incomplete")
    mapped_ids = {mapping.get("capability_profile_id") for mapping in mappings}
    if mapped_ids != set(profiles) or len(mapped_ids) != len(mappings):
        raise ContractError("Every capability must have exactly one action mapping")
    for mapping in mappings:
        _validate_action_mapping(mapping, profiles, kinds)


def _validate_action_mapping(
    mapping: dict[str, Any], profiles: dict[str, dict[str, Any]], kinds: set[str]
) -> None:
    profile = profiles[mapping["capability_profile_id"]]
    expected = set(profile["required_kinds"]) | set(profile["optional_kinds"])
    action_map = mapping.get("kind_action_map", {})
    if set(action_map) != expected or not expected <= kinds:
        raise ContractError("Action mapping kind coverage is incomplete")
    target_package = mapping.get("target_package")
    planned_action = mapping.get("planned_action_id")
    if not _is_non_empty_string(target_package) or not _is_non_empty_string(
        planned_action
    ):
        raise ContractError("Vendor target package and action must be non-empty")
    if not action_map or set(action_map.values()) != {planned_action}:
        raise ContractError("Each observation kind must resolve to an action")
    status = mapping.get("delivery_status")
    fixture_only = profile.get("fixture_only") is True
    if status not in {"contract_only", "planned", "ready"}:
        raise ContractError("Invalid vendor action delivery status")
    minimum = _parse_semver(mapping.get("planned_minimum_package_version"))
    current_value = mapping.get("verified_current_package_version")
    if fixture_only:
        _validate_fixture_only_action(mapping, status, current_value)
        return
    if status == "contract_only":
        raise ContractError(
            "Runtime capability cannot use contract-only action mapping"
        )
    if status == "planned" and current_value is not None:
        raise ContractError("Planned action cannot claim a verified package version")
    if status == "ready" and _parse_semver(current_value) < minimum:
        raise ContractError("Verified package version is below the planned minimum")
    if any(
        item.get("classification") != "connector_material_only"
        for item in mapping.get("connector_evidence", [])
    ):
        raise ContractError(
            "Existing connectors cannot claim observation v2 compatibility"
        )


def _validate_fixture_only_action(
    mapping: dict[str, Any], status: str, current_value: Any
) -> None:
    if (
        status != "contract_only"
        or mapping["target_package"] != "fixture-only:no-runtime-package"
        or mapping["planned_action_id"] != "fixture_contract_only"
        or current_value is not None
        or mapping.get("connector_evidence") != []
    ):
        raise ContractError("Fixture-only capability must remain contract-only")


def _parse_semver(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ContractError("Package version must use numeric SemVer")
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if match is None:
        raise ContractError("Package version must use numeric SemVer")
    return tuple(int(part) for part in match.groups())


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _select_capability(
    profiles: list[dict[str, Any]], key: tuple[str, str, str, str]
) -> dict[str, Any]:
    fields = ("device_type", "vendor", "platform", "os_family")
    matches: list[tuple[int, dict[str, Any]]] = []
    for profile in profiles:
        values = tuple(str(profile["match"][field]).lower() for field in fields)
        if all(
            expected == "*" or expected == actual
            for expected, actual in zip(values, key)
        ):
            matches.append((sum(value != "*" for value in values), profile))
    if not matches:
        raise ContractError("No capability profile matches the resource")
    best_score = max(score for score, _profile in matches)
    best = [profile for score, profile in matches if score == best_score]
    if len(best) != 1:
        raise ContractError("Capability profile match is ambiguous")
    return best[0]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Cannot load schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"Schema must be a JSON object: {path}")
    return value
