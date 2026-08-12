# coding=utf-8
"""单号生成。并发唯一性靠 advisory lock + DB 唯一索引，在 UAT 验证；
单测锁定格式、归零、递增，以及「锁必须在查询之前取」这个顺序。"""

from datetime import datetime

import pytest

from services.ticket import ticket_no as mod


class _FakeConnection:
    """记录 execute_query 调用顺序，用于断言锁先于查询。"""

    def __init__(self, max_seq=None):
        self.calls = []
        self.max_seq = max_seq

    async def execute_query(self, sql, values=None):
        self.calls.append((sql, values))
        if "pg_advisory_xact_lock" in sql:
            return 0, []
        return 1, [{"max_no": self.max_seq}]


@pytest.mark.asyncio
async def test_first_ticket_of_day_starts_at_0001():
    conn = _FakeConnection(max_seq=None)
    result = await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    assert result == "CHG-20260810-0001"


@pytest.mark.asyncio
async def test_sequence_increments_from_existing_max():
    conn = _FakeConnection(max_seq=7)
    result = await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    assert result == "CHG-20260810-0008"


@pytest.mark.asyncio
async def test_sequence_resets_across_days():
    """序号按租户+类型+日期三元组递增；查询已按当天前缀过滤，跨天自然归零。"""
    conn = _FakeConnection(max_seq=None)
    result = await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 11, 0, 1, 0)
    )
    assert result == "CHG-20260811-0001"


@pytest.mark.asyncio
async def test_sequence_grows_to_five_digits_without_error():
    """SPEC §3.1：超 9999 位数自然增长，不报错。"""
    conn = _FakeConnection(max_seq=9999)
    result = await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    assert result == "CHG-20260810-10000"


@pytest.mark.asyncio
async def test_advisory_lock_acquired_before_max_query():
    """顺序错了锁就白取：查询若在锁外，两个事务会读到同一个 max。"""
    conn = _FakeConnection(max_seq=3)
    await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    assert len(conn.calls) == 2
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert "MAX" in conn.calls[1][0].upper()


@pytest.mark.asyncio
async def test_lock_key_includes_tenant_type_and_date():
    """三元组任一维度漏了，就会跨租户/跨类型/跨天串号。"""
    conn = _FakeConnection(max_seq=None)
    await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    lock_sql, lock_values = conn.calls[0]
    assert "hashtext" in lock_sql
    assert lock_values == ["tenant-1:CHG:20260810"]


@pytest.mark.asyncio
async def test_max_query_scopes_to_tenant_and_prefix():
    conn = _FakeConnection(max_seq=None)
    await mod.generate_ticket_no(
        "tenant-1", "CHG", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    query_sql, query_values = conn.calls[1]
    assert '"tenant_id" = $1' in query_sql
    assert query_values == ["tenant-1", "CHG-20260810-%"]


@pytest.mark.asyncio
async def test_type_code_underscore_is_literal_in_max_query_prefix():
    conn = _FakeConnection(max_seq=None)
    await mod.generate_ticket_no(
        "tenant-1", "C_G", connection=conn, now=datetime(2026, 8, 10, 9, 0, 0)
    )
    query_sql, query_values = conn.calls[1]
    assert "ESCAPE '\\'" in query_sql
    assert query_values == ["tenant-1", "C\\_G-20260810-%"]


@pytest.mark.asyncio
async def test_rejects_empty_tenant_or_code():
    """空租户让锁键退化成近似全局锁，空 code 生成不可读单号。"""
    for tenant, code in (("", "CHG"), ("tenant-1", ""), (None, "CHG")):
        with pytest.raises(Exception):
            await mod.generate_ticket_no(tenant, code, connection=_FakeConnection())
