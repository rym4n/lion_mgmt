# coding=utf-8
"""知识空间与文档的业务逻辑（SPEC §10）。

lion_mgmt 是知识库的唯一写入口：前端、剧本、数字伙伴的写入全部经此。
原文与派生文本落 RustFS，向量索引由 lion_agent 管理。
"""

import hashlib
import uuid
from datetime import datetime, timedelta

from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent
from models.knowledge import KnowledgeDocument, KnowledgeSpace
from models.tenant_embedding import get_embedding_payload
from models.user import Account
from services.knowledge import object_store
from services.knowledge.client import get_knowledge_client
from services.knowledge.scope import validate_space_key
from settings import LION_MGMT_INTERNAL_BASE_URL, TZ

# 回调丢失时的兜底阈值。lion_agent 完成后会回调更新状态；若回调丢了，
# 记录会永远停在 processing。列表查询时惰性判定，不需要定时器
# （这正是 scheduler.py 那套 Kafka timer 被删掉的原因，SPEC D5）。
STALE_PROCESSING_MINUTES = 30

ALLOWED_DOC_SUFFIXES = {
    ".md", ".txt", ".pdf", ".rst", ".csv", ".htm", ".html",
    ".json", ".xml", ".yaml", ".yml", ".docx", ".xlsx", ".xls", ".pptx",
}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_SPACE_SIZE = 100 * 1024 * 1024


async def get_space_or_404(tenant_id: str, space_key: str) -> KnowledgeSpace:
    space = await KnowledgeSpace.get_or_none(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if not space:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_NOT_FOUND)
    return space


async def _create_space_with_key(
    tenant_id: str,
    space_key: str,
    name: str,
    description: str | None = None,
    source_type: str = "manual",
    source_ref: str | None = None,
) -> KnowledgeSpace:
    """使用指定标识创建空间，供需要稳定标识的内部导入流程调用。"""
    validate_space_key(space_key)

    existing = await KnowledgeSpace.get_or_none(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if existing:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_KEY_CONFLICT)

    return await KnowledgeSpace.create(
        tenant_id=tenant_id,
        space_key=space_key,
        name=name,
        description=description,
        source_type=source_type,
        source_ref=source_ref,
    )


async def create_space(
    tenant_id: str,
    name: str,
    description: str | None = None,
) -> KnowledgeSpace:
    """创建人工空间并生成内部标识。"""
    space_key = f"space-{uuid.uuid4().hex}"
    return await _create_space_with_key(
        tenant_id,
        space_key,
        name,
        description=description,
    )


async def ensure_space(
    tenant_id: str,
    space_key: str,
    name: str,
    source_type: str = "manual",
    source_ref: str | None = None,
) -> KnowledgeSpace:
    """幂等创建。技能包导入会反复调用，已存在时直接返回。"""
    validate_space_key(space_key)
    existing = await KnowledgeSpace.get_or_none(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if existing:
        return existing
    return await _create_space_with_key(
        tenant_id,
        space_key,
        name,
        source_type=source_type,
        source_ref=source_ref,
    )


async def update_space(
    tenant_id: str,
    space_key: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> KnowledgeSpace:
    """只能改展示信息；space_key 已固化进向量 payload。"""
    space = await get_space_or_404(tenant_id, space_key)
    if name is not None:
        space.name = name
    if description is not None:
        space.description = description
    await space.save()
    return space


async def _employees_bound_to(tenant_id: str, space_key: str) -> list[str]:
    """找出绑定了该空间的员工 id。"""
    employees = await CommandAgent.filter(tenant_id=tenant_id, deleted=False).all()
    hit = []
    for employee in employees:
        keys = getattr(employee, "knowledge_space_keys", None) or []
        if space_key in keys:
            hit.append(str(employee.id))
    return hit


async def delete_space(tenant_id: str, space_key: str, *, dry_run: bool = False) -> dict:
    """软删空间及其本地文档记录。向量重建负责清理远端孤儿记录。"""
    space = await get_space_or_404(tenant_id, space_key)
    affected = await _employees_bound_to(tenant_id, space_key)
    document_count = await KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    ).count()

    result = {
        "space_key": space_key,
        "document_count": document_count,
        "affected_employees": affected,
    }
    if dry_run:
        return result

    for employee_id in affected:
        employee = await CommandAgent.get_or_none(id=employee_id, deleted=False)
        if not employee:
            continue
        keys = [k for k in (getattr(employee, "knowledge_space_keys", None) or []) if k != space_key]
        employee.knowledge_space_keys = keys
        if getattr(employee, "knowledge_write_space", None) == space_key:
            employee.knowledge_write_space = None
        await employee.save()

    await KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    ).update(deleted=True)
    space.deleted = True
    await space.save()
    logger.info(
        "knowledge_space_deleted tenant=%s space=%s documents=%d employees=%d",
        tenant_id, space_key, document_count, len(affected),
    )
    return result


async def list_spaces(tenant_id: str, page: int = 1, size: int = 20) -> dict:
    """空间列表，带各空间的文档数与状态汇总。

    只返回 source_type=manual 的空间：skill_package 空间由技能管理维护，
    它是技能包的附属产物而非用户的知识空间，混在这里既不可编辑也无从理解
    （知识空间页曾因此展示出一堆技能包）。需要技能包空间的调用方走技能侧接口。
    """
    query = KnowledgeSpace.filter(
        tenant_id=tenant_id, deleted=False, source_type="manual"
    )
    total = await query.count()
    spaces = await query.offset((page - 1) * size).limit(size).all()

    items = []
    for space in spaces:
        base = KnowledgeDocument.filter(
            tenant_id=tenant_id, space_key=space.space_key, deleted=False
        )
        payload = dict(space.json)
        payload["document_count"] = await base.count()
        payload["processing_count"] = await base.filter(
            status__in=["pending", "processing"]
        ).count()
        payload["failed_count"] = await base.filter(status="failed").count()
        items.append(payload)

    return {"total": total, "page": page, "size": size, "items": items}


def _check_suffix(filename: str) -> None:
    from pathlib import PurePosixPath

    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in ALLOWED_DOC_SUFFIXES:
        raise CustomError(ErrorCode.KNOWLEDGE_FILE_TYPE_REJECTED)


async def _check_space_quota(tenant_id: str, space_key: str, incoming: int) -> None:
    """空间总量上限。用已有文档 size 之和估算。"""
    documents = await KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    ).all()
    used = sum(int(doc.size or 0) for doc in documents)
    if used + incoming > MAX_SPACE_SIZE:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_QUOTA_EXCEEDED)


def _callback_url(document_id: str) -> str:
    base = (LION_MGMT_INTERNAL_BASE_URL or "").rstrip("/")
    return f"{base}/knowledge/internal/documents/{document_id}/status"


async def upsert_document(
    tenant_id: str,
    *,
    space_key: str,
    content: bytes,
    filename: str,
    title: str | None = None,
    source: str = "manual",
    source_ref: str | None = None,
    uploader_id: str | None = None,
) -> dict:
    """写入或更新一个文档。

    幂等按 (tenant, space_key, content_hash) 判定：同一空间里内容相同
    就是同一篇，重新解析向量化是纯浪费，还会把已 ready 的打回 processing。

    流程：存 RustFS → 建行 → 提交 ingest（异步）→ lion_agent 回调更新状态。
    """
    validate_space_key(space_key)
    _check_suffix(filename)

    if not content:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if len(content) > MAX_FILE_SIZE:
        raise CustomError(ErrorCode.KNOWLEDGE_FILE_TOO_LARGE)

    space = await get_space_or_404(tenant_id, space_key)
    digest = hashlib.sha256(content).hexdigest()

    existing = await KnowledgeDocument.get_or_none(
        tenant_id=tenant_id, space_key=space_key, content_hash=digest, deleted=False
    )
    if existing and existing.status == "ready":
        return {**existing.json, "skipped": True}

    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        raise CustomError(ErrorCode.KNOWLEDGE_EMBEDDING_NOT_CONFIGURED)

    previous_size = int(existing.size or 0) if existing else 0
    await _check_space_quota(tenant_id, space_key, max(0, len(content) - previous_size))

    document = existing
    if document is None:
        document = await KnowledgeDocument.create(
            tenant_id=tenant_id,
            space=space,
            space_key=space_key,
            title=title or filename,
            size=len(content),
            content_hash=digest,
            source=source,
            source_ref=source_ref,
            uploader_id=uploader_id,
            status="processing",
        )
    else:
        document.title = title or document.title or filename
        document.size = len(content)
        document.uploader_id = uploader_id
        document.status = "processing"
        document.error = None
        document.indexed_at = None

    document.storage_key = await object_store.put_raw(str(document.id), content)
    await document.save()

    await get_knowledge_client().ingest(
        tenant_id=tenant_id,
        space_key=space_key,
        document_id=str(document.id),
        storage_key=document.storage_key,
        filename=filename,
        embedding=embedding,
        callback_url=_callback_url(str(document.id)),
    )
    return {**document.json, "skipped": False}


async def get_document_or_404(tenant_id: str, document_id: str) -> KnowledgeDocument:
    document = await KnowledgeDocument.get_or_none(
        id=document_id, tenant_id=tenant_id, deleted=False
    )
    if not document:
        raise CustomError(ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND)
    return document


def _mark_stale(document: KnowledgeDocument) -> dict:
    """processing 超时按 failed 呈现，但不改库——回调可能只是晚到。"""
    payload = document.json
    if document.status == "processing":
        deadline = datetime.now(TZ) - timedelta(minutes=STALE_PROCESSING_MINUTES)
        if document.utime and document.utime < deadline:
            payload["status"] = "failed"
            payload["error"] = "索引超时未完成，请重试"
    return payload


async def list_documents(
    tenant_id: str,
    space_key: str,
    *,
    page: int = 1,
    size: int = 20,
    status: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
) -> dict:
    await get_space_or_404(tenant_id, space_key)
    query = KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if status:
        query = query.filter(status=status)
    if source:
        query = query.filter(source=source)
    if keyword:
        query = query.filter(title__icontains=keyword)

    total = await query.count()
    documents = await query.offset((page - 1) * size).limit(size).all()
    updater_ids = {doc.uploader_id for doc in documents if doc.uploader_id}
    updaters = {}
    if updater_ids:
        accounts = await Account.filter(
            id__in=list(updater_ids), tenant_id=tenant_id
        ).all()
        updaters = {
            str(account.id): {
                "username": account.username,
                "display_name": account.display_name,
            }
            for account in accounts
        }

    items = []
    for document in documents:
        payload = _mark_stale(document)
        payload["updator"] = updaters.get(str(document.uploader_id))
        items.append(payload)
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": items,
    }


async def document_detail(tenant_id: str, document_id: str) -> dict:
    document = await get_document_or_404(tenant_id, document_id)
    payload = _mark_stale(document)
    payload["content"] = await object_store.get_text(str(document.id))
    return payload


async def delete_document(tenant_id: str, document_id: str) -> dict:
    """软删本地记录，并尽力清理向量与原文对象。

    本地删除是用户可见状态的唯一真相。远端清理短暂失败时不能阻塞
    删除，否则文档会表现为「只能添加、不能删除」；孤儿向量会在重建
    时清理，且检索装饰阶段会过滤掉已软删的文档。
    """
    document = await get_document_or_404(tenant_id, document_id)
    document.deleted = True
    await document.save()

    try:
        embedding = await get_embedding_payload(tenant_id)
    except Exception as exc:
        embedding = None
        logger.warning(
            "knowledge_embedding_for_delete_failed doc=%s error_type=%s",
            document_id, type(exc).__name__,
        )
    if embedding is not None:
        try:
            await get_knowledge_client().delete_document(
                tenant_id=tenant_id,
                space_key=document.space_key,
                document_id=str(document.id),
                embedding=embedding,
            )
        except Exception as exc:
            # 向量侧删不掉不该阻塞元数据删除——留下的孤儿记录检索时
            # 会因为查不到对应文档行而被过滤掉，且 rebuild 会清干净。
            logger.warning(
                "knowledge_vector_delete_failed doc=%s error_type=%s",
                document_id, type(exc).__name__,
            )
    try:
        await object_store.delete_objects(str(document.id))
    except Exception as exc:
        logger.warning(
            "knowledge_object_delete_failed doc=%s error_type=%s",
            document_id, type(exc).__name__,
        )
    logger.info(
        "knowledge_document_deleted tenant=%s space=%s doc=%s",
        tenant_id, document.space_key, document_id,
    )
    return {"deleted": True, "document_id": document_id}


async def search_documents_by_title(
    tenant_id: str,
    keyword: str,
    *,
    page: int = 1,
    size: int = 20,
) -> dict:
    """按文档标题模糊搜索，并返回文档所属空间名称。

    自动排除 skill_package 空间的文档——与 list_spaces 的过滤保持一致。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return {"total": 0, "page": page, "size": size, "items": []}

    # 只在 manual 空间里搜。用「先取候选空间再按 space_key__in 限定」而非
    # exclude(skill_package)：租户没有任何 manual 空间时结果必须为空，
    # 而 exclude 在空集合下会退化成「搜全部」。
    manual_spaces = await KnowledgeSpace.filter(
        tenant_id=tenant_id, deleted=False, source_type="manual"
    ).all()
    manual_space_keys = [space.space_key for space in manual_spaces]
    if not manual_space_keys:
        return {"total": 0, "page": page, "size": size, "items": []}

    query = KnowledgeDocument.filter(
        tenant_id=tenant_id,
        deleted=False,
        title__icontains=keyword,
        space_key__in=manual_space_keys,
    )
    total = await query.count()
    documents = await query.offset((page - 1) * size).limit(size).all()

    space_names = {space.space_key: space.name for space in manual_spaces}

    updater_ids = {doc.uploader_id for doc in documents if doc.uploader_id}
    updaters = {}
    if updater_ids:
        accounts = await Account.filter(
            id__in=list(updater_ids), tenant_id=tenant_id
        ).all()
        updaters = {
            str(account.id): {
                "username": account.username,
                "display_name": account.display_name,
            }
            for account in accounts
        }

    items = []
    for document in documents:
        payload = _mark_stale(document)
        payload["space_name"] = space_names.get(document.space_key, document.space_key)
        payload["updator"] = updaters.get(str(document.uploader_id))
        items.append(payload)
    return {"total": total, "page": page, "size": size, "items": items}


async def retry_document(
    tenant_id: str, document_id: str, *, updater_id: str | None = None
) -> dict:
    """重新提交失败的文档。

    原文还在 RustFS，直接重新提交 ingest 即可。
    """
    document = await get_document_or_404(tenant_id, document_id)
    if document.status not in ("failed", "processing"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        raise CustomError(ErrorCode.KNOWLEDGE_EMBEDDING_NOT_CONFIGURED)

    document.status = "processing"
    document.error = None
    if updater_id is not None:
        document.uploader_id = updater_id
    await document.save()
    await get_knowledge_client().ingest(
        tenant_id=tenant_id,
        space_key=document.space_key,
        document_id=str(document.id),
        storage_key=document.storage_key,
        filename=document.title,
        embedding=embedding,
        callback_url=_callback_url(str(document.id)),
    )
    return document.json


async def search_preview(
    tenant_id: str, query: str, *, space_key: str | None = None, top_k: int = 5
) -> list[dict]:
    """管理员的检索预览：范围是本租户的全部空间（或指定的一个）。"""
    spaces = await KnowledgeSpace.filter(tenant_id=tenant_id, deleted=False).all()
    keys = [s.space_key for s in spaces]
    if space_key:
        keys = [k for k in keys if k == space_key]
    if not keys:
        return []

    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        raise CustomError(ErrorCode.KNOWLEDGE_EMBEDDING_NOT_CONFIGURED)

    hits = await get_knowledge_client().search(
        tenant_id=tenant_id, space_keys=keys, query=query,
        top_k=top_k, embedding=embedding,
    )
    return await _decorate_hits(tenant_id, hits)


async def _decorate_hits(tenant_id: str, hits: list[dict]) -> list[dict]:
    """补上标题。lion_agent 只回 document_id/space_key/excerpt/score。"""
    decorated = []
    for hit in hits:
        document = await KnowledgeDocument.get_or_none(
            id=hit.get("document_id"), tenant_id=tenant_id, deleted=False
        )
        if document is None:
            # 向量库里有、元数据表里没有 => 孤儿记录，跳过不展示
            continue
        decorated.append({
            "document_id": str(document.id),
            "title": document.title,
            "space_key": document.space_key,
            "excerpt": hit.get("excerpt") or "",
            "score": hit.get("score"),
        })
    return decorated


async def health(tenant_id: str) -> dict:
    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        return {"available": False, "reason": "embedding_not_configured"}
    try:
        await get_knowledge_client().capabilities()
        return {"available": True}
    except CustomError:
        return {"available": False, "reason": "service_unavailable"}
