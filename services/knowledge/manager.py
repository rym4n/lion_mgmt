# coding=utf-8
"""知识空间与文档的业务逻辑（SPEC §10）。

lion_mgmt 是知识库的唯一写入口：前端、剧本、数字员工的写入全部经此。
所有 OpenViking 调用都在这一层发起，views 层不直接碰 client。
"""

import hashlib
from datetime import datetime

from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent
from models.knowledge import KnowledgeDocument, KnowledgeSpace
from settings import TZ
from services.knowledge.client import ensure_knowledge_client, get_knowledge_client
from services.knowledge.scope import (
    build_uri,
    uri_in_space,
    validate_rel_path,
    validate_space_key,
)


async def get_space_or_404(tenant_id: str, space_key: str) -> KnowledgeSpace:
    space = await KnowledgeSpace.get_or_none(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if not space:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_NOT_FOUND)
    return space


async def create_space(
    tenant_id: str,
    space_key: str,
    name: str,
    description: str | None = None,
    source_type: str = "manual",
    source_ref: str | None = None,
) -> KnowledgeSpace:
    """创建空间，并在 OpenViking 建对应目录。"""
    validate_space_key(space_key)

    existing = await KnowledgeSpace.get_or_none(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if existing:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_KEY_CONFLICT)

    client = await get_knowledge_client(tenant_id)
    if client is None:
        raise CustomError(ErrorCode.KNOWLEDGE_NOT_PROVISIONED)

    from models.knowledge import SPACES_URI_PREFIX

    await client.mkdir(f"{SPACES_URI_PREFIX}/{space_key}")
    return await KnowledgeSpace.create(
        tenant_id=tenant_id,
        space_key=space_key,
        name=name,
        description=description,
        source_type=source_type,
        source_ref=source_ref,
    )


async def ensure_space(
    tenant_id: str,
    space_key: str,
    name: str,
    source_type: str = "manual",
    source_ref: str | None = None,
) -> KnowledgeSpace:
    """幂等创建。技能包导入会反复调用，已存在时直接返回。"""
    existing = await KnowledgeSpace.get_or_none(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    )
    if existing:
        return existing
    return await create_space(
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
    """只能改展示信息；space_key 已固化进 URI。"""
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
    """软删空间；OpenViking 内容保留以便误删恢复。"""
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
        tenant_id,
        space_key,
        document_count,
        len(affected),
    )
    return result


async def list_spaces(tenant_id: str, page: int = 1, size: int = 20) -> dict:
    """空间列表，带各空间的文档数与状态汇总。"""
    query = KnowledgeSpace.filter(tenant_id=tenant_id, deleted=False)
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


# 文档白名单与配额（沿用 knowledge_ingest.py 的既有取值）
ALLOWED_DOC_SUFFIXES = {".md", ".txt", ".pdf", ".rst", ".adoc"}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_SPACE_SIZE = 100 * 1024 * 1024


def _check_suffix(rel_path: str) -> None:
    from pathlib import PurePosixPath

    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix not in ALLOWED_DOC_SUFFIXES:
        raise CustomError(ErrorCode.KNOWLEDGE_FILE_TYPE_REJECTED)


async def _check_space_quota(tenant_id: str, space_key: str, incoming: int) -> None:
    """空间总量上限。用已有文档 size 之和估算，不去 OpenViking 实测。"""
    documents = await KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    ).all()
    used = sum(int(doc.size or 0) for doc in documents)
    if used + incoming > MAX_SPACE_SIZE:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_QUOTA_EXCEEDED)


async def upsert_document(
    tenant_id: str,
    *,
    space_key: str,
    rel_path: str,
    content: bytes | None = None,
    url: str | None = None,
    title: str | None = None,
    instruction: str | None = None,
    source: str = "manual",
    source_ref: str | None = None,
    uploader_id: str | None = None,
) -> dict:
    """写入或更新一个文档，幂等按 (tenant, uri) + content_hash 判定。

    content 与 url 二选一。content_hash 相同则直接返回，不重新提交
    ——重新解析向量化是纯浪费，还会把已 ready 的文档打回 processing。
    """
    validate_space_key(space_key)
    normalized = validate_rel_path(rel_path)
    _check_suffix(normalized)

    if content is None and not url:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if content is not None and len(content) > MAX_FILE_SIZE:
        raise CustomError(ErrorCode.KNOWLEDGE_FILE_TOO_LARGE)

    space = await get_space_or_404(tenant_id, space_key)
    uri = build_uri(space_key, normalized)

    # URL 来源没有本地内容，用 URL 串本身做 hash，保证同 URL 重复调用可判重
    payload = content if content is not None else str(url).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    existing = await KnowledgeDocument.get_or_none(
        tenant_id=tenant_id, uri=uri, deleted=False
    )
    if existing and existing.content_hash == digest and existing.status == "ready":
        return {**existing.json, "skipped": True}

    client = await get_knowledge_client(tenant_id)
    if client is None:
        raise CustomError(ErrorCode.KNOWLEDGE_NOT_PROVISIONED)

    size = len(content) if content is not None else 0
    if content is not None:
        # 更新文档时只计入相对旧版本增加的字节数，避免把旧文件重复计入配额。
        previous_size = int(existing.size or 0) if existing else 0
        await _check_space_quota(
            tenant_id, space_key, max(0, size - previous_size)
        )

    from pathlib import PurePosixPath

    filename = PurePosixPath(normalized).name
    if content is not None:
        result = await client.upload_file(
            to=uri, filename=filename, content=content, instruction=instruction
        )
    else:
        result = await client.add_url(to=uri, url=str(url), instruction=instruction)

    task_id = (result or {}).get("task_id")

    if existing:
        existing.content_hash = digest
        existing.size = size
        existing.title = title or existing.title or filename
        existing.status = "processing"
        existing.task_id = task_id
        existing.error = None
        existing.indexed_at = None
        await existing.save()
        return {**existing.json, "skipped": False}

    document = await KnowledgeDocument.create(
        tenant_id=tenant_id,
        space=space,
        space_key=space_key,
        rel_path=normalized,
        uri=uri,
        title=title or filename,
        size=size,
        content_hash=digest,
        source=source,
        source_ref=source_ref,
        uploader_id=uploader_id,
        status="processing",
        task_id=task_id,
    )
    return {**document.json, "skipped": False}


async def get_document_or_404(tenant_id: str, document_id: str) -> KnowledgeDocument:
    document = await KnowledgeDocument.get_or_none(
        id=document_id, tenant_id=tenant_id, deleted=False
    )
    if not document:
        raise CustomError(ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND)
    return document


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
    if documents:
        from services.knowledge.status import refresh_pending

        await refresh_pending(tenant_id, documents)
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [doc.json for doc in documents],
    }


async def document_detail(tenant_id: str, document_id: str) -> dict:
    document = await get_document_or_404(tenant_id, document_id)
    payload = document.json
    client = await get_knowledge_client(tenant_id)
    if client is None:
        payload["content"] = ""
        return payload
    try:
        payload["content"] = await client.read(document.uri)
    except CustomError:
        # 存储侧读不到不该让整个详情接口失败——元数据仍有展示价值
        payload["content"] = ""
        payload["content_error"] = True
    return payload


async def delete_document(tenant_id: str, document_id: str) -> dict:
    """真删 OpenViking 内容 + 软删本地记录。"""
    document = await get_document_or_404(tenant_id, document_id)
    client = await get_knowledge_client(tenant_id)
    if client is None:
        raise CustomError(ErrorCode.KNOWLEDGE_NOT_PROVISIONED)

    await client.rm(document.uri)
    document.deleted = True
    await document.save()
    logger.info(
        "knowledge_document_deleted tenant=%s space=%s doc=%s",
        tenant_id,
        document.space_key,
        document_id,
    )
    return {"deleted": True, "document_id": document_id}


async def retry_document(tenant_id: str, document_id: str) -> dict:
    """重新提交失败的文档。内容仍在 OpenViking 侧的临时区已不可用，
    因此只对 url 来源或仍能重新读到内容的情况有效；
    对本地上传的失败项，提示用户重新上传。"""
    document = await get_document_or_404(tenant_id, document_id)
    if document.status != "failed":
        raise CustomError(ErrorCode.PARAM_ERROR)

    client = await get_knowledge_client(tenant_id)
    if client is None:
        raise CustomError(ErrorCode.KNOWLEDGE_NOT_PROVISIONED)

    # 让 OpenViking 重新索引已存在的对象
    result = await client.add_url(to=document.uri, url=document.uri)
    document.status = "processing"
    document.task_id = (result or {}).get("task_id")
    document.error = None
    await document.save()
    return document.json


async def search_preview(
    tenant_id: str, query: str, *, space_key: str | None = None, top_k: int = 5
) -> list[dict]:
    """管理员的检索预览：范围是本租户的全部空间（或指定的一个）。"""
    client = await get_knowledge_client(tenant_id)
    if client is None:
        raise CustomError(ErrorCode.KNOWLEDGE_NOT_PROVISIONED)

    spaces = await KnowledgeSpace.filter(tenant_id=tenant_id, deleted=False).all()
    if space_key:
        spaces = [s for s in spaces if s.space_key == space_key]
    roots = [s.root_uri for s in spaces]
    if not roots:
        return []

    result = await client.find(query=query, target_uri=roots, limit=top_k)
    return await _decorate_hits(tenant_id, result)


async def _decorate_hits(tenant_id: str, raw: dict) -> list[dict]:
    """把 OpenViking 的命中结果换成对外形状：URI 换成 document_id 与标题。

    URI 不出现在任何对外响应里（SPEC §6）。
    """
    contexts = (raw or {}).get("contexts") or (raw or {}).get("results") or []
    hits = []
    for item in contexts:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or item.get("target_uri") or "")
        document = await KnowledgeDocument.get_or_none(
            tenant_id=tenant_id, uri=uri, deleted=False
        )
        hits.append(
            {
                "document_id": str(document.id) if document else None,
                "title": document.title if document else uri.rsplit("/", 1)[-1],
                "space_key": document.space_key if document else "",
                "excerpt": str(item.get("content") or item.get("text") or "")[:500],
                "score": item.get("score"),
            }
        )
    return hits


async def health(tenant_id: str) -> dict:
    try:
        client = await ensure_knowledge_client(tenant_id)
    except Exception:
        return {"available": False, "reason": "service_unavailable"}
    if client is None:
        return {"available": False, "reason": "not_provisioned"}
    try:
        await client.health()
        return {"available": True}
    except CustomError:
        return {"available": False, "reason": "service_unavailable"}


async def sync_space(tenant_id: str, space_key: str) -> dict:
    """与 OpenViking 对账，修复元数据漂移（SPEC §10.6）。

    正常路径下 lion_mgmt 是唯一写入口不会漂移，本接口用于
    运维直接操作过存储、迁移遗留、或写成功但本地事务回滚的情况。
    """
    space = await get_space_or_404(tenant_id, space_key)
    client = await get_knowledge_client(tenant_id)
    if client is None:
        raise CustomError(ErrorCode.KNOWLEDGE_NOT_PROVISIONED)

    listing = await client.ls(space.root_uri, recursive=True)
    remote_entries = {}
    for entry in (listing or {}).get("entries") or (listing or {}).get("items") or []:
        if not isinstance(entry, dict):
            continue
        uri = str(entry.get("uri") or "")
        is_dir = entry.get("is_dir")
        if is_dir is None:
            is_dir = entry.get("type") == "dir"
        if uri and not bool(is_dir):
            remote_entries[uri] = entry

    local = await KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    ).all()
    local_by_uri = {doc.uri: doc for doc in local}

    added = orphaned = updated = 0

    for uri, entry in remote_entries.items():
        if uri in local_by_uri:
            remote_size = int(entry.get("size") or 0)
            document = local_by_uri[uri]
            if remote_size and remote_size != int(document.size or 0):
                document.size = remote_size
                await document.save()
                updated += 1
            continue
        if not uri_in_space(uri, space.root_uri):
            continue
        rel = uri[len(space.root_uri) + 1 :]
        await KnowledgeDocument.create(
            tenant_id=tenant_id,
            space=space,
            space_key=space_key,
            rel_path=rel,
            uri=uri,
            title=rel.rsplit("/", 1)[-1],
            size=int(entry.get("size") or 0),
            content_hash="",
            source="external",
            status="ready",
            indexed_at=datetime.now(TZ),
        )
        added += 1

    for uri, document in local_by_uri.items():
        if uri in remote_entries:
            continue
        document.status = "failed"
        document.error = "文件在存储中不存在"
        await document.save()
        orphaned += 1

    return {"added": added, "orphaned": orphaned, "updated": updated}
