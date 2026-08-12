# coding=utf-8
"""错误码契约。ErrorCode 是 @unique 强制的 Enum，重复码在 import 期即报错。"""

from core.error_code import ErrorCode

EXPECTED = {
    "TICKET_PERM_DENIED": 4200,
    "TICKET_FLOW_PERM_DENIED": 4201,
    "TICKET_NOT_APPROVER": 4202,
    "TICKET_ADMIN_ONLY": 4203,
    "TICKET_ALREADY_CLOSED": 4210,
    "TICKET_NODE_NOT_ACTIVE": 4211,
    "TICKET_SUSPENDED": 4212,
    "TICKET_WITHDRAW_FORBIDDEN": 4213,
    "TICKET_RETURN_TARGET_INVALID": 4214,
    "TICKET_NOT_FOUND": 4220,
    "TICKET_FLOW_NOT_FOUND": 4221,
    "TICKET_FLOW_NO_PUBLISHED": 4222,
    "TICKET_TYPE_NOT_FOUND": 4223,
    "TICKET_TYPE_NO_FLOW": 4224,
    "TICKET_FORM_INVALID": 4230,
    "TICKET_APPROVER_INVALID": 4231,
    "TICKET_FLOW_VALIDATE_FAILED": 4232,
    "TICKET_LINK_INVALID": 4233,
    "TICKET_QUORUM_REACHED": 4240,
    "TICKET_CALLBACK_TOKEN_INVALID": 4241,
}


def test_all_twenty_ticket_codes_defined_with_exact_values():
    for name, code in EXPECTED.items():
        assert hasattr(ErrorCode, name), f"缺错误码 {name}"
        assert getattr(ErrorCode, name).code == code


def test_ticket_codes_stay_inside_reserved_block():
    """4200-4241 是工单专属区段，越界会撞上后续模块。"""
    for code in EXPECTED.values():
        assert 4200 <= code <= 4241


def test_no_collision_with_pre_existing_codes():
    """既有码已到 4183，工单区段必须与之无交集。"""
    ticket_codes = set(EXPECTED.values())
    others = {m.code for m in ErrorCode if m.name not in EXPECTED}
    assert ticket_codes & others == set()


def test_every_ticket_code_has_nonempty_chinese_message():
    for name in EXPECTED:
        message = getattr(ErrorCode, name).message
        assert message and message.strip() == message
