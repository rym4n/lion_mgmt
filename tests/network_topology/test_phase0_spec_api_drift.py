from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from services.network_topology import contracts

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "网络拓扑模块优化SPEC.md"
STATUS_MARKER = "`POST /api/network-topology/config-sync/status` 必须携带"
UPLOAD_MARKER = "大型快照提供暂存式上传："
STATUS_URI = "aioe.network-topology.api/status-response/v1"
CHUNK_URI = "aioe.network-topology.api/upload-chunk-request/v1"
LLM_CHUNK_URI = "aioe.network-topology.llm-chunk/v1"


def json_example_after(marker: str) -> Any:
    document = SPEC_PATH.read_text(encoding="utf-8")
    section = document.split(marker, 1)[1]
    code_block = section.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(code_block)


def assert_rejected(payload: Any, schema_uri: str) -> None:
    with pytest.raises(contracts.ContractError):
        contracts.validate_contract(payload, schema_uri)


def schema_example(schema_uri: str, index: int = 0) -> dict[str, Any]:
    return contracts.load_schema(schema_uri)["examples"][index]


def test_status_example_matches_machine_contract_and_rejects_stage_as_state() -> None:
    payload = json_example_after(STATUS_MARKER)

    assert payload["code"] == 0
    assert payload["data"]["state"] == "RUNNING"
    assert payload["data"]["stage"] == "PARSING"
    assert payload["data"]["blocked_by"] is None
    contracts.validate_contract(payload, STATUS_URI)

    legacy = deepcopy(payload)
    legacy["data"]["state"] = "PARSING"
    assert_rejected(legacy, STATUS_URI)


def test_received_and_waiting_publish_gates_match_machine_state() -> None:
    received = schema_example(STATUS_URI)
    received["data"].update(
        state="RECEIVED", stage=None, blocked_by="ANALYSIS_GATE", attempt=0
    )
    contracts.validate_contract(received, STATUS_URI)

    wrong_gate = deepcopy(received)
    wrong_gate["data"]["blocked_by"] = "PUBLISH_GATE"
    assert_rejected(wrong_gate, STATUS_URI)

    waiting = schema_example(STATUS_URI, 1)
    assert waiting["data"]["stage"] == "FACTS_READY"
    assert waiting["data"]["blocked_by"] == "PUBLISH_GATE"
    contracts.validate_contract(waiting, STATUS_URI)

    for invalid_stage in (None, "VALIDATING_FACTS", "PROJECTING_GRAPH"):
        invalid = deepcopy(waiting)
        invalid["data"]["stage"] = invalid_stage
        assert_rejected(invalid, STATUS_URI)


def test_chunk_example_is_single_observation_and_rejects_batch_shape() -> None:
    chunk = json_example_after(UPLOAD_MARKER)["chunk"]

    assert set(chunk) == {"upload_session_id", "resource_id", "observation"}
    contracts.validate_contract(chunk, CHUNK_URI)

    legacy = deepcopy(chunk)
    legacy["observations"] = [legacy.pop("observation")]
    assert_rejected(legacy, CHUNK_URI)


def test_llm_chunk_keeps_one_to_one_hundred_observations() -> None:
    payload = schema_example(LLM_CHUNK_URI)
    contracts.validate_contract(payload, LLM_CHUNK_URI)

    maximum = deepcopy(payload)
    maximum["observations"] *= 100
    contracts.validate_contract(maximum, LLM_CHUNK_URI)

    for count in (0, 101):
        invalid = deepcopy(payload)
        invalid["observations"] *= count
        assert_rejected(invalid, LLM_CHUNK_URI)
