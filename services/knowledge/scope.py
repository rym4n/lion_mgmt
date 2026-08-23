# coding=utf-8
"""空间作用域解析与路径校验 —— 知识库隔离的唯一实现点（SPEC §5.1）。

隔离原理：调 OpenViking 检索时只传员工可见空间的路径。
传不进去的范围，就搜不出来。请求里的 space_key 只能收窄，不能放大。

所有 URI 拼接都必须经本模块，不要在别处手拼字符串。
"""

import re

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.knowledge import SPACES_URI_PREFIX, KnowledgeSpace

SPACE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# 路径里出现这些片段一律拒绝：穿越、协议歧义、Windows 分隔符
_FORBIDDEN_PATH_TOKENS = ("..", "?", "#", "\\", "\x00")


def validate_space_key(space_key: str) -> None:
    """空间标识必须能安全地拼进 URI。

    尤其要挡住斜杠——它能在 URI 里造出额外一层目录，
    让 uri_in_space 的边界判断失效。
    """
    if not isinstance(space_key, str) or not SPACE_KEY_PATTERN.match(space_key):
        raise CustomError(ErrorCode.KNOWLEDGE_PATH_INVALID)


def validate_rel_path(rel_path: str) -> str:
    """校验并归一化空间内相对路径，返回归一化结果。"""
    if not isinstance(rel_path, str):
        raise CustomError(ErrorCode.KNOWLEDGE_PATH_INVALID)

    path = rel_path.strip()
    if not path:
        raise CustomError(ErrorCode.KNOWLEDGE_PATH_INVALID)

    for token in _FORBIDDEN_PATH_TOKENS:
        if token in path:
            raise CustomError(ErrorCode.KNOWLEDGE_PATH_INVALID)

    if path.startswith("/"):
        raise CustomError(ErrorCode.KNOWLEDGE_PATH_INVALID)

    # 折叠重复斜杠；单独的 "." 段也不允许
    segments = [seg for seg in path.split("/") if seg != ""]
    if not segments or any(seg == "." for seg in segments):
        raise CustomError(ErrorCode.KNOWLEDGE_PATH_INVALID)

    return "/".join(segments)


def build_uri(space_key: str, rel_path: str) -> str:
    """拼出完整 URI。两个入参都会先过校验。"""
    validate_space_key(space_key)
    normalized = validate_rel_path(rel_path)
    return f"{SPACES_URI_PREFIX}/{space_key}/{normalized}"


def uri_in_space(uri: str, root_uri: str) -> bool:
    """URI 是否落在空间根之下。

    必须比到分隔符：单纯的 startswith(root_uri) 会让空间 ops
    匹配到 ops-secret，那是跨空间越权。
    """
    if not isinstance(uri, str) or not isinstance(root_uri, str):
        return False
    return uri == root_uri or uri.startswith(root_uri + "/")


async def visible_spaces(tenant_id: str, space_keys: list[str]) -> list[KnowledgeSpace]:
    """员工可见的空间实体（已过滤软删）。"""
    if not space_keys:
        return []
    return await KnowledgeSpace.filter(
        tenant_id=tenant_id, space_key__in=list(space_keys), deleted=False
    ).all()


async def visible_space_roots(tenant_id: str, space_keys: list[str]) -> list[str]:
    """员工可见空间的 OpenViking 路径列表。"""
    spaces = await visible_spaces(tenant_id, space_keys)
    return [space.root_uri for space in spaces]


async def resolve_search_roots(
    tenant_id: str, space_keys: list[str], requested: str | None
) -> list[str]:
    """确定本次检索的实际范围。

    requested 是「收窄」参数，不是「放大」参数：
      - 不传          → 全部可见空间
      - 传了且可见     → 只搜它
      - 传了但不可见   → 空集（不报错，避免泄露该空间是否存在）
    """
    roots = await visible_space_roots(tenant_id, space_keys)
    if not requested:
        return roots
    target = f"{SPACES_URI_PREFIX}/{requested}"
    return [root for root in roots if root == target]
