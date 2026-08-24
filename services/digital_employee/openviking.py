# coding=utf-8
"""OpenViking embedding 配置：解析、校验与应用编排。

配置格式与 middleware/lion_viking 保持一致：**JSON**。
lion_viking 的 entrypoint.sh 用 ``python3 -m json.tool`` 校验配置合法性，
并用 ``"dimension": <int>`` 提取维度、``sha256sum`` 比对内容哈希，
维度变化时清空 ``/app/data/accounts`` 下的向量库。
本模块的解析/哈希逻辑必须与该 entrypoint 对齐，否则会写出无法启动的配置。
"""

import hashlib
import json
import re
from typing import Any, Optional, Tuple

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError

# 与常见 embedding 模型对齐的合法维度上限
MIN_DIMENSION = 1
MAX_DIMENSION = 4096

# 兜底解析：形如 `embedding.dimension = 1536` 或 `dimension = 1536` 的扁平写法
_FLAT_DIMENSION_RE = re.compile(
    r"^\s*(?:[\w.]+\.)?dimension\s*=\s*(\d+)\s*$", re.MULTILINE
)


def _find_dimension(node: Any) -> Optional[int]:
    """在已解析的 JSON 结构中递归查找首个 ``dimension`` 整数值。

    与 entrypoint.sh 的 ``grep -oP '"dimension"\\s*:\\s*\\K[0-9]+'`` 行为对齐：
    取配置中出现的第一个 dimension 数值，不限定嵌套层级
    （实际 ov.conf 中位于 ``embedding.dense.dimension``）。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "dimension":
                if isinstance(value, bool):
                    continue
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip())
            found = _find_dimension(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_dimension(item)
            if found is not None:
                return found
    return None


def parse_config(content: str) -> dict:
    """解析 JSON 配置内容。

    Raises:
        CustomError: EMBEDDING_CONFIG_INVALID_JSON
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_INVALID_JSON)
    if not isinstance(parsed, dict):
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_INVALID_JSON)
    return parsed


def parse_dimension(content: str) -> Optional[int]:
    """从配置内容中解析 embedding 维度，解析不到返回 ``None``（不抛异常）。

    优先按 JSON 解析（与线上 ov.conf 一致），失败时退回扁平 key 正则，
    以兼容 ``embedding.dimension = 1536`` 这类写法。
    """
    if not content or not content.strip():
        return None

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = None

    if parsed is not None:
        found = _find_dimension(parsed)
        if found is not None:
            return found

    match = _FLAT_DIMENSION_RE.search(content)
    if match:
        return int(match.group(1))
    return None


def compute_content_hash(content: str) -> str:
    """计算配置内容的 SHA256，与 entrypoint.sh 的 ``sha256sum`` 对齐。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_embedding_config(content: str) -> Tuple[int, str]:
    """校验 OpenViking embedding 配置。

    校验项：
    1. 内容非空
    2. 是合法 JSON（entrypoint.sh 会拒绝非法 JSON 并拒绝启动）
    3. 含 dimension 且在 1-4096 范围内

    Returns:
        (dimension, content_hash)

    Raises:
        CustomError: 配置格式错误
    """
    if not content or not content.strip():
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_INVALID_JSON)

    parsed = parse_config(content)

    dimension = _find_dimension(parsed)
    if dimension is None:
        # 兼容扁平写法，避免误伤非标准但可解析的配置
        match = _FLAT_DIMENSION_RE.search(content)
        dimension = int(match.group(1)) if match else None

    if dimension is None:
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_MISSING_DIMENSION)

    if dimension < MIN_DIMENSION or dimension > MAX_DIMENSION:
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_INVALID_DIMENSION)

    return dimension, compute_content_hash(content)


# --------------------------------------------------------------------------
# 以下为 Task 3 / Task 4 的落地点，当前为占位实现。
# 保持签名稳定，views_embedding 已按此签名调用。
# --------------------------------------------------------------------------


def write_config_to_disk(content: str) -> None:
    """将配置写入 ``$RY_DATA_DIR/openviking/ov.conf``。

    TODO(Task 3): 实现落盘 + 权限设置 + 旧配置备份。
    """
    return None


async def restart_openviking_container() -> None:
    """重启 OpenViking 容器（Supervisor 优先，降级 docker CLI）。

    TODO(Task 3): 实现重启编排与 /health 就绪等待。
    """
    return None


async def schedule_reindex(config_id: str, tenant_id: str) -> Optional[str]:
    """维度变化后创建 ReindexTask 并拉起后台重建。

    TODO(Task 4): 实现任务创建与后台 worker 调度。

    Returns:
        任务 ID；当前占位实现返回 ``None``。
    """
    return None
