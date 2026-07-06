# coding=utf-8

from sanic import Sanic

from services import app_factory


def _route_paths(app: Sanic) -> set[str]:
    return {f"/{route.path.lstrip('/')}" for route in app.router.routes}


def test_supply_chain_review_is_not_registered_without_license(monkeypatch):
    app = Sanic("supply_chain_review_unlicensed")
    monkeypatch.setattr(app_factory, "load_license", lambda: {"moduleList": []})

    app_factory.register_blueprints(app)

    assert "/supply-chain-review/ci-type/list" not in _route_paths(app)
    assert "/system/platform" in _route_paths(app)


def test_supply_chain_review_legacy_license_key_registers_domain_blueprints(monkeypatch):
    app = Sanic("supply_chain_review_legacy_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {"安全管理": {"expireTime": 4102444800000}},
            "moduleList": [
                {
                    "name": "安全管理",
                    "module": "supply_chain_review_bp",
                    "auth": True,
                    "group": "安全管理",
                }
            ],
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/supply-chain-review/ci-type/list" in paths
    assert "/supply-chain-review/personnel-authorization/list" in paths
    assert "/supply-chain-review/collection-task/list" in paths
    assert "/supply-chain-review/disposal-task/confirm" in paths
    assert "/supply-chain-review/attachment-ref/upload" in paths


def test_supply_chain_review_domain_license_key_registers_only_that_domain(monkeypatch):
    app = Sanic("supply_chain_review_domain_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "moduleList": [
                {
                    "name": "安全管理CMDB",
                    "module": "supply_chain_review_cmdb_bp",
                    "auth": True,
                    "expireTime": 4102444800000,
                }
            ],
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/supply-chain-review/ci-type/list" in paths
    assert "/supply-chain-review/personnel-authorization/list" not in paths


def test_supply_chain_review_aggregate_and_domain_keys_do_not_duplicate_blueprints(monkeypatch):
    app = Sanic("supply_chain_review_deduplicate_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {"安全管理": {"expireTime": 4102444800000}},
            "moduleList": [
                {
                    "name": "安全管理",
                    "module": "supply_chain_review_bp",
                    "auth": True,
                    "group": "安全管理",
                },
                {
                    "name": "安全管理CMDB",
                    "module": "supply_chain_review_cmdb_bp",
                    "auth": True,
                    "expireTime": 4102444800000,
                },
            ],
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/supply-chain-review/ci-type/list" in paths
    assert len([route for route in app.router.routes if route.path.lstrip("/") == "supply-chain-review/ci-type/list"]) == 1
