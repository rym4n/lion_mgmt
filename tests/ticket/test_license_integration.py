# coding=utf-8
import time

from sanic import Sanic

from services import app_factory


def _route_paths(app: Sanic) -> set[str]:
    return {f"/{route.path.lstrip('/')}" for route in app.router.routes}


def test_ticket_blueprint_loaded_with_active_license(monkeypatch):
    app = Sanic("ticket_license_active")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {
                "工单管理": {
                    "expireTime": int(time.time() * 1000) + 60000,
                }
            },
            "moduleList": [
                {
                    "name": "工单列表",
                    "module": "ticket_bp",
                    "auth": True,
                    "group": "工单管理",
                }
            ],
        },
    )

    app_factory.register_blueprints(app)

    paths = _route_paths(app)
    assert any(path.startswith("/ticket/") for path in paths)
    assert "/ws/ticket/pending" in paths


def test_ticket_blueprint_skipped_without_license(monkeypatch):
    app = Sanic("ticket_license_missing")
    warnings = []
    monkeypatch.setattr(app_factory, "load_license", lambda: {"moduleList": []})
    monkeypatch.setattr(app_factory.logger, "warning", warnings.append)

    app_factory.register_blueprints(app)

    paths = _route_paths(app)
    assert not any(path.startswith("/ticket/") for path in paths)
    assert "/ws/ticket/pending" not in paths
    assert warnings == ["ticket 模块未授权，跳过加载"]
