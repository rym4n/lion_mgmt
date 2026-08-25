# coding=utf-8
"""空间作用域解析 —— 知识库隔离在 lion_mgmt 侧的落点（SPEC §3.1）。

隔离原理：调 lion_agent 检索时只传员工可见的 space_key 列表。
传不进去的空间，就搜不出来。请求里的 space_key 只能收窄，不能放大。
真正的过滤在 lion_agent 的 metadata_filter 上执行。

URI 构造相关的函数随 D3（砍掉文件系统语义）一并删除。
"""

import re

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.knowledge import KnowledgeSpace

SPACE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_space_key(space_key: str) -> None:
    """空间标识校验。

    它现在是 Qdrant payload 的过滤值而非 URI 片段，但校验同样不能松：
    过滤值里混入意外字符会让 metadata_filter 匹配不到任何记录，
    表现为「写进去了但搜不到」。
    """
    if not isinstance(space_key, str) or not SPACE_KEY_PATTERN.match(space_key):
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_KEY_INVALID)


async def visible_spaces(tenant_id: str, space_keys: list[str]) -> list[KnowledgeSpace]:
    """员工可见的空间实体（已过滤软删）。"""
    if not space_keys:
        return []
    return await KnowledgeSpace.filter(
        tenant_id=tenant_id, space_key__in=list(space_keys), deleted=False
    ).all()


async def resolve_search_scope(
    tenant_id: str, space_keys: list[str], requested: str | None
) -> list[str]:
    """确定本次检索的实际空间集合。

    requested 是「收窄」参数，不是「放大」参数：
      - 不传          → 全部可见空间
      - 传了且可见     → 只搜它
      - 传了但不可见   → 空集（不报错，避免泄露该空间是否存在）
    """
    spaces = await visible_spaces(tenant_id, space_keys)
    keys = [space.space_key for space in spaces]
    if not requested:
        return keys
    return [key for key in keys if key == requested]
