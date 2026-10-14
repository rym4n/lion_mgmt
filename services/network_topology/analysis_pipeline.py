"""Durable collection analysis, graph projection, and fenced publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import orjson
from tortoise.transactions import in_transaction

from models._network_topology_v2_analysis import TopologyAnalysisRun
from models._network_topology_v2_base import AnalysisRunStage, PayloadObjectStatus
from models._network_topology_v2_collection import (
    TopologyCollectionManifest,
    TopologyProfileRevision,
    TopologyCollectionSnapshot,
    TopologyPayloadObject,
)
from services.network_topology import contracts
from services.network_topology.graph_pipeline import build_graph
from services.network_topology.identity import OrmIdentityPersistence, resolve_with_persistence
from services.network_topology.legacy import publish_upgrade_graph
from services.network_topology.publishing import PublicationRequest, publish_graph
from services.network_topology.scope_tree import ScopeTreeError
from services.network_topology.versions import facts_hash, graph_hash
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
    split_collection,
)
from services.network_topology.worker import PipelineFailure, StageResult


GRAPH_STAGES = frozenset({
    AnalysisRunStage.RESOLVING_IDENTITIES, AnalysisRunStage.BUILDING_SCOPES,
    AnalysisRunStage.PROJECTING_GRAPH, AnalysisRunStage.VALIDATING_GRAPH,
    AnalysisRunStage.PUBLISHING,
})

PIPELINE_STAGES = GRAPH_STAGES | frozenset(
    (
        AnalysisRunStage.SPLITTING,
        AnalysisRunStage.PARSING,
        AnalysisRunStage.AGENT_NORMALIZING,
        AnalysisRunStage.VALIDATING_FACTS,
        AnalysisRunStage.FACTS_READY,
    )
)


@dataclass(frozen=True, slots=True)
class _AnalysisContext:
    collection: dict[str, Any]
    chunks: tuple[ObservationChunk, ...]


class NoAgentStage:
    """Fail closed unless no observations are pending for the agent."""

    async def run(self, run, collection, lease):
        if getattr(run, "agent_observation_ids", None):
            raise PipelineFailure("AGENT_NOT_CONFIGURED")
        return ()

    async def load_results(self, run):
        return ()


class AnalysisPipeline:
    """Turn an admitted collection snapshot into formal, provenance-bound Facts."""

    def __init__(self, repository=None, payload_store=None, agent_stage=None) -> None:
        self.repository = repository or OrmAnalysisPipelineRepository()
        self.payload_store = payload_store or PayloadStore()
        self.agent_stage = agent_stage or NoAgentStage()

    async def analyze_collection(
        self, collection: dict[str, Any], *, analysis_run_id: str,
        agent_results: tuple = (),
        exclude_observation_ids: frozenset[str] = frozenset(),
    ) -> MergeResult:
        try:
            context = _build_context(collection)
            batches = parse_collection_candidates(
                collection, exclude_observation_ids=exclude_observation_ids
            )
            converted = (*_convert_batches(context, batches, analysis_run_id), *agent_results)
            result = _merge(context, converted, exclude_observation_ids)
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
        selected = _known_stage(stage)
        await self.repository.assert_active(run, selected, lease)
        _require_current_stage(run, selected)
        if selected == AnalysisRunStage.FACTS_READY:
            return StageResult()
        collection = await self.repository.load_collection(run, self.payload_store)
        await self.repository.assert_active(run, selected, lease)
        if selected == AnalysisRunStage.SPLITTING:
            _build_context(collection)
            await self.repository.assert_active(run, selected, lease)
            return StageResult()
        if selected in GRAPH_STAGES:
            return await self._graph_stage(run, selected, lease, collection)
        split = split_collection(collection)
        if selected == AnalysisRunStage.PARSING:
            _build_context(collection)
            parse_collection_candidates(
                collection, exclude_observation_ids=frozenset(split.agent_observation_ids)
            )
            await self.repository.assert_active(run, selected, lease)
            return StageResult(updates={"agent_observation_ids": list(split.agent_observation_ids)})
        if selected == AnalysisRunStage.AGENT_NORMALIZING:
            await self.agent_stage.run(run, collection, lease)
            await self.repository.assert_active(run, selected, lease)
            return StageResult()
        if isinstance(self.agent_stage, NoAgentStage) and (
            split.agent_observation_ids or getattr(run, "agent_observation_ids", None)
        ):
            raise PipelineFailure("AGENT_NOT_CONFIGURED")
        result = await self.analyze_collection(
            collection, analysis_run_id=str(run.id),
            agent_results=await self.agent_stage.load_results(run),
            exclude_observation_ids=frozenset(split.agent_observation_ids),
        )
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

    async def _graph_stage(self, run, stage, lease, collection):
        facts = await self.repository.load_facts(run, self.payload_store)
        if not isinstance(facts, dict):
            raise PipelineFailure("EVIDENCE_INVALID")
        profile_row = await self.repository.load_profile(run)
        profile = _decode_document(profile_row.profile_json, "GRAPH_VALIDATION_FAILED")
        if stage in {
            AnalysisRunStage.RESOLVING_IDENTITIES, AnalysisRunStage.BUILDING_SCOPES,
            AnalysisRunStage.PROJECTING_GRAPH,
        }:
            resolution = await self.repository.resolve_identities(run, stage, lease, facts)
            if stage == AnalysisRunStage.RESOLVING_IDENTITIES:
                await self.repository.assert_active(run, stage, lease)
                return StageResult()
            try:
                graph = build_graph(facts, resolution, profile,
                                    profile_revision=int(profile_row.revision), collection=collection)
            except (ScopeTreeError, ValueError) as exc:
                raise PipelineFailure("GRAPH_VALIDATION_FAILED") from exc
            await self.repository.assert_active(run, stage, lease)
            if stage == AnalysisRunStage.BUILDING_SCOPES:
                return StageResult()
            return StageResult(updates={"graph_payload": orjson.dumps(graph).decode(),
                                        "graph_hash": graph_hash(graph)})
        graph = _decode_document(run.graph_payload, "GRAPH_VALIDATION_FAILED")
        if stage == AnalysisRunStage.VALIDATING_GRAPH:
            try:
                contracts.validate_contract(graph, "aioe.network-topology.graph/v2")
            except contracts.ContractError as exc:
                raise PipelineFailure("GRAPH_VALIDATION_FAILED") from exc
            await self.repository.assert_active(run, stage, lease)
            return StageResult()
        snapshot = await self.repository.load_snapshot(run)
        request = PublicationRequest(
            tenant_id=run.tenant_id, topology_id=str(run.topology_row_id),
            analysis_run_id=str(run.id), snapshot_id=str(run.snapshot_row_id),
            profile_revision_id=str(run.target_profile_revision_row_id),
            lease_owner=lease.worker_id, fencing_token=int(lease.fencing_token),
            lease_generation=int(lease.lease_generation),
            collection_sequence=int(snapshot.collection_sequence),
            expected_graph_revision=int(run.expected_current_graph_revision),
            expected_publication_revision=int(run.expected_publication_revision),
            facts=facts, graph=graph, profile=profile, actor=run.submitter_subject,
            profile_revision=int(profile_row.revision),
            upgrade_intent_id=await self.repository.upgrade_intent_id(run),
            require_non_empty=True,
        )
        await self.repository.assert_active(run, stage, lease)
        result = await _publish(request)
        if result.status == "SUPERSEDED":
            raise PipelineFailure("RUN_SUPERSEDED")
        return StageResult(published=True)


async def _publish(request):
    if request.upgrade_intent_id:
        return await publish_upgrade_graph(request)
    return await publish_graph(request)


def _decode_document(value, error_code):
    try:
        document = orjson.loads(value)
    except (orjson.JSONDecodeError, TypeError) as exc:
        raise PipelineFailure(error_code) from exc
    if not isinstance(document, dict):
        raise PipelineFailure(error_code)
    return document


def _known_stage(stage: Any) -> AnalysisRunStage:
    try:
        selected = stage if isinstance(stage, AnalysisRunStage) else AnalysisRunStage(stage)
    except (TypeError, ValueError) as exc:
        raise PipelineFailure("GRAPH_VALIDATION_FAILED") from exc
    if selected not in PIPELINE_STAGES:
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


def _merge(context, converted, exclude):
    collection = context.collection
    manifest = collection.get("target_manifest", {}).get("expected_resources", ())
    expected = tuple(sorted(item["resource_id"] for item in manifest))
    parser_chunks = tuple(
        chunk.chunk_id for chunk in context.chunks
        if any(item["observation_id"] not in exclude for item in chunk.observations)
    )
    agent_chunks = tuple(dict.fromkeys(
        result.chunk_id for result in converted if result.chunk_id not in parser_chunks
    ))
    required = (*parser_chunks, *agent_chunks)
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

    async def resolve_identities(self, run, stage, lease, facts):
        async with in_transaction() as connection:
            locked = await _locked_run(run, connection)
            _require_active(locked, stage, lease)
            persistence = OrmIdentityPersistence(
                locked.topology_row_id, locked.id, connection=connection,
                created_by=locked.submitter_subject,
            )
            return await resolve_with_persistence(
                locked.tenant_id, "collection", facts, persistence
            )

    async def load_profile(self, run):
        return await TopologyProfileRevision.get(
            id=run.target_profile_revision_row_id, tenant_id=run.tenant_id, deleted=False)

    async def load_snapshot(self, run):
        return await TopologyCollectionSnapshot.get(
            id=run.snapshot_row_id, tenant_id=run.tenant_id, deleted=False)

    async def upgrade_intent_id(self, run):
        manifest = await TopologyCollectionManifest.filter(
            tenant_id=run.tenant_id, committed_analysis_run_row_id=run.id, deleted=False
        ).first()
        return str(manifest.upgrade_intent_row_id) if manifest and manifest.upgrade_intent_row_id else None

    async def load_facts(self, run, store):
        if run.fact_graph_payload is not None:
            facts = _decode_document(run.fact_graph_payload, "EVIDENCE_INVALID")
            _require_same_hash(facts_hash(facts), run.facts_hash)
            return facts
        row = await TopologyPayloadObject.filter(
            id=run.fact_graph_payload_object_row_id, tenant_id=run.tenant_id,
            deleted=False, status=PayloadObjectStatus.LIVE,
        ).first()
        if row is None:
            raise PipelineFailure("EVIDENCE_INVALID")
        try:
            encrypted = await store.backend.read(row.object_key)
            payload = store.codec.decrypt(encrypted)
        except Exception as exc:
            raise PipelineFailure("EVIDENCE_INVALID") from exc
        if (len(payload) != int(row.size_bytes) or
                sha256_uri(payload) != row.content_hash or row.content_hash != run.facts_hash):
            raise PipelineFailure("EVIDENCE_INVALID")
        return _decode_document(payload, "EVIDENCE_INVALID")

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


def build_default_pipeline() -> AnalysisPipeline:
    """Build the fail-closed pipeline for listener and standalone worker."""
    from services.network_topology.agent_stage import build_agent_stage
    return AnalysisPipeline(agent_stage=build_agent_stage())
