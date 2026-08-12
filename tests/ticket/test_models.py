# coding=utf-8
"""模型契约测试。tortoise 的 _meta 在 import 期即建好，无需 DB 连接。"""

import pytest
from tortoise import fields

from models.ticket import (
    NODE_STATUS_ABNORMAL,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    TICKET_STATUS_SUSPENDED,
    TICKET_TERMINAL_STATUSES,
    Ticket,
    TicketActivity,
    TicketAttachment,
    TicketFlow,
    TicketFlowPermission,
    TicketFlowVersion,
    TicketLink,
    TicketNodeInstance,
    TicketType,
)

ALL_MODELS = [
    TicketType, TicketFlow, TicketFlowVersion, TicketFlowPermission,
    Ticket, TicketNodeInstance, TicketActivity, TicketAttachment, TicketLink,
]


def test_nine_tables_present_and_named():
    tables = {m._meta.db_table for m in ALL_MODELS}
    assert tables == {
        "ticket_type", "ticket_flow", "ticket_flow_version",
        "ticket_flow_permission", "ticket", "ticket_node_instance",
        "ticket_activity", "ticket_attachment", "ticket_link",
    }
    assert len(ALL_MODELS) == 9


@pytest.mark.parametrize("model", ALL_MODELS)
def test_every_model_inherits_base_fields(model):
    """BaseModel 的五个公共字段必须都在，租户隔离与软删除依赖它们。"""
    for field in ("id", "ctime", "utime", "deleted", "tenant_id"):
        assert field in model._meta.fields_map, f"{model.__name__} 缺 {field}"


def test_ticket_has_no_current_node_field():
    """SPEC §3.1：并行分支下当前节点是集合，不能有单值 current_node。"""
    assert "current_node" not in Ticket._meta.fields_map


def test_ticket_origin_and_callback_fields():
    """剧本恢复契约依赖 origin_* 四field；callback_token 供外部回调。"""
    for field in ("origin_run_id", "origin_node_id", "origin_flow_id",
                  "origin_params", "callback_token"):
        assert Ticket._meta.fields_map[field].null is True


def test_ticket_sla_and_suspend_fields_nullable():
    """sla_config 缺当前优先级键时两个字段留 null（SPEC §4.7）。"""
    for field in ("sla_respond_at", "sla_resolve_at", "closed_at", "suspended_at"):
        assert Ticket._meta.fields_map[field].null is True


def test_ticket_sweep_lease_fields_for_claim():
    """SPEC §4.7：claim/lease 互斥要求行上有 owner + 到期时间。"""
    for field in ("sweep_owner", "sweep_expires_at"):
        assert field in Ticket._meta.fields_map


def test_node_instance_m2m_through_table():
    field = TicketNodeInstance._meta.fields_map["approvers"]
    assert field.through == "ticket_node_approvers"
    assert field.model_name == "models.Account"


@pytest.mark.parametrize(("model", "field_name"), (
    (TicketFlow, "creator"),
    (TicketFlowVersion, "creator"),
    (TicketFlowVersion, "updator"),
    (TicketFlowPermission, "users"),
    (TicketFlowPermission, "roles"),
    (Ticket, "creator"),
    (TicketActivity, "actor"),
    (TicketAttachment, "uploader"),
    (TicketLink, "creator"),
))
def test_nullable_account_and_role_foreign_keys_set_null(model, field_name):
    field = model._meta.fields_map[field_name]
    assert field.null is True
    assert field.on_delete == fields.SET_NULL


def test_node_instance_fork_fields_for_merge_counting():
    """SPEC §4.2：汇聚按 fork 分支计数，节点须记住自己属于哪一路。"""
    for field in ("fork_id", "branch_key"):
        assert field in TicketNodeInstance._meta.fields_map


def test_terminal_status_set_matches_spec():
    """SPEC §4.8 转移表：1-5 是终态，0 处理中与 6 挂起不是。"""
    assert TICKET_TERMINAL_STATUSES == frozenset({1, 2, 3, 4, 5})
    assert TICKET_STATUS_SUSPENDED == 6
    assert (NODE_STATUS_PENDING, NODE_STATUS_RUNNING, NODE_STATUS_ABNORMAL) == (0, 4, 5)


def test_ticket_no_indexed_for_lookup():
    assert Ticket._meta.fields_map["ticket_no"].index is True
