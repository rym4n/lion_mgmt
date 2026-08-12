# coding=utf-8

from sanic import Sanic

from services import app_factory


def _route_paths(app: Sanic) -> set[str]:
    return {f"/{route.path.lstrip('/')}" for route in app.router.routes}


def test_asset_and_network_blueprints_are_not_registered_without_license(monkeypatch):
    app = Sanic("asset_network_unlicensed")
    monkeypatch.setattr(app_factory, "load_license", lambda: {"moduleList": []})

    app_factory.register_blueprints(app)

    assert "/asset-management/asset-model/list" not in _route_paths(app)
    assert "/network-access-control/actual-policy/list" not in _route_paths(app)
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

    assert "/asset-management/asset-model/list" in paths
    assert "/asset-management/asset/list" in paths
    assert "/network-access-control/personnel-authorization/list" not in paths
    assert "/network-access-control/preset-policy/list" not in paths


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
    assert "/network-access-control/network-topology/list" not in paths
    assert "/network-access-control/preset-policy/list" not in paths


def test_network_access_control_license_key_registers_network_access_blueprint(
    monkeypatch,
):
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

    assert "/network-access-control/actual-policy/list" in paths
    assert "/network-access-control/actual-policy/detail" in paths
    assert "/network-access-control/actual-policy/save" in paths
    assert "/network-access-control/actual-policy/delete" in paths
    assert "/network-access-control/actual-policy/sync" in paths
    assert "/network-access-control/policy-change/list" in paths
    assert "/network-access-control/policy-change/detail" in paths
    assert "/network-access-control/policy-change/save" not in paths
    assert "/network-access-control/policy-change/delete" not in paths
    assert "/network-access-control/preset-policy/list" in paths
    assert "/network-access-control/policy-compare/run" in paths
    assert "/network-access-control/policy-change/external-callback" in paths
    assert "/network-access-control/collection-task/list" in paths
    assert "/network-access-control/disposal-task/confirm" in paths
    assert "/asset-management/asset-model/list" not in paths


def test_duplicate_asset_license_entries_register_each_blueprint_once(monkeypatch):
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
                    "name": "资产数据",
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

    assert (
        len(
            [
                route
                for route in app.router.routes
                if route.path.lstrip("/") == "asset-management/asset-model/list"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                route
                for route in app.router.routes
                if route.path.lstrip("/") == "network-access-control/actual-policy/list"
            ]
        )
        == 1
    )
