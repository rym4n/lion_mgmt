# coding=utf-8
"""知识空间与文档的业务逻辑（SPEC §10）。

lion_mgmt 是知识库的唯一写入口：前端、剧本、数字员工的写入全部经此。
所有 OpenViking 调用都在这一层发起，views 层不直接碰 client。
"""

from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent
from models.knowledge import KnowledgeDocument, KnowledgeSpace
from services.knowledge.client import get_knowledge_client
from services.knowledge.scope import validate_space_key


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
