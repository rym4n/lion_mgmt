"""Fail-closed resource merge and AnalysisRun payload boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import orjson

from services.network_topology.canonicalization import canonicalize_json, sha256_uri
from services.network_topology.contracts import ContractError, validate_contract

from .candidate_conversion import ConversionResult, FACT_ARRAYS, FACTS_SCHEMA


class FactMergeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MergeResult:
    facts: dict[str, Any]
    validation_report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedAnalysisPayloads:
    facts: Any
    report: Any


def merge_fact_results(
    results: tuple[ConversionResult | dict[str, Any], ...],
    *,
    expected_resource_ids: tuple[str, ...],
    required_chunk_ids: tuple[str, ...],
    successful_chunk_ids: tuple[str, ...],
    manifest_non_empty: bool,
) -> MergeResult:
    """Merge only formal Facts after all run-wide completeness gates pass."""
    _validate_chunks(required_chunk_ids, successful_chunk_ids)
    documents = [_document(item) for item in results]
    if not documents:
        if manifest_non_empty:
            raise FactMergeError("non-empty manifest parsed to no facts")
        return _empty_merge()
    _validate_bindings(documents)
    facts, conflicts = _merge_documents(documents)
    _validate_provenance_closure(facts)
    _validate_expected_devices(facts, expected_resource_ids)
    if manifest_non_empty and not any(facts[name] for name in FACT_ARRAYS):
        raise FactMergeError("non-empty manifest parsed to no facts")
    try:
        validate_contract(facts, FACTS_SCHEMA)
    except ContractError as exc:
        raise FactMergeError("merged facts schema validation failed") from exc
    report = _report(results, conflicts)
    return MergeResult(facts, report)


def _validate_chunks(required, successful):
    missing = sorted(set(required) - set(successful))
    if missing:
        raise FactMergeError("required chunk failed")


def _document(item):
    document = item.facts if isinstance(item, ConversionResult) else item
    try:
        validate_contract(document, FACTS_SCHEMA)
    except ContractError as exc:
        raise FactMergeError("resource facts schema validation failed") from exc
    return deepcopy(document)


def _validate_bindings(documents):
    snapshots = {item["snapshot_id"] for item in documents}
    runs = {item["analysis_run_id"] for item in documents}
    if len(snapshots) != 1 or len(runs) != 1:
        raise FactMergeError("resource facts have inconsistent run binding")


def _merge_documents(documents):
    first = documents[0]
    output = {"schema_version": FACTS_SCHEMA, "snapshot_id": first["snapshot_id"],
              "analysis_run_id": first["analysis_run_id"]}
    conflicts = []
    for family in (*FACT_ARRAYS, "unresolved_references", "provenance_catalog"):
        output[family], family_conflicts = _merge_family(documents, family)
        conflicts.extend(family_conflicts)
    return output, conflicts


def _merge_family(documents, family):
    values = [item for document in documents for item in document[family]]
    by_id: dict[str, list[dict[str, Any]]] = {}
    for item in values:
        by_id.setdefault(item["id"], []).append(item)
    merged = []
    conflicts = []
    for identifier in sorted(by_id):
        variants = by_id[identifier]
        canonical = {orjson.dumps(value, option=orjson.OPT_SORT_KEYS) for value in variants}
        if len(canonical) == 1:
            merged.append(deepcopy(variants[0]))
        else:
            conflicts.append({"code": "CONFLICTING_FACT_SOURCES", "family": family,
                              "id": identifier, "source_count": len(variants)})
    return merged, conflicts


def _validate_provenance_closure(facts):
    catalog = {item["id"] for item in facts["provenance_catalog"]}
    referenced = set()
    for family in FACT_ARRAYS:
        for fact in facts[family]:
            referenced.update(fact["provenance_ids"])
    for item in facts["unresolved_references"]:
        referenced.update(item["provenance_ids"])
    if not referenced.issubset(catalog) or any(not fact["provenance_ids"]
                                                for family in FACT_ARRAYS
                                                for fact in facts[family]):
        raise FactMergeError("formal fact provenance is missing")


def _validate_expected_devices(facts, expected):
    counts = {resource_id: 0 for resource_id in expected}
    for device in facts["devices"]:
        for resource_id in device["resource_ids"]:
            if resource_id in counts and device["id"] == f"device:{resource_id}":
                counts[resource_id] += 1
    if any(count != 1 for count in counts.values()):
        raise FactMergeError("every expected resource needs one canonical device")


def _report(results, conflicts):
    validation = [deepcopy(item) for result in results
                  if isinstance(result, ConversionResult)
                  for item in result.validation_items]
    bindings = [_result_binding(item) for item in results
                if isinstance(item, ConversionResult)]
    llm_summaries = [deepcopy(item.llm_summary) for item in results
                     if isinstance(item, ConversionResult) and item.llm_summary]
    return {"valid": not conflicts, "errors": [], "unresolved": validation,
            "conflicts": conflicts, "bindings": sorted(
                bindings, key=lambda item: (item["resource_id"], item["chunk_id"])
            ), "llm_summaries": llm_summaries}


def _result_binding(result):
    return {"resource_id": result.resource_id, "chunk_id": result.chunk_id,
            "collector_run_id": result.collector_run_id,
            "redacted_input_hash": result.input_hash}


def _empty_merge():
    facts = {"schema_version": FACTS_SCHEMA, "snapshot_id": "empty",
             "analysis_run_id": "empty", **{name: [] for name in FACT_ARRAYS},
             "unresolved_references": [], "provenance_catalog": []}
    return MergeResult(facts, {"valid": True, "errors": [], "unresolved": [],
                               "conflicts": [], "bindings": [], "llm_summaries": []})


def analysis_run_payloads(result: MergeResult) -> dict[str, str]:
    """Serialize only public formal Facts and the internal validation report."""
    facts_bytes = canonicalize_json(result.facts)
    return {"fact_graph_payload": facts_bytes.decode(),
            "facts_hash": sha256_uri(facts_bytes),
            "validation_report": orjson.dumps(
                result.validation_report, option=orjson.OPT_SORT_KEYS
            ).decode()}


async def prepare_analysis_run_payloads(
    tenant_id: str, result: MergeResult, *, store: Any
) -> PreparedAnalysisPayloads:
    """Prepare inline or READY object payloads using the shared payload lifecycle."""
    facts = await _prepare_document(tenant_id, result.facts, store)
    report = await _prepare_document(tenant_id, result.validation_report, store)
    return PreparedAnalysisPayloads(facts, report)


async def _prepare_document(tenant_id, document, store):
    from services.network_topology.payload_store import PreparedEnvelope, PreparedPayload

    canonical = canonicalize_json(document)
    envelope = PreparedEnvelope(document, canonical, sha256_uri(canonical))
    if len(canonical) <= store.inline_threshold:
        return PreparedPayload(tenant_id, envelope.payload_hash, len(canonical),
                               inline_payload=canonical.decode())
    return await store._prepare_object(tenant_id, envelope, datetime.now(timezone.utc))


async def activate_analysis_run_payloads(
    run: Any, prepared: PreparedAnalysisPayloads, *, store: Any, connection: Any
) -> None:
    """Atomically link LIVE payloads; candidates never cross this boundary."""
    facts_row = await store.activate(
        run.tenant_id, prepared.facts, connection=connection
    )
    report_row = await store.activate(
        run.tenant_id, prepared.report, connection=connection
    )
    _bind_run_payloads(run, prepared, facts_row, report_row)
    await run.save(using_db=connection, update_fields=[
        "fact_graph_payload", "fact_graph_payload_object_row",
        "validation_report", "validation_report_object_row", "facts_hash", "utime",
    ])


def _bind_run_payloads(run, prepared, facts_row, report_row):
    run.fact_graph_payload = prepared.facts.inline_payload
    run.fact_graph_payload_object_row = facts_row
    run.validation_report = prepared.report.inline_payload
    run.validation_report_object_row = report_row
    run.facts_hash = prepared.facts.payload_hash
