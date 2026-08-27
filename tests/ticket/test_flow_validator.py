# coding=utf-8
"""流程发布前的字段与节点引用完整性校验。"""

from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.ticket.flow_validator import (
    validate_action_input_references,
    validate_aggregation_references,
    validate_field_control_references,
    validate_field_linkage_references,
    validate_flow_before_publish,
    validate_node_form_definitions,
    validate_flow_structure,
)
from tests.ticket.conftest import FakeQuery


def _flow(node_config=None):
    node = {"id": "approve-1", "type": "approve"}
    node.update(node_config or {})
    return {
        "form_schema": {
            "fields": [
                {"key": "amount"},
                {"key": "reason"},
                {"key": "summary"},
            ]
        },
        "nodes": [
            {"id": "start", "type": "start"},
            node,
            {"id": "auto-1", "type": "auto"},
            {"id": "end", "type": "end"},
        ],
    }


def test_field_control_accepts_existing_field():
    flow = _flow({"field_control": [{"field_id": "amount", "required": True}]})
    assert validate_field_control_references(flow) == []


def test_field_control_rejects_missing_field_with_node_and_field():
    flow = _flow({"field_control": {"deleted_field": {"visible": True}}})
    errors = validate_field_control_references(flow)
    assert len(errors) == 1
    assert "approve-1" in errors[0]
    assert "deleted_field" in errors[0]


def test_field_linkage_accepts_existing_source_and_target():
    flow = _flow(
        {"field_linkage": [{"source_field": "amount", "target_field": "reason"}]}
    )
    assert validate_field_linkage_references(flow) == []


def test_field_linkage_rejects_missing_source_and_target():
    flow = _flow(
        {
            "field_linkage": [
                {"source_field": "missing_source", "target_field": "missing_target"}
            ]
        }
    )
    errors = validate_field_linkage_references(flow)
    assert len(errors) == 2
    assert all("approve-1" in error for error in errors)
    assert any("missing_source" in error for error in errors)
    assert any("missing_target" in error for error in errors)


def test_action_inputs_accept_existing_field_templates():
    flow = _flow(
        {
            "actions": [
                {
                    "inputs": {
                        "payload": {"amount": "{{ amount }}"},
                        "description": "原因: {{reason}}",
                    }
                }
            ]
        }
    )
    assert validate_action_input_references(flow) == []


def test_action_inputs_reject_missing_field_template():
    flow = _flow({"actions": [{"inputs": {"value": "{{deleted_field}}"}}]})
    errors = validate_action_input_references(flow)
    assert len(errors) == 1
    assert "approve-1" in errors[0]
    assert "deleted_field" in errors[0]


def test_aggregation_accepts_existing_source_node_and_fields():
    flow = _flow(
        {
            "aggregation": {
                "source_node_id": "auto-1",
                "source_field": "amount",
                "target_field": "summary",
            }
        }
    )
    assert validate_aggregation_references(flow) == []


def test_aggregation_rejects_missing_source_node_and_field():
    flow = _flow(
        {
            "aggregation": {
                "source_node_id": "deleted-node",
                "source_field": "deleted_field",
            }
        }
    )
    errors = validate_aggregation_references(flow)
    assert len(errors) == 2
    assert all("approve-1" in error for error in errors)
    assert any("deleted-node" in error for error in errors)
    assert any("deleted_field" in error for error in errors)


def test_validate_flow_before_publish_aggregates_all_reference_errors():
    flow = _flow(
        {
            "field_control": {"missing_control": {}},
            "field_linkage": [
                {"source_field": "amount", "target_field": "missing_target"}
            ],
            "actions": [{"inputs": {"value": "{{missing_action}}"}}],
            "aggregation": {
                "source_node_id": "missing-node",
                "source_field": "missing_aggregation",
            },
        }
    )
    errors = validate_flow_before_publish(flow)
    assert len(errors) == 5
    for reference in (
        "missing_control",
        "missing_target",
        "missing_action",
        "missing-node",
        "missing_aggregation",
    ):
        assert any(reference in error for error in errors)


def test_flow_structure_requires_unique_start_and_end_nodes():
    flow = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "start-2", "type": "start"},
            {"id": "end-1", "type": "end"},
            {"id": "end-2", "type": "end"},
        ],
        "edges": [],
    }

    messages = [error["message"] for error in validate_flow_structure(flow)]

    assert "只能有一个开始节点" in messages
    assert "只能有一个结束节点" in messages


def test_flow_structure_rejects_edges_into_start_or_out_of_end():
    flow = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "approve", "type": "approve", "approvers": ["user-1"]},
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"sourceNodeId": "end", "targetNodeId": "approve"},
            {"sourceNodeId": "approve", "targetNodeId": "start"},
        ],
    }

    messages = [error["message"] for error in validate_flow_structure(flow)]

    assert "结束节点不能作为连线起点" in messages
    assert "开始节点不能作为连线终点" in messages


def test_flow_structure_accepts_approval_role_without_static_user():
    flow = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "approve", "type": "approve", "roles": ["role-1"]},
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "approve"},
            {"source": "approve", "target": "end"},
        ],
    }

    messages = [error["message"] for error in validate_flow_structure(flow)]

    assert "审批节点未配置审批人" not in messages


def test_node_form_fields_extend_the_flow_field_namespace():
    flow = _flow(
        {
            "properties": {
                "form_fields": [
                    {"key": "review_note", "label": "审批说明", "type": "text"}
                ]
            },
            "field_control": {"review_note": {"visible": True}},
        }
    )

    assert validate_field_control_references(flow) == []
    assert validate_node_form_definitions(flow) == []


def test_node_form_field_keys_must_be_unique_across_the_flow():
    flow = _flow(
        {
            "form_fields": [
                {"key": "amount", "label": "重复字段", "type": "number"},
                {"key": "", "label": "无标识", "type": "text"},
            ]
        }
    )

    errors = validate_node_form_definitions(flow)
    assert any("amount" in error and "重复" in error for error in errors)
    assert any("缺少名称或键" in error for error in errors)


@pytest.mark.asyncio
async def test_flow_publish_rejects_reference_errors_before_saving(monkeypatch):
    from models import ticket as ticket_models
    from services.ticket import views

    flow_json = {
        "nodes": [
            {"id": "start", "type": "start"},
            {
                "id": "approve-1",
                "type": "approve",
                "approvers": ["account-1"],
                "field_control": {"deleted_field": {"visible": True}},
            },
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "approve-1"},
            {"source": "approve-1", "target": "end"},
        ],
    }
    draft = SimpleNamespace(
        flow_json=flow_json,
        form_schema={"fields": [{"key": "amount"}]},
        saved=False,
    )

    async def _save():
        draft.saved = True

    draft.save = _save
    monkeypatch.setattr(
        ticket_models.TicketFlowVersion,
        "filter",
        lambda **kwargs: FakeQuery([draft]),
    )
    request = SimpleNamespace(
        json={"flow_id": "flow-1"},
        ctx=SimpleNamespace(
            session={"tenant_id": "tenant-1", "sub": "account-1"}
        ),
    )

    undecorated_publish = views.flow_publish_view.__wrapped__.__wrapped__
    with pytest.raises(CustomError) as exc:
        await undecorated_publish(request)

    assert exc.value.enum is ErrorCode.TICKET_FLOW_VALIDATE_FAILED
    assert draft.saved is False
