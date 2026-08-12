# coding=utf-8
import importlib.util
from pathlib import Path


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = _load_module("approval_contract", "services/command_center/approval_contract.py")


def _context(**overrides):
    values = {
        "tenant_id": "tenant-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "approval_id": "approval-1",
        "employee_id": "employee-1",
        "session_id": "session-1",
        "policy_snapshot": {"approval": {"write_file": "approval_required"}},
        "requested_action": {"type": "tool_call", "tool": "write_file"},
        "evidence_refs": ["event:tool-call-1"],
        "resource_scope": {"assets": ["db01"]},
    }
    values.update(overrides)
    return contract.approval_required_context(**values)


def test_runtime_decision_keeps_lion_agent_and_command_approval_authoritative():
    decision = contract.approval_runtime_decision()

    assert decision["runtime"] == "lion_agent"
    assert decision["continuation_model"] == "lion_mgmt_held_continuation"
    assert decision["approval_source_of_truth"] == "command_approval"
    assert decision["approval_inbox"] == "command_center"
    assert "ephemeral_stream_resume" in decision["rejected"]
    assert "mutate_approvement_as_source" in decision["rejected"]


def test_approval_required_event_contains_only_durable_resume_context():
    context = _context()

    event = contract.approval_required_event_metadata(
        {
            **context,
            "active_stream_id": "stream-1",
            "coroutine": object(),
            "http_stream": object(),
        }
    )

    assert event["event_type"] == "approval_required"
    assert event["status"] == "pending_approval"
    metadata = event["metadata"]
    assert metadata["runtime"] == "lion_agent"
    assert metadata["continuation_model"] == "lion_mgmt_held_continuation"
    assert metadata["approval_source"] == "command_approval"
    assert metadata["policy_snapshot"]["approval"]["write_file"] == "approval_required"
    assert metadata["requested_action"]["tool"] == "write_file"
    assert metadata["session_id"] == "session-1"
    assert metadata["evidence_refs"] == ["event:tool-call-1"]
    assert "active_stream_id" not in metadata
    assert "coroutine" not in metadata
    assert "http_stream" not in metadata


def test_validator_rejects_ephemeral_only_resume_context():
    context = _context()
    incomplete = dict(context)
    incomplete["policy_snapshot"] = {}
    incomplete["requested_action"] = {}
    incomplete["session_id"] = ""
    incomplete["active_stream_id"] = "stream-1"

    assert contract.durable_context_missing_fields(incomplete) == [
        "session_id",
        "policy_snapshot",
        "requested_action",
    ]
    assert contract.ephemeral_context_fields(incomplete) == ["active_stream_id"]
    assert contract.is_durable_continuation_context(incomplete) is False


def test_empty_evidence_refs_are_valid_durable_context():
    context = _context(evidence_refs=[])

    assert context["evidence_refs"] == []
    assert contract.durable_context_missing_fields(context) == []
    assert contract.is_durable_continuation_context(context) is True
    assert contract.approval_decision_transition("approve", context)["run_id"] == "run-1"


def test_decision_transitions_cover_required_approval_actions():
    context = _context()

    approve = contract.approval_decision_transition("approve", context)
    reject = contract.approval_decision_transition("reject", context)
    needs_info = contract.approval_decision_transition("needs_info", context)
    read_only = contract.approval_decision_transition("read_only", context)
    takeover = contract.approval_decision_transition("takeover", context)

    assert approve["approval_status"] == "approved"
    assert approve["run_status"] == "continuation_ready"
    assert approve["next_event_type"] == "continuation_started"
    assert approve["release_lease"] is True
    assert reject["approval_status"] == "rejected"
    assert reject["task_status"] == "cancelled"
    assert reject["release_lease"] is True
    assert needs_info["approval_status"] == "pending"
    assert needs_info["task_status"] == "pending_approval"
    assert needs_info["needs_info"] is True
    assert read_only["policy_mutation"] == "read_only"
    assert read_only["run_status"] == "continuation_ready"
    assert read_only["release_lease"] is True
    assert takeover["approval_status"] == "handed_off"
    assert takeover["task_status"] == "handed_off"
    assert takeover["release_lease"] is True


def test_stale_recovery_preserves_approval_record_and_evidence_refs():
    context = _context(evidence_refs=["event:approval-required", "event:heartbeat"])

    event = contract.stale_approval_recovery_event(context)

    assert event["event_type"] == "approval_stale"
    assert event["approval_id"] == "approval-1"
    assert event["task_id"] == "task-1"
    assert event["run_id"] == "run-1"
    assert event["session_id"] == "session-1"
    assert event["run_status"] == "stale"
    assert event["task_status"] == "pending_approval"
    assert event["evidence_refs"] == ["event:approval-required", "event:heartbeat"]
    assert event["release_lease"] is True


def test_policy_denied_event_reuses_same_durable_context_shape():
    context = _context()

    event = contract.policy_denied_event_metadata(context, reason="resource_out_of_scope")

    assert event["event_type"] == "policy_denied"
    assert event["metadata"]["deny_reason"] == "resource_out_of_scope"
    assert event["metadata"]["approval_source"] == "command_approval"
    assert event["metadata"]["requested_action"]["tool"] == "write_file"
