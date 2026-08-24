# coding=utf-8
"""OpenViking embedding 配置：解析、校验与应用编排。

配置格式与 middleware/lion_viking 保持一致：**JSON**。
lion_viking 的 entrypoint.sh 用 ``python3 -m json.tool`` 校验配置合法性，
并用 ``"dimension": <int>`` 提取维度、``sha256sum`` 比对内容哈希，
维度变化时清空 ``/app/data/accounts`` 下的向量库。
本模块的解析/哈希逻辑必须与该 entrypoint 对齐，否则会写出无法启动的配置。
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Tuple

import httpx

from core.error_code import ErrorCode
from libs import supervisor
from libs.error_code.exception import CustomError
from settings import (
    OPENVIKING_ADMIN_BASE_URL,
    OPENVIKING_CONFIG_BACKUP_NAME,
    OPENVIKING_CONFIG_DIR,
    OPENVIKING_CONTAINER_NAME,
    OPENVIKING_READY_TIMEOUT,
    OPENVIKING_SUPERVISOR_PROGRAM,
)

logger = logging.getLogger(__name__)

# 与常见 embedding 模型对齐的合法维度上限
MIN_DIMENSION = 1
MAX_DIMENSION = 4096

# /health 轮询参数
HEALTH_POLL_INTERVAL = 2.0
HEALTH_REQUEST_TIMEOUT = 5.0

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
# 配置落盘与容器重启编排（Task 3）
# --------------------------------------------------------------------------


def _config_paths() -> Tuple[Path, Path, Path]:
    """返回 (配置目录, ov.conf 路径, 备份路径)。

    单独抽出便于单测 monkeypatch ``OPENVIKING_CONFIG_DIR``。
    """
    config_dir = Path(OPENVIKING_CONFIG_DIR)
    return (
        config_dir,
        config_dir / "ov.conf",
        config_dir / OPENVIKING_CONFIG_BACKUP_NAME,
    )


def write_config_to_disk(content: str) -> None:
    """将配置写入 ``$OPENVIKING_CONFIG_DIR/ov.conf``。

    要点：
    1. **写前校验 JSON**。entrypoint.sh 对非法 JSON 会拒绝启动（无备份时直接
       ``exit 1`` 造成 crash loop），所以宁可在这里失败也不能写坏配置。
    2. **原地截断写入，不用"临时文件 + rename"**。ov.conf 是以单文件形式
       bind mount 进 lion-viking 容器的（``./data/viking/ov.conf:/app/ov.conf:ro``），
       rename 会换掉 inode，导致容器仍看到旧文件。
    3. 写入前把旧配置备份到 ``.ov_conf_backup``——entrypoint.sh 在配置校验失败时
       会从这个文件恢复（容器内路径 ``/app/data/.ov_conf_backup``）。

    Raises:
        CustomError: 配置非法（EMBEDDING_CONFIG_INVALID_JSON）或落盘失败
                     （EMBEDDING_CONFIG_APPLY_FAILED）
    """
    # 写前校验：非法 JSON 会让 lion-viking 起不来
    parse_config(content)

    config_dir, config_file, backup_file = _config_paths()

    try:
        config_dir.mkdir(parents=True, exist_ok=True)

        # 备份旧配置，供 entrypoint.sh 校验失败时回滚
        if config_file.exists():
            try:
                backup_file.write_text(
                    config_file.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except OSError as ex:
                # 备份失败不阻塞主流程，只降低回滚能力
                logger.warning(f"backup openviking config failed: {ex}")

        # 原地截断写入，保持 inode 不变（bind mount 依赖 inode）
        with open(config_file, "w", encoding="utf-8") as fp:
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())

        os.chmod(config_file, 0o644)
    except OSError as ex:
        logger.error(f"write openviking config to {config_file} failed: {ex}")
        raise CustomError(ErrorCode.EMBEDDING_CONFIG_APPLY_FAILED)

    logger.info(f"openviking config written to {config_file}")


async def _restart_via_supervisor() -> bool:
    """尝试通过 Supervisor 重启。

    ``libs.supervisor`` 是同步 XMLRPC 调用，放到线程池避免阻塞事件循环。
    返回 False 表示未托管/不可达，由调用方降级。
    """
    try:
        return await asyncio.to_thread(
            supervisor.restart_process, OPENVIKING_SUPERVISOR_PROGRAM
        )
    except Exception as ex:
        logger.warning(f"supervisor restart openviking failed: {ex}")
        return False


async def _restart_via_docker() -> bool:
    """尝试通过 docker CLI 重启容器。返回 False 表示不可用或执行失败。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "restart",
            OPENVIKING_CONTAINER_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        # 镜像里没装 docker CLI，或未挂载 docker.sock
        logger.warning("docker CLI not available, cannot restart openviking")
        return False
    except Exception as ex:
        logger.warning(f"spawn docker restart failed: {ex}")
        return False

    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        logger.info(f"openviking restarted via docker CLI: {OPENVIKING_CONTAINER_NAME}")
        return True

    detail = (stderr or stdout or b"").decode("utf-8", "replace").strip()
    logger.warning(f"docker restart {OPENVIKING_CONTAINER_NAME} failed: {detail}")
    return False


async def restart_openviking_container() -> None:
    """重启 OpenViking 使新配置生效（Supervisor 优先，降级 docker CLI）。

    重启后 entrypoint.sh 会比对配置哈希；维度变化时清空 ``/app/data/accounts``
    下的向量库，因此调用方需在重启后等待 ``/health`` 就绪再触发 reindex。

    Raises:
        CustomError: EMBEDDING_CONFIG_RESTART_FAILED（两种方式都没成功）
    """
    if await _restart_via_supervisor():
        logger.info(f"openviking restarted via supervisor: {OPENVIKING_SUPERVISOR_PROGRAM}")
        return

    if await _restart_via_docker():
        return

    logger.error("failed to restart openviking via supervisor and docker CLI")
    raise CustomError(ErrorCode.EMBEDDING_CONFIG_RESTART_FAILED)


async def wait_for_openviking_ready(timeout: Optional[int] = None) -> None:
    """轮询 OpenViking ``/health``，等待重启后的服务就绪。

    Args:
        timeout: 超时秒数，默认取 ``OPENVIKING_READY_TIMEOUT``（120s）。
                 维度变化时容器要先清空向量库，耗时会明显变长。

    Raises:
        CustomError: EMBEDDING_SERVICE_NOT_READY（超时仍未就绪）
    """
    if timeout is None:
        timeout = OPENVIKING_READY_TIMEOUT

    base_url = (OPENVIKING_ADMIN_BASE_URL or "").rstrip("/")
    if not base_url:
        logger.warning("OPENVIKING_ADMIN_BASE_URL not configured, skip readiness wait")
        return

    health_url = f"{base_url}/health"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_error: Optional[str] = None

    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get(health_url, timeout=HEALTH_REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    logger.info("openviking is ready")
                    return
                last_error = f"HTTP {resp.status_code}"
            except Exception as ex:
                # 重启窗口内连接被拒是预期现象，继续轮询
                last_error = str(ex)

            if loop.time() >= deadline:
                logger.error(
                    f"openviking not ready within {timeout}s, last error: {last_error}"
                )
                raise CustomError(ErrorCode.EMBEDDING_SERVICE_NOT_READY)

            await asyncio.sleep(HEALTH_POLL_INTERVAL)


# --------------------------------------------------------------------------
# 以下为 Task 4 的落地点，当前为占位实现。
# --------------------------------------------------------------------------


async def schedule_reindex(config_id: str, tenant_id: str) -> Optional[str]:
    """维度变化后创建 ReindexTask 并拉起后台重建。

    TODO(Task 4): 实现任务创建与后台 worker 调度。

    Returns:
        任务 ID；当前占位实现返回 ``None``。
    """
    return None
