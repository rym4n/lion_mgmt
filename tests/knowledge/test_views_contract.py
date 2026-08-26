# coding=utf-8
"""管理面路由与权限契约（SPEC §10.1、§10.8）。"""

from pathlib import Path

from sanic.request.parameters import RequestParameters

VIEWS = Path(__file__).resolve().parents[2] / "services" / "knowledge" / "views.py"


def _src() -> str:
    return VIEWS.read_text(encoding="utf-8")


def test_blueprint_name_and_prefix():
    src = _src()
    assert 'Blueprint("knowledge"' in src
    assert 'url_prefix="/knowledge"' in src


def test_all_management_routes_declared():
    src = _src()
    for route in [
        "/space/list", "/space/create", "/space/update", "/space/delete",
        "/document/list", "/document/upload", "/document/upsert",
        "/document/detail", "/document/delete", "/document/retry", "/search", "/health",
    ]:
        assert f'"{route}"' in src, f"缺少路由 {route}"


def test_write_routes_require_write_permission():
    src = _src()
    assert src.count('@requires_permission("knowledge:c")') >= 4
    assert src.count('@requires_permission("knowledge:d")') >= 2
    assert src.count('@requires_permission("knowledge:r")') >= 5


def test_tenant_id_never_from_request_body():
    src = _src()
    assert 'session.get("tenant_id")' in src
    assert 'data.get("tenant_id")' not in src
    # 仅内部 lion_agent 回调读取 tenant_id；用户管理路由仍只信 session。
    assert 'tenant_id = params.get("tenant_id")' in src


def test_document_delete_does_not_expose_recursive():
    src = _src()
    assert "recursive" not in src


def test_space_create_only_requires_user_visible_fields():
    src = _src()
    create_route = src[src.index("async def space_create"):src.index("async def space_update")]

    assert '_require(params, "name")' in create_route
    assert 'params["space_key"]' not in create_route
    assert "await manager.create_space(" in create_route


def test_document_upload_keeps_complete_form_values():
    form = RequestParameters()
    form["space_key"] = ["test"]
    form["title"] = ["完整标题"]

    assert form.get("space_key") == "test"
    assert form.get("title") == "完整标题"

    src = _src()
    assert 'space_key = form.get("space_key")' in src
    assert 'title=form.get("title")' in src
    assert '(form.get("space_key") or [None])[0]' not in src
    assert '(form.get("title") or [None])[0]' not in src


def test_document_retry_passes_current_operator():
    src = _src()
    retry_route = src[
        src.index("async def document_retry"):src.index("async def search_preview")
    ]

    assert "updater_id=_operator_id(request)" in retry_route
