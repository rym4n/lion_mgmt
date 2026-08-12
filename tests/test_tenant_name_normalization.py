# coding=utf-8
import inspect

from core.error_code import ErrorCode
from services.tenant import views as tenant_views


def test_normalize_tenant_name_trims_and_rejects_non_strings():
    assert tenant_views._normalize_tenant_name("  测试  ") == "测试"
    assert tenant_views._normalize_tenant_name("   ") == ""
    assert tenant_views._normalize_tenant_name(None) == ""


def test_tenant_name_endpoints_use_normalized_uniqueness_contract():
    add_source = inspect.getsource(inspect.unwrap(tenant_views.add_tenant))
    edit_source = inspect.getsource(inspect.unwrap(tenant_views.edit_tenant))

    assert "name = _normalize_tenant_name(req.get(\"name\"))" in add_source
    assert "raise CustomError(ErrorCode.TENANT_NAME_INVALID)" in add_source
    assert "raise CustomError(ErrorCode.TENANT_ALREADY_EXISTS)" in add_source
    assert 'if "name" in req:' in edit_source
    assert ".exclude(id=tenant_id).exists()" in edit_source
    assert "if tenant.name != name:" in edit_source


def test_tenant_name_invalid_has_a_specific_business_error():
    assert ErrorCode.TENANT_NAME_INVALID.code == 4184
