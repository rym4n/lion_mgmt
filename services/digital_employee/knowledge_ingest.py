# coding=utf-8
"""技能包随包文档入库（SPEC §11.3）。

改造要点（相对阶段三之前的实现）：
1. 不再自己调 OpenViking——统一走 services/knowledge/manager 的 upsert，
   这样随包文档也进 KnowledgeDocument 表，在管理界面可见、可删、有状态。
2. 每个技能包一个空间 skill-{skill_key}，员工绑了该技能即可见。
3. URI 不含租户前缀与版本号：租户隔离由 OpenViking account 保证；
   版本升级时同名文档直接覆盖，content_hash 相同则跳过。
"""

import hashlib
from pathlib import Path
from typing import Any

from sanic.log import logger

from models.digital_employee import PackageIngestOutbox
from services.knowledge.manager import ensure_space, get_document_or_404, upsert_document

ALLOWED_DOC_SUFFIXES = {".md", ".txt", ".pdf", ".rst", ".adoc"}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TOTAL_SIZE = 100 * 1024 * 1024

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def skill_space_key(skill_key: str) -> str:
    """技能包空间的命名：统一 skill- 前缀。"""
    return f"skill-{str(skill_key).replace('/', '-')}"


def _make_target_rel_path(relative_path: str) -> str:
    """包内相对路径直接作为空间内相对路径。"""
    return str(relative_path).replace("\\", "/").lstrip("/")


def _compute_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def collect_package_docs(package_root: Path) -> list[tuple[Path, str]]:
    """扫描包目录，收集符合白名单的文档。返回 [(绝对路径, 相对路径), ...]"""
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
                file_path, file_size, MAX_FILE_SIZE,
            )
            continue

        total_size += file_size
        if total_size > MAX_TOTAL_SIZE:
            logger.warning(
                "knowledge_ingest: total size exceeded at file=%s total=%d max=%d",
                file_path, total_size, MAX_TOTAL_SIZE,
            )
            break

        docs.append((file_path, file_path.relative_to(package_root).as_posix()))

    return docs


async def ingest_package_docs(
    tenant_id: str,
    package_identity: str,
    package_type: str,
    package_key: str,
    version: str,
    doc_files: list[tuple[Path, str]],
) -> dict[str, Any]:
    """把随包文档写入该技能包的知识空间。"""
    if not doc_files:
        return {"succeeded": 0, "failed": 0, "skipped": 0, "errors": []}

    space_key = skill_space_key(package_key)
    try:
        await ensure_space(
            tenant_id,
            space_key,
            name=f"技能：{package_key}",
            source_type="skill_package",
            source_ref=str(package_key),
        )
    except Exception as exc:
        logger.error(
            "knowledge_ingest: ensure_space failed tenant=%s space=%s error_type=%s",
            tenant_id, space_key, type(exc).__name__,
        )
        return {
            "succeeded": 0,
            "failed": len(doc_files),
            "skipped": 0,
            "errors": [f"创建技能空间失败: {type(exc).__name__}"],
        }

    succeeded = failed = skipped = 0
    errors: list[str] = []

    for file_path, relative_path in doc_files:
        outbox = None
        try:
            content = file_path.read_bytes()
            content_hash = _compute_content_hash(content)
            rel_path = _make_target_rel_path(relative_path)

            existing = await PackageIngestOutbox.filter(
                tenant_id=tenant_id,
                package_identity=package_identity,
                content_hash=content_hash,
                status=STATUS_SUCCEEDED,
                deleted=False,
            ).first()
            if existing:
                skipped += 1
                continue

            outbox, created = await PackageIngestOutbox.get_or_create(
                tenant_id=tenant_id,
                package_identity=package_identity,
                content_hash=content_hash,
                defaults={
                    "file_path": rel_path,
                    "target_uri": f"viking://resources/spaces/{space_key}/{rel_path}",
                    "status": STATUS_PENDING,
                },
            )
            if not created and outbox.status == STATUS_SUCCEEDED:
                skipped += 1
                continue

            outbox.status = STATUS_PROCESSING
            outbox.attempt_count += 1
            await outbox.save()

            result = await upsert_document(
                tenant_id,
                space_key=space_key,
                rel_path=rel_path,
                content=content,
                title=Path(rel_path).name,
                source="skill_package",
                source_ref=str(package_key),
            )

            outbox.status = STATUS_SUCCEEDED
            outbox.task_id = str(result.get("id") or "")
            outbox.error = None
            await outbox.save()
            succeeded += 1

        except Exception as exc:
            failed += 1
            errors.append(f"{relative_path}: {type(exc).__name__}")
            logger.error(
                "knowledge_ingest: failed tenant=%s package=%s file=%s error_type=%s",
                tenant_id, package_identity, relative_path, type(exc).__name__,
            )
            if outbox is not None:
                try:
                    outbox.status = STATUS_FAILED
                    outbox.error = str(exc)[:1000]
                    await outbox.save()
                except Exception:
                    logger.error("knowledge_ingest: outbox save failed")

    return {"succeeded": succeeded, "failed": failed, "skipped": skipped, "errors": errors}


async def forget_package_docs(tenant_id: str, package_identity: str) -> dict[str, Any]:
    """卸载或升级时删除该包写入的文档。"""
    from models.knowledge import KnowledgeDocument
    from services.knowledge.manager import delete_document

    records = await PackageIngestOutbox.filter(
        tenant_id=tenant_id,
        package_identity=package_identity,
        status=STATUS_SUCCEEDED,
        deleted=False,
    ).all()

    deleted = 0
    errors: list[str] = []

    for record in records:
        try:
            document = await KnowledgeDocument.get_or_none(
                tenant_id=tenant_id, uri=record.target_uri, deleted=False
            )
            if document:
                await delete_document(tenant_id, str(document.id))
            record.deleted = True
            await record.save()
            deleted += 1
        except Exception as exc:
            errors.append(f"{record.target_uri}: {type(exc).__name__}")
            logger.error(
                "knowledge_ingest: forget failed uri=%s error_type=%s",
                record.target_uri, type(exc).__name__,
            )

    return {"deleted": deleted, "errors": errors}
