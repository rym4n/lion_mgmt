# coding=utf-8
"""工单单号生成。

格式 {type_code}-{YYYYMMDD}-{NNNN}，序号按「租户 + 类型 + 日期」三元组递增。
并发原子性靠 PostgreSQL 事务级 advisory lock：同一三元组的并发建单串行化，
事务结束自动释放，无需显式解锁。不用 DB sequence（无法按三元组分组且不能
归零），不用应用层计数（多 worker 会冲突）。
"""

from datetime import datetime

from tortoise import connections

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import TZ

_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1));"

_MAX_SQL = """
    SELECT MAX(SUBSTRING("ticket_no" FROM '[0-9]+$')::BIGINT) AS max_no
    FROM "ticket"
    WHERE "tenant_id" = $1 AND "ticket_no" LIKE $2 ESCAPE '\\'
"""


async def generate_ticket_no(
    tenant_id: str,
    type_code: str,
    connection=None,
    now: datetime | None = None,
) -> str:
    """在**调用方的建单事务内**分配单号。

    必须传入该事务的 connection —— pg_advisory_xact_lock 绑定事务，
    若在事务外取锁会立即释放，等于没锁。
    """
    if not tenant_id or not type_code:
        raise CustomError(ErrorCode.PARAM_ERROR)

    conn = connection or connections.get("default")
    day = (now or datetime.now(TZ)).strftime("%Y%m%d")
    prefix = f"{type_code}-{day}"
    escaped_prefix = (
        prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )

    # 1) 先取锁：锁键含三元组，不同租户/类型/日期互不阻塞
    await conn.execute_query(_LOCK_SQL, [f"{tenant_id}:{type_code}:{day}"])

    # 2) 锁内查当日最大序号。软删除的单也算占号，避免号被复用
    _, rows = await conn.execute_query(_MAX_SQL, [tenant_id, f"{escaped_prefix}-%"])
    max_no = (rows[0]["max_no"] if rows else None) or 0

    # 超 9999 时 :04d 自然变 5 位，不截断不报错
    return f"{prefix}-{int(max_no) + 1:04d}"
