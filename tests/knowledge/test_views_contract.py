# coding=utf-8
"""管理面路由与权限契约（SPEC §10.1、§10.8）。"""

from pathlib import Path

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
    assert 'params.get("tenant_id")' not in src


def test_document_delete_does_not_expose_recursive():
    src = _src()
    assert "recursive" not in src
