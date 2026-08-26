# coding=utf-8
"""知识库管理面 API（给人用）。"""

from sanic import Blueprint, Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from services.knowledge import manager
from services.digital_employee.views import _check_internal
from services.knowledge.callbacks import apply_ingest_result

knowledge_bp = Blueprint("knowledge", url_prefix="/knowledge")


def _tenant(request: Request) -> str:
    """租户恒取 session，绝不接受请求体传入。"""
    tenant_id = request.ctx.session.get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.FORBIDDEN)
    return str(tenant_id)


def _json(request: Request) -> dict:
    return request.json or {}


def _operator_id(request: Request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("id")
    return getattr(account, "id", None)


def _require(params: dict, *keys: str) -> None:
    for key in keys:
        if not params.get(key):
            raise CustomError(ErrorCode.PARAM_ERROR)


def _page(params: dict) -> tuple[int, int]:
    try:
        page = max(1, int(params.get("page", 1)))
        size = max(1, min(100, int(params.get("size", 20))))
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR) from None
    return page, size


@knowledge_bp.post("/space/list")
@requires_permission("knowledge:r")
async def space_list(request: Request):
    params = _json(request)
    page, size = _page(params)
    return response_ok(await manager.list_spaces(_tenant(request), page, size))


@knowledge_bp.post("/space/create")
@requires_permission("knowledge:c")
async def space_create(request: Request):
    params = _json(request)
    _require(params, "name")
    space = await manager.create_space(
        _tenant(request),
        str(params["name"]),
        description=params.get("description"),
    )
    return response_ok(space.json)


@knowledge_bp.post("/space/update")
@requires_permission("knowledge:u")
async def space_update(request: Request):
    params = _json(request)
    _require(params, "space_key")
    space = await manager.update_space(
        _tenant(request),
        str(params["space_key"]),
        name=params.get("name"),
        description=params.get("description"),
    )
    return response_ok(space.json)


@knowledge_bp.post("/space/delete")
@requires_permission("knowledge:d")
async def space_delete(request: Request):
    params = _json(request)
    _require(params, "space_key")
    return response_ok(
        await manager.delete_space(
            _tenant(request), str(params["space_key"]), dry_run=bool(params.get("dry_run", False))
        )
    )


@knowledge_bp.post("/document/list")
@requires_permission("knowledge:r")
async def document_list(request: Request):
    params = _json(request)
    _require(params, "space_key")
    page, size = _page(params)
    return response_ok(
        await manager.list_documents(
            _tenant(request), str(params["space_key"]), page=page, size=size,
            status=params.get("status"), source=params.get("source"), keyword=params.get("keyword")
        )
    )


@knowledge_bp.post("/document/upload")
@requires_permission("knowledge:c")
async def document_upload(request: Request):
    form = request.form or {}
    files = request.files or {}
    uploaded = files.get("file")
    # Sanic RequestParameters.get() already returns the first scalar value.
    space_key = form.get("space_key")
    if not uploaded or not space_key:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(
        await manager.upsert_document(
            _tenant(request),
            space_key=str(space_key),
            content=uploaded.body, title=form.get("title"),
            filename=uploaded.name,
            source="manual", uploader_id=_operator_id(request)
        )
    )


@knowledge_bp.post("/document/upsert")
@requires_permission("knowledge:c")
async def document_upsert(request: Request):
    params = _json(request)
    _require(params, "space_key", "filename", "content")
    content = params["content"]
    return response_ok(
        await manager.upsert_document(
            _tenant(request),
            space_key=str(params["space_key"]),
            content=content.encode("utf-8") if isinstance(content, str) else content,
            filename=str(params["filename"]),
            title=params.get("title"), source="api",
            uploader_id=_operator_id(request)
        )
    )


@knowledge_bp.post("/document/detail")
@requires_permission("knowledge:r")
async def document_detail(request: Request):
    params = _json(request)
    _require(params, "document_id")
    return response_ok(await manager.document_detail(_tenant(request), str(params["document_id"])))


@knowledge_bp.post("/document/delete")
@requires_permission("knowledge:d")
async def document_delete(request: Request):
    params = _json(request)
    _require(params, "document_id")
    return response_ok(await manager.delete_document(_tenant(request), str(params["document_id"])))


@knowledge_bp.post("/document/retry")
@requires_permission("knowledge:c")
async def document_retry(request: Request):
    params = _json(request)
    _require(params, "document_id")
    return response_ok(
        await manager.retry_document(
            _tenant(request),
            str(params["document_id"]),
            updater_id=_operator_id(request),
        )
    )


@knowledge_bp.post("/search")
@requires_permission("knowledge:r")
async def search_preview(request: Request):
    params = _json(request)
    _require(params, "query")
    try:
        top_k = int(params.get("top_k", 5))
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR) from None
    return response_ok(await manager.search_preview(
        _tenant(request), str(params["query"]), space_key=params.get("space_key"), top_k=top_k
    ))


@knowledge_bp.post("/health")
@requires_permission("knowledge:r")
async def health(request: Request):
    return response_ok(await manager.health(_tenant(request)))


@knowledge_bp.post("/internal/documents/<document_id>/status")
async def internal_document_status(request: Request, document_id: str):
    """lion_agent 入库完成后的回调。内部 token 鉴权，不走用户 session。"""
    _check_internal(request)
    params = _json(request)
    tenant_id = params.get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(
        await apply_ingest_result(str(tenant_id), str(document_id), params)
    )
