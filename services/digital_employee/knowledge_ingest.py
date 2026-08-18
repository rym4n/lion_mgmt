# coding=utf-8
"""技能包随包文档 ingest 到 OpenViking（阶段三 §5.11.7）。

两段式上传：
1. 调 MCP add_resource 拿 upload URL
2. 立即 multipart 上传并解析 root_uri/task_id

worker 属于 lion_mgmt，不是 lion_agent。
"""

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx
from sanic.log import logger

from models.digital_employee import PackageIngestOutbox

# 文档白名单（禁止 .py/.sh/.yaml/.json/.env/.key 等）
ALLOWED_DOC_SUFFIXES = {".md", ".txt", ".pdf", ".rst", ".adoc"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB

# 状态常量
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _compute_content_hash(content: bytes) -> str:
    """计算文件内容 SHA256。"""
    return hashlib.sha256(content).hexdigest()


def _parse_upload_url_from_mcp_response(response_text: str) -> str | None:
    """从 MCP add_resource 的自然语言响应中解析 upload URL。

    预期格式：
    "Please upload your file to: http://lion-viking:8000/api/v1/resources/temp_upload?token=..."
    """
    match = re.search(r'https?://[^\s]+/temp_upload\?token=[^\s"\']+', response_text)
    if match:
        return match.group(0)
    return None


def _make_target_uri(
    tenant_short: str, package_type: str, package_key: str, version: str, filename: str
) -> str:
    """确定性目标 URI：viking://resources/{tenant}/{type}/{key}/{version}/{filename}"""
    safe_tenant = tenant_short.replace("/", "_")
    safe_type = package_type.replace("/", "_")
    safe_key = package_key.replace("/", "_")
    safe_version = str(version).replace("/", "_")
    safe_filename = filename.replace("/", "_")
    return f"viking://resources/{safe_tenant}/{safe_type}/{safe_key}/{safe_version}/{safe_filename}"


async def collect_package_docs(package_root: Path) -> list[tuple[Path, str]]:
    """扫描包目录，收集符合白名单的文档。

    返回 [(绝对路径, 相对路径), ...]
    """
    if not package_root.exists() or not package_root.is_dir():
        return []

    docs: list[tuple[Path, str]] = []
    total_size = 0

    for file_path in sorted(package_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in ALLOWED_DOC_SUFFIXES:
            continue

        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            logger.warning(
                "knowledge_ingest: skip oversized file path=%s size=%d max=%d",
                file_path,
                file_size,
                MAX_FILE_SIZE,
            )
            continue

        total_size += file_size
        if total_size > MAX_TOTAL_SIZE:
            logger.warning(
                "knowledge_ingest: total size exceeded at file=%s total=%d max=%d",
                file_path,
                total_size,
                MAX_TOTAL_SIZE,
            )
            break

        relative = file_path.relative_to(package_root).as_posix()
        docs.append((file_path, relative))

    return docs


def _is_knowledge_layer_enabled() -> bool:
    """检查知识层是否启用（阶段三）。

    阶段三未实施前返回 False，跳过 ingest。
    """
    try:
        from settings import OPENVIKING_ENDPOINT

        return bool(OPENVIKING_ENDPOINT)
    except (ImportError, AttributeError):
        return False


async def ingest_package_docs(
    tenant_id: str,
    package_identity: str,
    package_type: str,
    package_key: str,
    version: str,
    doc_files: list[tuple[Path, str]],
    knowledge_client: Any = None,  # KnowledgeClient，阶段三前为 None
) -> dict[str, Any]:
    """两段式上传随包文档到 OpenViking。

    Args:
        tenant_id: 租户 ID
        package_identity: 包标识（type:key:version）
        package_type: 包类型（role/skill/bundle）
        package_key: 包 key
        version: 版本
        doc_files: [(绝对路径, 相对路径), ...]
        knowledge_client: KnowledgeClient 实例（阶段三前为 None）

    Returns:
        {"succeeded": int, "failed": int, "skipped": int, "errors": [str]}
    """
    # 阶段三未启用时跳过
    if not _is_knowledge_layer_enabled() or knowledge_client is None:
        logger.info(
            "knowledge_ingest: knowledge layer not enabled, skip ingest tenant=%s package=%s",
            tenant_id,
            package_identity,
        )
        return {"succeeded": 0, "failed": 0, "skipped": len(doc_files), "errors": []}
    tenant_short = tenant_id[:8] if len(tenant_id) > 8 else tenant_id
    succeeded = 0
    failed = 0
    skipped = 0
    errors: list[str] = []

    for file_path, relative_path in doc_files:
        try:
            content = file_path.read_bytes()
            content_hash = _compute_content_hash(content)
            target_uri = _make_target_uri(
                tenant_short, package_type, package_key, version, relative_path
            )

            # 检查幂等：已成功的跳过
            existing = await PackageIngestOutbox.filter(
                tenant_id=tenant_id,
                package_identity=package_identity,
                content_hash=content_hash,
                status=STATUS_SUCCEEDED,
                deleted=False,
            ).first()
            if existing:
                logger.info(
                    "knowledge_ingest: skip already succeeded tenant=%s package=%s file=%s",
                    tenant_id,
                    package_identity,
                    relative_path,
                )
                skipped += 1
                continue

            # 写 outbox（幂等）
            outbox, created = await PackageIngestOutbox.get_or_create(
                tenant_id=tenant_id,
                package_identity=package_identity,
                content_hash=content_hash,
                defaults={
                    "file_path": relative_path,
                    "target_uri": target_uri,
                    "status": STATUS_PENDING,
                },
            )
            if not created and outbox.status == STATUS_SUCCEEDED:
                skipped += 1
                continue

            # 更新状态为 processing
            outbox.status = STATUS_PROCESSING
            outbox.attempt_count += 1
            await outbox.save()

            # ① 调 MCP add_resource 拿 upload URL
            add_resp = await knowledge_client.add_resource(
                uri=target_uri,
                source_type="local",
                source=str(file_path),
            )
            upload_url = _parse_upload_url_from_mcp_response(
                add_resp.get("content", "")
            )
            if not upload_url:
                raise ValueError(
                    f"Failed to parse upload URL from response: {add_resp}"
                )

            # ② multipart 上传
            async with httpx.AsyncClient(timeout=60.0) as client:
                files = {"file": (relative_path, content, "application/octet-stream")}
                resp = await client.post(upload_url, files=files)
                resp.raise_for_status()
                result = resp.json()

            # 解析 root_uri / task_id / 状态
            root_uri = result.get("root_uri")
            task_id = result.get("task_id")
            if not root_uri:
                raise ValueError(f"Upload response missing root_uri: {result}")

            # 标记成功
            outbox.status = STATUS_SUCCEEDED
            outbox.task_id = task_id
            outbox.error = None
            await outbox.save()

            logger.info(
                "knowledge_ingest: succeeded tenant=%s package=%s file=%s uri=%s task_id=%s",
                tenant_id,
                package_identity,
                relative_path,
                root_uri,
                task_id,
            )
            succeeded += 1

        except Exception as exc:
            failed += 1
            error_msg = f"{relative_path}: {exc}"
            errors.append(error_msg)
            logger.error(
                "knowledge_ingest: failed tenant=%s package=%s file=%s error=%s",
                tenant_id,
                package_identity,
                relative_path,
                exc,
                exc_info=True,
            )

            # 更新 outbox 失败状态
            try:
                outbox.status = STATUS_FAILED
                outbox.error = str(exc)[:1000]  # 截断
                await outbox.save()
            except Exception as save_exc:
                logger.error(
                    "knowledge_ingest: failed to save outbox error=%s", save_exc
                )

    return {
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
    }


async def forget_package_docs(
    tenant_id: str,
    package_identity: str,
    knowledge_client: Any = None,
) -> dict[str, Any]:
    """删除旧版本包的文档（升级或卸载时调用）。

    Args:
        tenant_id: 租户 ID
        package_identity: 包标识（type:key:version）
        knowledge_client: KnowledgeClient 实例（阶段三前为 None）

    Returns:
        {"deleted": int, "errors": [str]}
    """
    # 阶段三未启用时跳过
    if not _is_knowledge_layer_enabled() or knowledge_client is None:
        logger.info(
            "knowledge_ingest: knowledge layer not enabled, skip forget tenant=%s package=%s",
            tenant_id,
            package_identity,
        )
        return {"deleted": 0, "errors": []}
    # 查询该 package_identity 的全部 URI
    outbox_records = await PackageIngestOutbox.filter(
        tenant_id=tenant_id,
        package_identity=package_identity,
        status=STATUS_SUCCEEDED,
        deleted=False,
    ).all()

    deleted = 0
    errors: list[str] = []

    for record in outbox_records:
        try:
            # 调 MCP forget
            await knowledge_client.forget(uri=record.target_uri, recursive=False)
            # 软删 outbox 记录
            record.deleted = True
            await record.save()
            deleted += 1
            logger.info(
                "knowledge_ingest: forgot uri=%s tenant=%s package=%s",
                record.target_uri,
                tenant_id,
                package_identity,
            )
        except Exception as exc:
            errors.append(f"{record.target_uri}: {exc}")
            logger.error(
                "knowledge_ingest: forget failed uri=%s error=%s",
                record.target_uri,
                exc,
                exc_info=True,
            )

    return {"deleted": deleted, "errors": errors}
