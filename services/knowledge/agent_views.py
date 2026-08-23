# coding=utf-8
"""知识库 Agent 面 API（给数字员工用）。

与管理面 API 的根本区别：范围由服务端按员工身份注入，请求参数只能收窄不能放大。
员工只能通过 document_id 接触文档，OpenViking URI 不直接暴露给调用方。
"""

from datetime import datetime

from sanic import Blueprint, Request
from sanic.log import logger

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent
from models.digital_employee import EmployeeApiCredential
from services.knowledge import scope
from services.knowledge.client import get_knowledge_client
from services.knowledge.manager import (
    _decorate_hits,
    delete_document,
    get_document_or_404,
    upsert_document,
)
from settings import TZ


knowledge_agent_bp = Blueprint("knowledge_agent", url_prefix="/knowledge/agent")

EXCERPT_LIMIT = 500
DEFAULT_TOP_K = 5
MAX_TOP_K = 20


async def resolve_employee(request: Request) -> CommandAgent:
    """从请求身份反查当前数字员工。"""
    account = getattr(request.ctx, "session", {}).get("account_info")
    account_id = (
        account.get("id") if isinstance(account, dict) else getattr(account, "id", None)
    )
    if not account_id:
        raise CustomError(ErrorCode.FORBIDDEN)

    credential = await EmployeeApiCredential.get_or_none(
        account_id=str(account_id), deleted=False
    )
    if not credential:
        raise CustomError(ErrorCode.FORBIDDEN)

    employee = await CommandAgent.get_or_none(
        id=credential.employee_id, deleted=False
    )
    if not employee:
        raise CustomError(ErrorCode.FORBIDDEN)
    return employee


def _space_keys(employee) -> list[str]:
    return list(getattr(employee, "knowledge_space_keys", None) or [])


def _write_space(employee) -> str:
    write_space = getattr(employee, "knowledge_write_space", None)
    if not write_space or write_space not in _space_keys(employee):
        raise CustomError(ErrorCode.KNOWLEDGE_WRITE_NOT_ALLOWED)
    return str(write_space)


async def do_search(
    employee, query: str, *, space_key: str | None = None, top_k: int = DEFAULT_TOP_K
) -> list[dict]:
    """检索范围恒由员工可见空间决定；space_key 只能收窄。"""
    roots = await scope.resolve_search_roots(
        employee.tenant_id, _space_keys(employee), space_key
    )
    if not roots:
        # 未绑定空间或指定不可见空间时静默降级，且不触发存储检索。
        return []

    client = await get_knowledge_client(employee.tenant_id)
    if client is None:
        return []

    try:
        limit = max(1, min(MAX_TOP_K, int(top_k or DEFAULT_TOP_K)))
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR) from None
    raw = await client.find(query=query, target_uri=roots, limit=limit)
    hits = await _decorate_hits(employee.tenant_id, raw)
    for hit in hits:
        hit["excerpt"] = str(hit.get("excerpt") or "")[:EXCERPT_LIMIT]
    return hits


async def _assert_visible(employee, document) -> None:
    if document.space_key not in _space_keys(employee):
        # 404 不确认 document_id 对应的不可见文档是否存在。
        raise CustomError(ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND)


async def do_read(employee, document_id: str) -> dict:
    document = await get_document_or_404(employee.tenant_id, document_id)
    await _assert_visible(employee, document)

    client = await get_knowledge_client(employee.tenant_id)
    content = ""
    if client is not None:
        try:
            content = await client.read(document.uri)
        except CustomError:
            content = ""
    return {
        "document_id": str(document.id),
        "title": document.title,
        "space_key": document.space_key,
        "content": content,
    }


async def do_list(employee, space_key: str | None = None) -> list[dict]:
    from models.knowledge import KnowledgeDocument

    keys = _space_keys(employee)
    if space_key:
        keys = [key for key in keys if key == space_key]
    if not keys:
        return []

    documents = await KnowledgeDocument.filter(
        tenant_id=employee.tenant_id,
        space_key__in=keys,
        deleted=False,
        status="ready",
    ).limit(200).all()
    return [
        {
            "document_id": str(document.id),
            "title": document.title,
            "space_key": document.space_key,
            "updated_at": document.utime,
        }
        for document in documents
    ]


async def do_write(employee, *, title: str, content: str, path: str | None = None) -> dict:
    """写入员工唯一配置的可写空间。

    函数签名刻意不提供 space/space_key，空间选择是服务端配置而非请求参数。
    """
    write_space = _write_space(employee)
    if not content:
        raise CustomError(ErrorCode.PARAM_ERROR)

    rel_path = path or f"agent/{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.md"
    if not rel_path.endswith(".md"):
        rel_path = f"{rel_path}.md"

    result = await upsert_document(
        employee.tenant_id,
        space_key=write_space,
        rel_path=rel_path,
        content=content.encode("utf-8"),
        title=title or rel_path.rsplit("/", 1)[-1],
        source="agent",
        source_ref=str(employee.id),
    )
    logger.info(
        "knowledge_agent_write tenant=%s employee=%s space=%s",
        employee.tenant_id,
        employee.id,
        write_space,
    )
    return {"document_id": result.get("id"), "status": result.get("status")}


async def do_delete(employee, document_id: str) -> dict:
    """只能删除员工配置的可写空间里的文档。"""
    write_space = _write_space(employee)
    document = await get_document_or_404(employee.tenant_id, document_id)
    if document.space_key != write_space:
        raise CustomError(ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND)
    return await delete_document(employee.tenant_id, document_id)


def _json(request: Request) -> dict:
    return request.json or {}


@knowledge_agent_bp.post("/search")
async def agent_search(request: Request):
    employee = await resolve_employee(request)
    params = _json(request)
    if not params.get("query"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(
        await do_search(
            employee,
            str(params["query"]),
            space_key=params.get("space_key"),
            top_k=int(params.get("top_k", DEFAULT_TOP_K)),
        )
    )


@knowledge_agent_bp.post("/read")
async def agent_read(request: Request):
    employee = await resolve_employee(request)
    params = _json(request)
    if not params.get("document_id"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(await do_read(employee, str(params["document_id"])))


@knowledge_agent_bp.post("/list")
async def agent_list(request: Request):
    employee = await resolve_employee(request)
    params = _json(request)
    return response_ok(await do_list(employee, params.get("space_key")))


@knowledge_agent_bp.post("/write")
async def agent_write(request: Request):
    employee = await resolve_employee(request)
    params = _json(request)
    if not params.get("content"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(
        await do_write(
            employee,
            title=str(params.get("title") or ""),
            content=str(params["content"]),
            path=params.get("path"),
        )
    )


@knowledge_agent_bp.post("/delete")
async def agent_delete(request: Request):
    employee = await resolve_employee(request)
    params = _json(request)
    if not params.get("document_id"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(await do_delete(employee, str(params["document_id"])))
