# coding=utf-8
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


COMMAND_RUNTIME = "lion_agent"
COMMAND_APPROVAL_SOURCE = "command_approval"
APPROVEMENT_PRESENTATION_ADAPTER = "approvement_inbox_adapter"

CONTINUATION_MODE = "lion_mgmt_held_continuation"
REJECTED_CONTINUATION_MODES = {
    "ephemeral_stream_resume": "approval wait time must survive closed HTTP streams, process restarts, and lost coroutines",
    "mutate_approvement_as_source": "playbook approval callbacks must not become command-center runtime governance state",
}

TASK_STATUS_PENDING_APPROVAL = "pending_approval"
TASK_STATUS_DISPATCHED = "dispatched"
TASK_STATUS_CANCELLED = "cancelled"
TASK_STATUS_HANDED_OFF = "handed_off"

RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
RUN_STATUS_CONTINUATION_READY = "continuation_ready"
RUN_STATUS_REJECTED = "rejected"
RUN_STATUS_HANDED_OFF = "handed_off"
RUN_STATUS_STALE = "stale"

APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_APPROVED = "approved"
APPROVAL_STATUS_REJECTED = "rejected"
APPROVAL_STATUS_NEEDS_INFO = "needs_info"
APPROVAL_STATUS_APPROVED_READ_ONLY = "approved_read_only"
APPROVAL_STATUS_HANDED_OFF = "handed_off"

EVENT_APPROVAL_REQUIRED = "approval_required"
EVENT_APPROVAL_DECIDED = "approval_decided"
EVENT_POLICY_DENIED = "policy_denied"
EVENT_CONTINUATION_STARTED = "continuation_started"
EVENT_CONTINUATION_FAILED = "continuation_failed"
EVENT_TAKEOVER = "takeover"
EVENT_APPROVAL_STALE = "approval_stale"

DURABLE_CONTINUATION_FIELDS = (
    "tenant_id",
    "task_id",
    "run_id",
    "approval_id",
    "employee_id",
    "session_id",
    "policy_snapshot",
    "requested_action",
    "evidence_refs",
)

OPTIONAL_EMPTY_DURABLE_FIELDS = {"evidence_refs"}

EPHEMERAL_CONTEXT_FIELDS = (
    "active_stream",
    "active_stream_id",
    "coroutine",
    "http_stream",
    "iterator",
    "task_handle",
)

DECISION_TRANSITIONS = {
    "approve": {
        "approval_status": APPROVAL_STATUS_APPROVED,
        "task_status": TASK_STATUS_DISPATCHED,
        "run_status": RUN_STATUS_CONTINUATION_READY,
        "event_type": EVENT_APPROVAL_DECIDED,
        "next_event_type": EVENT_CONTINUATION_STARTED,
        "release_lease": True,
        "policy_mutation": None,
    },
    "reject": {
        "approval_status": APPROVAL_STATUS_REJECTED,
        "task_status": TASK_STATUS_CANCELLED,
        "run_status": RUN_STATUS_REJECTED,
        "event_type": EVENT_APPROVAL_DECIDED,
        "next_event_type": None,
        "release_lease": True,
        "policy_mutation": None,
    },
    "needs_info": {
        "approval_status": APPROVAL_STATUS_PENDING,
        "task_status": TASK_STATUS_PENDING_APPROVAL,
        "run_status": RUN_STATUS_WAITING_APPROVAL,
        "event_type": EVENT_APPROVAL_DECIDED,
        "next_event_type": None,
        "release_lease": False,
        "needs_info": True,
        "policy_mutation": None,
    },
    "read_only": {
        "approval_status": APPROVAL_STATUS_APPROVED_READ_ONLY,
        "task_status": TASK_STATUS_DISPATCHED,
        "run_status": RUN_STATUS_CONTINUATION_READY,
        "event_type": EVENT_APPROVAL_DECIDED,
        "next_event_type": EVENT_CONTINUATION_STARTED,
        "release_lease": True,
        "policy_mutation": "read_only",
    },
    "takeover": {
        "approval_status": APPROVAL_STATUS_HANDED_OFF,
        "task_status": TASK_STATUS_HANDED_OFF,
        "run_status": RUN_STATUS_HANDED_OFF,
        "event_type": EVENT_TAKEOVER,
        "next_event_type": None,
        "release_lease": True,
        "policy_mutation": None,
    },
}


def approval_runtime_decision() -> dict[str, Any]:
    return {
        "runtime": COMMAND_RUNTIME,
        "continuation_model": CONTINUATION_MODE,
        "approval_source_of_truth": COMMAND_APPROVAL_SOURCE,
        "approvement_integration": APPROVEMENT_PRESENTATION_ADAPTER,
        "hard_rules": [
            "pending approvals must be reconstructable from persisted task/run/approval records",
            "approval wait and resume must not require a live HTTP stream, coroutine, or in-memory AgentScope object",
            "Approvement can present command approvals in a shared inbox but does not own command-center approval state",
        ],
        "rejected": deepcopy(REJECTED_CONTINUATION_MODES),
    }


def durable_context_missing_fields(context: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in DURABLE_CONTINUATION_FIELDS:
        value = context.get(field)
        if field in OPTIONAL_EMPTY_DURABLE_FIELDS:
            if value is None:
                missing.append(field)
            continue
        if value in (None, "", [], {}):
            missing.append(field)
    return missing


def ephemeral_context_fields(context: Mapping[str, Any]) -> list[str]:
    return [field for field in EPHEMERAL_CONTEXT_FIELDS if context.get(field) is not None]


def assert_durable_continuation_context(context: Mapping[str, Any]) -> None:
    missing = durable_context_missing_fields(context)
    if missing:
        raise ValueError(f"approval continuation context is missing durable fields: {', '.join(missing)}")


def is_durable_continuation_context(context: Mapping[str, Any]) -> bool:
    return not durable_context_missing_fields(context)


def approval_required_context(
    *,
    tenant_id: str,
    task_id: str,
    run_id: str,
    approval_id: str,
    employee_id: str,
    session_id: str,
    policy_snapshot: Mapping[str, Any],
    requested_action: Mapping[str, Any],
    evidence_refs: list[str] | None = None,
    risk_level: str = "medium",
    trigger_type: str = "runtime_tool",
    resource_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "run_id": run_id,
        "approval_id": approval_id,
        "employee_id": employee_id,
        "session_id": session_id,
        "runtime": COMMAND_RUNTIME,
        "approval_source": COMMAND_APPROVAL_SOURCE,
        "continuation_model": CONTINUATION_MODE,
        "task_status": TASK_STATUS_PENDING_APPROVAL,
        "run_status": RUN_STATUS_WAITING_APPROVAL,
        "approval_status": APPROVAL_STATUS_PENDING,
        "trigger_type": trigger_type,
        "risk_level": risk_level,
        "policy_snapshot": deepcopy(dict(policy_snapshot)),
        "requested_action": deepcopy(dict(requested_action)),
        "resource_scope": deepcopy(dict(resource_scope or {})),
        "evidence_refs": list(evidence_refs or []),
    }
    assert_durable_continuation_context(context)
    return context


def approval_required_event_metadata(context: Mapping[str, Any]) -> dict[str, Any]:
    assert_durable_continuation_context(context)
    metadata = dict(context)
    for field in EPHEMERAL_CONTEXT_FIELDS:
        metadata.pop(field, None)
    return {
        "event_type": EVENT_APPROVAL_REQUIRED,
        "status": TASK_STATUS_PENDING_APPROVAL,
        "metadata": metadata,
    }


def approval_decision_transition(decision: str, context: Mapping[str, Any]) -> dict[str, Any]:
    assert_durable_continuation_context(context)
    if decision not in DECISION_TRANSITIONS:
        allowed = ", ".join(sorted(DECISION_TRANSITIONS))
        raise ValueError(f"unsupported approval decision '{decision}', expected one of: {allowed}")
    transition = deepcopy(DECISION_TRANSITIONS[decision])
    transition["decision"] = decision
    transition["approval_id"] = context["approval_id"]
    transition["task_id"] = context["task_id"]
    transition["run_id"] = context["run_id"]
    transition["session_id"] = context["session_id"]
    transition["approval_source"] = COMMAND_APPROVAL_SOURCE
    return transition


def stale_approval_recovery_event(context: Mapping[str, Any], *, reason: str = "heartbeat_expired") -> dict[str, Any]:
    assert_durable_continuation_context(context)
    return {
        "event_type": EVENT_APPROVAL_STALE,
        "task_status": TASK_STATUS_PENDING_APPROVAL,
        "run_status": RUN_STATUS_STALE,
        "approval_status": context.get("approval_status", APPROVAL_STATUS_PENDING),
        "reason": reason,
        "approval_id": context["approval_id"],
        "task_id": context["task_id"],
        "run_id": context["run_id"],
        "session_id": context["session_id"],
        "evidence_refs": list(context.get("evidence_refs") or []),
        "release_lease": True,
    }


def policy_denied_event_metadata(context: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    assert_durable_continuation_context(context)
    metadata = dict(context)
    metadata["deny_reason"] = reason
    return {
        "event_type": EVENT_POLICY_DENIED,
        "status": context.get("task_status", TASK_STATUS_PENDING_APPROVAL),
        "metadata": metadata,
    }
