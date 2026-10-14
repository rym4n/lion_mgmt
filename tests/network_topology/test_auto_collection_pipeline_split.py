from copy import deepcopy
from types import SimpleNamespace

import pytest

from models._network_topology_v2_base import AnalysisRunStage
from services.network_topology.run_pipeline import split_collection, parse_collection_candidates
from services.network_topology.worker import PipelineFailure
from tests.network_topology.test_phase3_pipeline import _fixture, _pipeline, _run, _lease


def _obs(oid, kind, status="success"):
    return {"observation_id": oid, "kind": kind, "observation_status": status}


def _collection(*resources):
    return {"sources": [{"resources": [
        {"resource_id": f"r{index}", "result": {"observations": list(observations)}}
        for index, observations in enumerate(resources)
    ]}]}


def test_resource_with_raw_goes_entirely_to_agent():
    result = split_collection(_collection((_obs("a", "raw.action_output"), _obs("b", "system.identity"))))
    assert result.agent_observation_ids == ("a", "b")
    assert result.parser_observation_ids == ()


def test_resource_without_raw_goes_to_parser():
    result = split_collection(_collection((_obs("b", "system.identity"), _obs("c", "interface.inventory"))))
    assert result.agent_observation_ids == ()
    assert result.parser_observation_ids == ("b", "c")


def test_failed_observations_ignored_and_failed_raw_does_not_route_resource():
    result = split_collection(_collection(
        (_obs("a", "raw.action_output"), _obs("c", "l2.vlans", "not_supported")),
        (_obs("d", "raw.action_output", "failed"), _obs("e", "system.identity")),
    ))
    assert result.agent_observation_ids == ("a",)
    assert result.parser_observation_ids == ("e",)


def _raw_fixture():
    fixture = deepcopy(_fixture())
    resource = fixture["collection"]["sources"][0]["resources"][0]
    resource["result"]["observations"].append(_obs("raw-1", "raw.action_output"))
    return fixture


def test_excluded_observations_never_reach_parser_registry():
    fixture = _raw_fixture()
    split = split_collection(fixture["collection"])

    class RejectRegistry:
        def resolve_input(self, parser_input):
            pytest.fail("excluded observation reached deterministic parser")

    # Authority parsing is independent; ensure this assertion concerns observation batches.
    fixture["collection"]["authoritative_context"] = {}
    assert parse_collection_candidates(
        fixture["collection"], RejectRegistry(),
        exclude_observation_ids=frozenset(split.agent_observation_ids),
    ) == ()


@pytest.mark.asyncio
async def test_agent_stage_without_pending_observations_is_noop():
    from services.network_topology.analysis_pipeline import NoAgentStage
    stage = NoAgentStage()
    assert await stage.run(SimpleNamespace(agent_observation_ids=[]), {}, None) == ()
    assert await stage.load_results(SimpleNamespace()) == ()


@pytest.mark.asyncio
async def test_agent_stage_with_pending_observations_fails_closed():
    from services.network_topology.analysis_pipeline import NoAgentStage
    with pytest.raises(PipelineFailure) as exc:
        await NoAgentStage().run(SimpleNamespace(agent_observation_ids=["a"]), {}, None)
    assert exc.value.error_code == "AGENT_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_parsing_persists_split_and_agent_stage_cannot_commit():
    fixture = _raw_fixture()
    pipeline, repository = _pipeline(fixture)
    run = _run(fixture)
    run.stage = AnalysisRunStage.PARSING
    result = await pipeline.execute_stage(run, run.stage, _lease(run))
    assert result.updates == {"agent_observation_ids": list(split_collection(fixture["collection"]).agent_observation_ids)}
    run.agent_observation_ids = result.updates["agent_observation_ids"]
    run.stage = AnalysisRunStage.AGENT_NORMALIZING
    with pytest.raises(PipelineFailure, match="AGENT_NOT_CONFIGURED"):
        await pipeline.execute_stage(run, run.stage, _lease(run))
    assert repository.commits == 0


@pytest.mark.asyncio
async def test_structured_pipeline_agent_stage_is_noop():
    fixture = _fixture()
    pipeline, repository = _pipeline(fixture)
    run = _run(fixture)
    run.agent_observation_ids = []
    run.stage = AnalysisRunStage.AGENT_NORMALIZING
    result = await pipeline.execute_stage(run, run.stage, _lease(run))
    assert not result.published
    assert repository.commits == 0


@pytest.mark.asyncio
async def test_validation_cannot_bypass_unconfigured_agent_stage():
    fixture = _raw_fixture()
    pipeline, repository = _pipeline(fixture)
    run = _run(fixture)
    run.agent_observation_ids = list(split_collection(fixture["collection"]).agent_observation_ids)
    run.stage = AnalysisRunStage.VALIDATING_FACTS
    with pytest.raises(PipelineFailure, match="AGENT_NOT_CONFIGURED"):
        await pipeline.execute_stage(run, run.stage, _lease(run))
    assert repository.commits == 0


def test_parser_exclusion_keeps_other_observations():
    fixture = _fixture()
    collection = fixture["collection"]
    observation_id = collection["sources"][0]["resources"][0]["result"]["observations"][0]["observation_id"]
    from services.network_topology.parsers.registry import ParserRegistry
    seen = []

    class RecordingRegistry:
        def resolve_input(self, parser_input):
            seen.extend(item["observation_id"] for item in parser_input.observations)
            return ParserRegistry.default().resolve_input(parser_input)

    batches = parse_collection_candidates(
        collection, RecordingRegistry(), exclude_observation_ids=frozenset([observation_id])
    )
    assert batches
    assert seen
    assert observation_id not in seen


def test_raw_output_registry_selection_is_explicitly_rejected():
    from services.network_topology.parsers.base import ParserError
    from services.network_topology.parsers.registry import ParserRegistry
    with pytest.raises(ParserError, match="requires the agent stage"):
        ParserRegistry.default().resolve(
            kind="raw.action_output", data_schema="irrelevant",
            vendor="huawei", platform="vrp", os_family="vrp", parser_profile=None,
        )


@pytest.mark.asyncio
async def test_configured_agent_results_are_merged_without_requiring_raw_chunk():
    from services.network_topology.analysis_pipeline import _build_context, _convert_batches
    fixture = _raw_fixture()
    original = _fixture()
    pipeline, repository = _pipeline(fixture)
    run = _run(fixture)
    run.agent_observation_ids = list(split_collection(fixture["collection"]).agent_observation_ids)
    converted = _convert_batches(
        _build_context(fixture["collection"]),
        parse_collection_candidates(original["collection"]), str(run.id),
    )
    calls = []

    class AgentStage:
        async def run(self, actual_run, collection, lease):
            assert actual_run is run
            calls.append("run")
            return converted

        async def load_results(self, actual_run):
            assert actual_run is run
            calls.append("load")
            return converted

    pipeline.agent_stage = AgentStage()
    for stage in (AnalysisRunStage.AGENT_NORMALIZING, AnalysisRunStage.VALIDATING_FACTS):
        run.stage = stage
        await pipeline.execute_stage(run, stage, _lease(run))
    assert calls == ["run", "load"]
    assert repository.commits == 1
    assert run.facts_hash
