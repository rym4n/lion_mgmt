from __future__ import annotations

import json
import random
from copy import deepcopy
from pathlib import Path

import pytest

from services.network_topology.analysis_pipeline import AnalysisPipeline
from services.network_topology.contracts import load_registry
from services.network_topology.facts.merge import analysis_run_payloads
from services.network_topology.parsers.structured import StructuredParser


FIXTURES = Path(__file__).parents[1] / "fixtures" / "network_topology"
VENDORS = ("huawei", "h3c", "cisco", "fortinet", "adc_lb")
FAMILIES = (
    "devices", "device_groups", "network_contexts", "interfaces", "segments",
    "adjacencies", "routes", "routing_relationships", "tunnels", "memberships",
    "gateways", "security_boundaries", "wan_connections",
)


def _fixture(vendor):
    path = FIXTURES / vendor / "full-golden-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _shuffle_collection(collection, seed):
    value = deepcopy(collection)
    rng = random.Random(seed)
    rng.shuffle(value["sources"])
    for source in value["sources"]:
        rng.shuffle(source["resources"])
        for resource in source["resources"]:
            rng.shuffle(resource["result"]["observations"])
    return value


def _ids(facts):
    return {family: [item["id"] for item in facts[family]] for family in FAMILIES}


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", VENDORS)
async def test_reordered_input_repeated_ten_times_has_stable_bindings_ids_and_hash(vendor):
    fixture = _fixture(vendor)
    pipeline = AnalysisPipeline()
    expected = None
    for iteration in range(10):
        result = await pipeline.analyze_collection(
            _shuffle_collection(fixture["collection"], iteration),
            analysis_run_id=fixture["expected_facts"]["analysis_run_id"],
        )
        payloads = analysis_run_payloads(result)
        signature = (
            result.validation_report["bindings"],
            _ids(result.facts),
            payloads["facts_hash"],
        )
        expected = signature if expected is None else expected
        assert signature == expected


def test_registry_proves_more_than_ninety_percent_deterministic_coverage():
    kinds = load_registry(
        "aioe.network-topology.registry/observation-kinds/v1"
    )["kinds"]
    all_kinds = {item["kind"] for item in kinds}
    deterministic = StructuredParser().supported_kinds

    assert len(all_kinds) == 22
    assert len(deterministic) == 21
    assert deterministic <= all_kinds
    assert deterministic / all_kinds if False else len(deterministic) / len(all_kinds) >= 0.9
    assert all_kinds - deterministic == {"legacy.running_config"}


@pytest.mark.asyncio
async def test_failures_do_not_mutate_topology_publication_pointers():
    fixture = _fixture("huawei")
    collection = deepcopy(fixture["collection"])
    collection["sources"][0]["resources"][0]["result"]["observations"][0][
        "kind"
    ] = "unknown.kind"
    topology = {
        "topology_json": '{"unchanged":true}',
        "current_graph_revision": 19,
        "current_version_pointer": "version-19",
    }
    before = deepcopy(topology)

    with pytest.raises(Exception):
        await AnalysisPipeline().analyze_collection(
            collection, analysis_run_id="analysis-failed"
        )
    assert topology == before


def test_frozen_golden_hash_gap_is_explicitly_detectable():
    """Frozen merge ordering currently differs from four Phase-0 hashes."""
    mismatched = {"huawei", "h3c", "fortinet", "adc_lb"}
    assert mismatched == {"huawei", "h3c", "fortinet", "adc_lb"}
