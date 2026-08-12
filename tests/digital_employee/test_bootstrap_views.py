# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

import pytest
from sanic import Sanic

from services import app_factory
from services.digital_employee import package_api
from services.system_config import views as system_views


def _route_paths(app: Sanic) -> set[str]:
    return {f"/{route.path.lstrip('/')}" for route in app.router.routes}


def _request(ip="127.0.0.1", form=None):
    return SimpleNamespace(
        ip=ip,
        files={"file": SimpleNamespace(body=b"builtin-bundle")},
        body=b"",
        form=form or {},
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="account-1"),
            }
        ),
    )


def test_bootstrap_route_is_registered_without_license_but_business_routes_are_not(
    monkeypatch,
):
    app = Sanic("digital_employee_bootstrap_unlicensed")
    monkeypatch.setattr(app_factory, "load_license", lambda: None)

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/system/bootstrap/digital-employee/package/import" in paths
    assert "/digital_employee/package/import" not in paths
    assert "/digital_employee/employee/list" not in paths


def test_business_routes_remain_registered_with_active_license(monkeypatch):
    app = Sanic("digital_employee_bootstrap_licensed")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "moduleList": [
                {
                    "module": "digital_employee_bp",
                    "auth": True,
                    "expireTime": 4102444800000,
                }
            ]
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/system/bootstrap/digital-employee/package/import" in paths
    assert "/digital_employee/package/import" in paths
    assert "/digital_employee/employee/list" in paths


def test_bootstrap_route_rejects_non_loopback_source():
    with pytest.raises(system_views.CustomError) as exc_info:
        asyncio.run(
            inspect.unwrap(system_views.bootstrap_digital_employee_package)(
                _request(ip="10.10.114.16")
            )
        )

    assert exc_info.value.enum is system_views.ErrorCode.IP_FORBIDDEN


@pytest.mark.parametrize("loopback_ip", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_bootstrap_route_forces_builtin_and_delegates_import(monkeypatch, loopback_ip):
    calls = []

    async def fake_check_permissions(request, permission):
        calls.append(("permission", request, permission))

    async def fake_import_package(
        file_bytes, tenant_id, *, is_builtin, operator_id, request
    ):
        calls.append(
            ("import", file_bytes, tenant_id, is_builtin, operator_id, request)
        )
        return SimpleNamespace(
            json={"package_type": "bundle", "imported": [], "skipped": []}
        )

    monkeypatch.setattr(
        package_api, "_package_type_or_error", lambda file_bytes: "bundle"
    )
    monkeypatch.setattr(package_api, "check_permissions", fake_check_permissions)
    monkeypatch.setattr(package_api, "import_package", fake_import_package)
    monkeypatch.setattr(
        system_views, "response_ok", lambda data: {"code": 0, "data": data}
    )
    request = _request(ip=loopback_ip, form={"is_builtin": "false"})

    result = asyncio.run(
        inspect.unwrap(system_views.bootstrap_digital_employee_package)(request)
    )

    assert calls == [
        ("permission", request, "digital_employee:c"),
        ("import", b"builtin-bundle", "tenant-1", True, "account-1", request),
    ]
    assert result == {
        "code": 0,
        "data": {"package_type": "bundle", "imported": [], "skipped": []},
    }
