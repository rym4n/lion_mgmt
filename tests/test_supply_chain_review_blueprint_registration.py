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
    assert "/network-topology/list" not in _route_paths(app)
    assert "/system/platform" in _route_paths(app)


def test_asset_management_license_key_registers_asset_blueprint(monkeypatch):
    app = Sanic("asset_management_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {"资产管理": {"expireTime": 4102444800000}},
            "moduleList": [
                {
                    "name": "资产模型",
                    "module": "asset_management_bp",
                    "auth": True,
                    "group": "资产管理",
                }
            ],
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/supply-chain-review/ci-type/list" in paths
    assert "/supply-chain-review/ci/list" in paths
    assert "/supply-chain-review/personnel-authorization/list" not in paths
    assert "/supply-chain-review/preset-policy/list" not in paths


def test_network_topology_license_key_registers_legacy_topology_only(monkeypatch):
    app = Sanic("network_topology_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {"网络管理": {"expireTime": 4102444800000}},
            "moduleList": [
                {
                    "name": "网络拓扑",
                    "module": "topology_bp",
                    "auth": True,
                    "group": "网络管理",
                }
            ],
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/network-topology/list" in paths
    assert "/supply-chain-review/network-topology/list" not in paths
    assert "/supply-chain-review/preset-policy/list" not in paths


def test_network_access_control_license_key_registers_policy_routes_only(monkeypatch):
    app = Sanic("network_access_control_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {"网络管理": {"expireTime": 4102444800000}},
            "moduleList": [
                {
                    "name": "访问控制",
                    "module": "network_access_control_bp",
                    "auth": True,
                    "group": "网络管理",
                },
            ],
        },
    )

    app_factory.register_blueprints(app)
    paths = _route_paths(app)

    assert "/supply-chain-review/preset-policy/list" in paths
    assert "/supply-chain-review/actual-policy/list" in paths
    assert "/supply-chain-review/policy-change/external-callback" in paths
    assert "/supply-chain-review/collection-task/list" not in paths
    assert "/supply-chain-review/disposal-task/confirm" not in paths


def test_asset_and_network_keys_do_not_duplicate_shared_blueprints(monkeypatch):
    app = Sanic("asset_network_deduplicate_license")
    monkeypatch.setattr(
        app_factory,
        "load_license",
        lambda: {
            "grouplist": {
                "资产管理": {"expireTime": 4102444800000},
                "网络管理": {"expireTime": 4102444800000},
            },
            "moduleList": [
                {
                    "name": "资产模型",
                    "module": "asset_management_bp",
                    "auth": True,
                    "group": "资产管理",
                },
                {
                    "name": "资产目录",
                    "module": "asset_management_bp",
                    "auth": True,
                    "group": "资产管理",
                },
                {
                    "name": "访问控制",
                    "module": "network_access_control_bp",
                    "auth": True,
                    "group": "网络管理",
                },
            ],
        },
    )

    app_factory.register_blueprints(app)

    assert len([route for route in app.router.routes if route.path.lstrip("/") == "supply-chain-review/ci-type/list"]) == 1
