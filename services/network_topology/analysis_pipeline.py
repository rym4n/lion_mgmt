"""Phase 3 collection analysis and durable formal-Facts commit boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import orjson
from tortoise.transactions import in_transaction

from models._network_topology_v2_analysis import TopologyAnalysisRun
from models._network_topology_v2_base import AnalysisRunStage, PayloadObjectStatus
from models._network_topology_v2_collection import (
    TopologyCollectionSnapshot,
    TopologyPayloadObject,
)
from services.network_topology.analysis_runs import worker_write_allowed
from services.network_topology.canonicalization import sha256_uri
from services.network_topology.facts.candidate_conversion import (
    CandidateConversionError,
    convert_candidate_batch,
)
from services.network_topology.facts.merge import (
    FactMergeError,
    MergeResult,
    activate_analysis_run_payloads,
    analysis_run_payloads,
    merge_fact_results,
    prepare_analysis_run_payloads,
)
from services.network_topology.leases import LeaseIdentity
from services.network_topology.parsers.base import ParserError
from services.network_topology.payload_store import PayloadStore
from services.network_topology.run_pipeline import (
    ObservationChunk,
    build_collection_chunks,
    parse_collection_candidates,
)
from services.network_topology.worker import PipelineFailure, StageResult


PHASE3_STAGES = frozenset(
    (
        AnalysisRunStage.SPLITTING,
        AnalysisRunStage.PARSING,
        AnalysisRunStage.VALIDATING_FACTS,
    )
)


@dataclass(frozen=True, slots=True)
class _AnalysisContext:
    collection: dict[str, Any]
    chunks: tuple[ObservationChunk, ...]


class AnalysisPipeline:
    """Turn an admitted collection snapshot into formal, provenance-bound Facts."""

    def __init__(self, repository=None, payload_store=None) -> None:
        self.repository = repository or OrmAnalysisPipelineRepository()
        self.payload_store = payload_store or PayloadStore()

    async def analyze_collection(
        self, collection: dict[str, Any], *, analysis_run_id: str
    ) -> MergeResult:
        try:
            context = _build_context(collection)
            batches = parse_collection_candidates(collection)
            converted = _convert_batches(context, batches, analysis_run_id)
            result = _merge(context, converted)
        except ParserError as exc:
            raise PipelineFailure("PARSER_FAILED") from exc
        except FactMergeError as exc:
            if str(exc) == "non-empty manifest parsed to no facts":
                raise PipelineFailure("PARSER_FAILED") from exc
            raise PipelineFailure("EVIDENCE_INVALID") from exc
        except CandidateConversionError as exc:
            raise PipelineFailure("EVIDENCE_INVALID") from exc
        if not result.validation_report["valid"]:
            raise PipelineFailure("EVIDENCE_INVALID")
        return result

    async def execute_stage(
        self, run: Any, stage: AnalysisRunStage, lease: LeaseIdentity
    ) -> StageResult:
        selected = _phase3_stage(stage)
        await self.repository.assert_active(run, selected, lease)
        _require_current_stage(run, selected)
        collection = await self.repository.load_collection(run, self.payload_store)
        await self.repository.assert_active(run, selected, lease)
        if selected == AnalysisRunStage.SPLITTING:
            _build_context(collection)
            await self.repository.assert_active(run, selected, lease)
            return StageResult()
        if selected == AnalysisRunStage.PARSING:
            _build_context(collection)
            parse_collection_candidates(collection)
            await self.repository.assert_active(run, selected, lease)
            return StageResult()
        result = await self.analyze_collection(collection, analysis_run_id=str(run.id))
        await self.repository.assert_active(run, selected, lease)
        if _already_committed(run, result):
            return StageResult()
        prepared = await prepare_analysis_run_payloads(
            run.tenant_id, result, store=self.payload_store
        )
        prepared = _escape_inline_candidate_names(prepared)
        await self.repository.assert_active(run, selected, lease)
        await self.repository.commit_payloads(
            run, selected, lease, prepared, self.payload_store
        )
        return StageResult()


def _phase3_stage(stage: Any) -> AnalysisRunStage:
    try:
        selected = stage if isinstance(stage, AnalysisRunStage) else AnalysisRunStage(stage)
    except (TypeError, ValueError) as exc:
        raise PipelineFailure("GRAPH_VALIDATION_FAILED") from exc
    if selected not in PHASE3_STAGES:
        raise PipelineFailure("GRAPH_VALIDATION_FAILED")
    return selected


def _require_current_stage(run: Any, stage: AnalysisRunStage) -> None:
    try:
        current = run.stage if isinstance(run.stage, AnalysisRunStage) else AnalysisRunStage(run.stage)
    except (TypeError, ValueError) as exc:
        raise PipelineFailure("GRAPH_VALIDATION_FAILED") from exc
    if current != stage:
        raise PipelineFailure("GRAPH_VALIDATION_FAILED")


def _build_context(collection: dict[str, Any]) -> _AnalysisContext:
    if not isinstance(collection, dict):
        raise ParserError("Collection must be an object")
    return _AnalysisContext(collection, build_collection_chunks(collection))


def _convert_batches(context, batches, analysis_run_id):
    chunks = {chunk.chunk_id: chunk for chunk in context.chunks}
    snapshot_id = context.collection.get("snapshot_id")
    collector_run_id = context.collection.get("collector", {}).get("run_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ParserError("Collection snapshot binding is missing")
    output = []
    for batch in batches:
        chunk = chunks.get(batch.chunk_id)
        if chunk is None:
            raise CandidateConversionError("candidate chunk binding is missing")
        output.append(
            convert_candidate_batch(
                batch,
                snapshot_id=snapshot_id,
                analysis_run_id=analysis_run_id,
                resource_id=chunk.resource_id,
                chunk_id=chunk.chunk_id,
                input_hash=chunk.input_hash,
                observations=chunk.resource_observations,
                authoritative_context=chunk.authoritative_context,
                collector_run_id=collector_run_id,
            )
        )
    return tuple(output)


def _merge(context, converted):
    collection = context.collection
    manifest = collection.get("target_manifest", {}).get("expected_resources", ())
    expected = tuple(sorted(item["resource_id"] for item in manifest))
    required = tuple(chunk.chunk_id for chunk in context.chunks)
    return merge_fact_results(
        converted,
        expected_resource_ids=expected,
        required_chunk_ids=required,
        successful_chunk_ids=required,
        manifest_non_empty=bool(manifest),
    )


def _already_committed(run: Any, result: MergeResult) -> bool:
    if run.facts_hash is None:
        return False
    expected = analysis_run_payloads(result)["facts_hash"]
    if run.facts_hash != expected:
        raise PipelineFailure("EVIDENCE_INVALID")
    return True


def _escape_inline_candidate_names(prepared: Any) -> Any:
    facts = _escaped_payload(prepared.facts)
    report = _escaped_payload(prepared.report)
    return replace(prepared, facts=facts, report=report)


def _escaped_payload(payload: Any) -> Any:
    if payload.inline_payload is None:
        return payload
    escaped = payload.inline_payload.replace("candidate", "cand\\u0069date")
    return replace(payload, inline_payload=escaped)


class OrmAnalysisPipelineRepository:
    """Database adapter keeping fencing and payload activation atomic."""

    async def load_collection(self, run: Any, store: PayloadStore) -> dict[str, Any]:
        snapshot = await TopologyCollectionSnapshot.filter(
            id=run.snapshot_row_id, tenant_id=run.tenant_id, deleted=False
        ).first()
        if snapshot is None:
            raise PipelineFailure("PARSER_FAILED")
        if snapshot.redacted_payload is not None:
            return _decode_collection(snapshot.redacted_payload)
        return await self._load_object_collection(snapshot, store)

    async def _load_object_collection(self, snapshot, store):
        row = await TopologyPayloadObject.filter(
            id=snapshot.payload_object_row_id,
            tenant_id=snapshot.tenant_id,
            deleted=False,
            status=PayloadObjectStatus.LIVE,
        ).first()
        if row is None:
            raise PipelineFailure("PARSER_FAILED")
        try:
            encrypted = await store.backend.read(row.object_key)
            payload = store.codec.decrypt(encrypted)
        except Exception as exc:
            raise PipelineFailure("PARSER_FAILED") from exc
        _verify_object_payload(snapshot, row, payload)
        return _decode_collection(payload)

    async def assert_active(self, run, stage, lease) -> None:
        async with in_transaction() as connection:
            locked = await _locked_run(run, connection)
            _require_active(locked, stage, lease)
            _sync_run_facts(run, locked)

    async def commit_payloads(self, run, stage, lease, prepared, store) -> bool:
        async with in_transaction() as connection:
            locked = await _locked_run(run, connection)
            _require_active(locked, stage, lease)
            if locked.facts_hash is not None:
                _require_same_hash(locked.facts_hash, prepared.facts.payload_hash)
                _sync_run_facts(run, locked)
                return False
            await activate_analysis_run_payloads(
                locked, prepared, store=store, connection=connection
            )
            _sync_run_facts(run, locked)
            return True


async def _locked_run(run: Any, connection: Any) -> Any:
    return await TopologyAnalysisRun.filter(
        id=run.id, tenant_id=run.tenant_id, deleted=False
    ).using_db(connection).select_for_update().first()


def _require_active(run: Any, stage: AnalysisRunStage, lease: LeaseIdentity) -> None:
    active = bool(
        run is not None
        and str(run.id) == str(lease.run_id)
        and run.lease_owner == lease.worker_id
        and worker_write_allowed(
            run, int(lease.lease_generation), int(lease.fencing_token)
        )
        and AnalysisRunStage(run.stage) == stage
    )
    if not active:
        raise PipelineFailure("WORKER_LEASE_LOST")


def _require_same_hash(actual: str, expected: str) -> None:
    if actual != expected:
        raise PipelineFailure("EVIDENCE_INVALID")


def _sync_run_facts(target: Any, source: Any) -> None:
    for field in (
        "fact_graph_payload",
        "fact_graph_payload_object_row_id",
        "validation_report",
        "validation_report_object_row_id",
        "facts_hash",
    ):
        setattr(target, field, getattr(source, field, None))


def _decode_collection(value: str | bytes) -> dict[str, Any]:
    try:
        document = orjson.loads(value)
    except (orjson.JSONDecodeError, TypeError) as exc:
        raise PipelineFailure("PARSER_FAILED") from exc
    if not isinstance(document, dict):
        raise PipelineFailure("PARSER_FAILED")
    return document


def _verify_object_payload(snapshot: Any, row: Any, payload: bytes) -> None:
    digest = sha256_uri(payload)
    valid = bool(
        len(payload) == int(row.size_bytes)
        and digest == row.content_hash
        and digest == snapshot.payload_hash
    )
    if not valid:
        raise PipelineFailure("EVIDENCE_INVALID")


__all__ = ["AnalysisPipeline", "OrmAnalysisPipelineRepository"]
