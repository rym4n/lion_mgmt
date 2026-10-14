from __future__ import annotations

from typing import Any

import pytest

from services.network_topology import contracts

API_ERROR_URI = "aioe.network-topology.api/error-response/v1"
COMMON_URI = "aioe.network-topology.api/common/v1"
STATUS_URI = "aioe.network-topology.api/status-response/v1"
API_ERROR_RETRYABILITY = {
    "SCHEMA_INVALID": False,
    "MANIFEST_MISMATCH": False,
    "MANIFEST_CONSUMED": False,
    "UPLOAD_CONFLICT": False,
    "SERVICE_BINDING_NOT_FOUND": False,
    "SERVICE_BINDING_AMBIGUOUS": False,
    "LEGACY_CONVERSION_REQUIRED": False,
    "WORKER_UNAVAILABLE": True,
}
TEMPORARY_CODES = (
    "PROVIDER_TIMEOUT",
    "TEMPORARY_NETWORK_FAILURE",
    "TEMPORARY_DATABASE_FAILURE",
    "WORKER_LEASE_LOST",
)
FAILED_CODES = (
    "AGENT_NOT_CONFIGURED",
    "RUN_SUPERSEDED",
    "SCHEMA_INVALID",
    "PARSER_FAILED",
    "EVIDENCE_INVALID",
    "GRAPH_VALIDATION_FAILED",
    *TEMPORARY_CODES,
)
SUPERSEDED_CODES = (
    "NEWER_COLLECTION_PUBLISHED",
    "PUBLICATION_CAS_MISMATCH",
    "STALE_PROFILE",
    "UPGRADE_INTENT_INVALID",
)
API_ONLY_CODES = (
    "MANIFEST_MISMATCH",
    "MANIFEST_CONSUMED",
    "UPLOAD_CONFLICT",
    "SERVICE_BINDING_NOT_FOUND",
    "SERVICE_BINDING_AMBIGUOUS",
    "LEGACY_CONVERSION_REQUIRED",
)
VALID_STATUS_CASES = (
    ("RECEIVED", None),
    ("RECEIVED", "WORKER_UNAVAILABLE"),
    ("RUNNING", None),
    *(("RETRY_WAIT", code) for code in TEMPORARY_CODES),
    ("WAITING_FOR_PUBLISH", None),
    ("SUCCEEDED", None),
    *(("FAILED", code) for code in FAILED_CODES),
    *(("SUPERSEDED", code) for code in SUPERSEDED_CODES),
    ("CANCELLED", "CANCELLED_BY_REQUEST"),
)


def assert_rejected(payload: Any, schema_uri: str) -> None:
    with pytest.raises(contracts.ContractError):
        contracts.validate_contract(payload, schema_uri)


def api_error(error_code: str, retryable: bool) -> dict[str, Any]:
    return {
        "code": 4000,
        "message": "Network topology request rejected",
        "data": {"error_code": error_code, "retryable": retryable},
    }


def status_payload(state: str, error_code: str | None = None) -> dict[str, Any]:
    terminal = state in {"SUCCEEDED", "FAILED", "SUPERSEDED", "CANCELLED"}
    data = {
        "sync_id": "analysis-1",
        "state": state,
        "terminal": terminal,
        "stage": None,
        "blocked_by": None,
        "attempt": 1,
        "retryable": state in {"RECEIVED", "RUNNING", "RETRY_WAIT"},
        "next_retry_at": None,
        "published": state == "SUCCEEDED",
        "graph_revision": 0,
        "publication_revision": 0,
        "error_code": error_code,
        "error_summary": "Frozen error" if error_code else None,
    }
    if state == "RECEIVED" and error_code == "WORKER_UNAVAILABLE":
        data["retryable"] = True
    if state == "RUNNING":
        data["stage"] = "PARSING"
    if state == "RETRY_WAIT":
        data["stage"] = "PARSING"
        data["next_retry_at"] = "2026-07-28T00:01:00Z"
    if state == "WAITING_FOR_PUBLISH":
        data["stage"] = "FACTS_READY"
        data["blocked_by"] = "PUBLISH_GATE"
    if state == "SUCCEEDED":
        data["stage"] = "PUBLISHING"
    return {"code": 0, "data": data}


@pytest.mark.parametrize(("error_code", "retryable"), API_ERROR_RETRYABILITY.items())
def test_api_error_code_and_retryability_are_closed(
    error_code: str, retryable: bool
) -> None:
    contracts.validate_contract(api_error(error_code, retryable), API_ERROR_URI)
    assert_rejected(api_error(error_code, not retryable), API_ERROR_URI)


def test_api_error_rejects_invented_uppercase_code() -> None:
    assert_rejected(api_error("INVENTED_PLATFORM_FAILURE", False), API_ERROR_URI)


def test_common_error_code_definitions_match_frozen_sets() -> None:
    definitions = contracts.load_schema(COMMON_URI)["$defs"]
    expected = {
        "apiErrorCode": set(API_ERROR_RETRYABILITY),
        "temporaryErrorCode": set(TEMPORARY_CODES),
        "failedErrorCode": set(FAILED_CODES),
        "supersededErrorCode": set(SUPERSEDED_CODES),
        "errorCode": {
            None,
            "WORKER_UNAVAILABLE",
            "CANCELLED_BY_REQUEST",
            *FAILED_CODES,
            *SUPERSEDED_CODES,
        },
    }
    for name, frozen_codes in expected.items():
        assert set(definitions[name]["enum"]) == frozen_codes


@pytest.mark.parametrize(("state", "error_code"), VALID_STATUS_CASES)
def test_status_matrix_accepts_only_frozen_cases(
    state: str, error_code: str | None
) -> None:
    contracts.validate_contract(status_payload(state, error_code), STATUS_URI)


@pytest.mark.parametrize(
    ("state", "error_code"),
    (
        ("RECEIVED", "SCHEMA_INVALID"),
        ("RUNNING", "WORKER_UNAVAILABLE"),
        ("RETRY_WAIT", "PARSER_FAILED"),
        ("WAITING_FOR_PUBLISH", "WORKER_UNAVAILABLE"),
        ("SUCCEEDED", "SCHEMA_INVALID"),
        ("FAILED", "NEWER_COLLECTION_PUBLISHED"),
        ("SUPERSEDED", "EVIDENCE_INVALID"),
        ("CANCELLED", "SCHEMA_INVALID"),
    ),
)
def test_status_rejects_error_code_from_wrong_state(
    state: str, error_code: str
) -> None:
    assert_rejected(status_payload(state, error_code), STATUS_URI)


@pytest.mark.parametrize("error_code", API_ONLY_CODES)
def test_api_only_error_codes_never_enter_status(error_code: str) -> None:
    assert_rejected(status_payload("FAILED", error_code), STATUS_URI)


def test_status_rejects_invented_uppercase_code() -> None:
    assert_rejected(status_payload("FAILED", "INVENTED_PLATFORM_FAILURE"), STATUS_URI)


def test_worker_unavailable_cannot_claim_analysis_gate() -> None:
    no_error = status_payload("RECEIVED")
    no_error["data"]["blocked_by"] = "ANALYSIS_GATE"
    contracts.validate_contract(no_error, STATUS_URI)

    worker_unavailable = status_payload("RECEIVED", "WORKER_UNAVAILABLE")
    worker_unavailable["data"]["blocked_by"] = "ANALYSIS_GATE"
    assert_rejected(worker_unavailable, STATUS_URI)


@pytest.mark.parametrize(("state", "error_code"), VALID_STATUS_CASES)
def test_status_rejects_retryable_flip(state: str, error_code: str | None) -> None:
    payload = status_payload(state, error_code)
    payload["data"]["retryable"] = not payload["data"]["retryable"]
    assert_rejected(payload, STATUS_URI)


@pytest.mark.parametrize(
    ("state", "error_code"),
    tuple(case for case in VALID_STATUS_CASES if case[1] is not None),
)
def test_status_error_requires_nonempty_summary(state: str, error_code: str) -> None:
    payload = status_payload(state, error_code)
    payload["data"]["error_summary"] = ""
    assert_rejected(payload, STATUS_URI)


@pytest.mark.parametrize(
    ("state", "error_code"),
    (
        ("RECEIVED", None),
        ("RUNNING", None),
        ("RETRY_WAIT", "PROVIDER_TIMEOUT"),
        ("WAITING_FOR_PUBLISH", None),
        ("FAILED", "SCHEMA_INVALID"),
        ("SUPERSEDED", "STALE_PROFILE"),
        ("CANCELLED", "CANCELLED_BY_REQUEST"),
    ),
)
def test_non_succeeded_status_cannot_claim_published(
    state: str, error_code: str | None
) -> None:
    payload = status_payload(state, error_code)
    payload["data"]["published"] = True
    assert_rejected(payload, STATUS_URI)


def test_retry_wait_requires_next_retry_at() -> None:
    payload = status_payload("RETRY_WAIT", "PROVIDER_TIMEOUT")
    payload["data"]["next_retry_at"] = None
    assert_rejected(payload, STATUS_URI)


@pytest.mark.parametrize(("field", "value"), (("stage", None), ("blocked_by", None)))
def test_waiting_for_publish_keeps_frozen_gate(field: str, value: None) -> None:
    payload = status_payload("WAITING_FOR_PUBLISH")
    payload["data"][field] = value
    assert_rejected(payload, STATUS_URI)
