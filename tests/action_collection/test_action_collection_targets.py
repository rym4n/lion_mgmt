# tests/action_collection/test_action_collection_targets.py
from types import SimpleNamespace

import pytest

from services.action_collection.errors import CollectionError
from services.action_collection.targets import freeze_targets


class Repo:
    def __init__(self, resources):
        self.resources = resources

    async def resource(self, tenant_id, app_name, resource_id):
        return self.resources.get(resource_id)

    async def latest_tool(self, tenant_id, app_name):
        return ("v2", "main.py")

    async def tool(self, tenant_id, app_name, version):
        return (version, "main.py") if version == "v1" else None

    async def actions(self, tenant_id, app_name, version):
        return [{"func": "collect_policy_observations", "name": "采集策略"}]


def item(resources, **kw):
    values = dict(id="i1", app_name="huawei_usg", app_version=None, action_func="collect_policy_observations",
                  action_input={"x": 1}, resources=resources)
    values.update(kw)
    return SimpleNamespace(**values)


PLAN = SimpleNamespace(tenant_id="t1")
RESOURCES = {
    "r1": {"id": "r1", "name": "FW-1", "resources_json": {"host": {"value": "10.0.0.1"}}},
    "r2": {"id": "r2", "name": "FW-2", "resources_json": {"ip": {"value": "10.0.0.2"}}},
}


@pytest.mark.asyncio
async def test_device_asset_binding_flows_into_target():
    frozen = await freeze_targets(PLAN, [item([{"resource_id": "r1", "device_asset_id": "asset-1"},
                                               {"resource_id": "r2"}])], repository=Repo(RESOURCES))
    assert [(t.resource_id, t.host, t.asset_instance_id, t.app_version, t.action_name) for t in frozen] == [
        ("r1", "10.0.0.1", "asset-1", "v2", "采集策略"),
        ("r2", "10.0.0.2", None, "v2", "采集策略"),
    ]


@pytest.mark.asyncio
async def test_legacy_asset_instance_key_is_ignored():
    frozen = await freeze_targets(PLAN, [item([{"resource_id": "r1", "asset_instance_id": "legacy"}])],
                                  repository=Repo(RESOURCES))
    assert frozen[0].asset_instance_id is None


@pytest.mark.asyncio
async def test_action_input_is_copied_per_target():
    source = item([{"resource_id": "r1"}, {"resource_id": "r2"}])
    frozen = await freeze_targets(PLAN, [source], repository=Repo(RESOURCES))
    frozen[0].action_input["x"] = 2
    assert source.action_input == {"x": 1} and frozen[1].action_input == {"x": 1}


@pytest.mark.asyncio
async def test_missing_resource_and_version_fail_closed():
    with pytest.raises(CollectionError) as exc:
        await freeze_targets(PLAN, [item([{"resource_id": "gone", "resource_name": "旧防火墙"}])],
                             repository=Repo(RESOURCES))
    assert (exc.value.code, exc.value.status, exc.value.message) == ("RESOURCE_NOT_FOUND", 409, "旧防火墙")
    with pytest.raises(CollectionError) as exc:
        await freeze_targets(PLAN, [item([{"resource_id": "r1"}], app_version="v9")], repository=Repo(RESOURCES))
    assert exc.value.code == "COLLECTION_ACTION_NOT_FOUND"


def test_topology_targets_module_reexports_core():
    from services.action_collection import targets as core
    from services.network_topology.collection import targets as topology
    assert topology.Target is core.Target and topology.freeze_targets is core.freeze_targets


@pytest.mark.asyncio
async def test_missing_application_and_action_fail_closed():
    class MissingTool(Repo):
        async def latest_tool(self, tenant_id, app_name):
            return None

    class MissingAction(Repo):
        async def actions(self, tenant_id, app_name, version):
            return [{"func": "other_action"}]

    for repo in (MissingTool(RESOURCES), MissingAction(RESOURCES)):
        with pytest.raises(CollectionError) as exc:
            await freeze_targets(PLAN, [item([{"resource_id": "r1"}])], repository=repo)
        assert exc.value.code == "COLLECTION_ACTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_explicit_version_fallback_name_and_nested_inputs():
    class UnnamedAction(Repo):
        async def actions(self, tenant_id, app_name, version):
            return [{"func": "collect_policy_observations"}]

    source = item([{"resource_id": "r1"}, {"resource_id": "r2"}],
                  app_version="v1", action_input={"nested": {"values": [1]}})
    frozen = await freeze_targets(PLAN, [source], repository=UnnamedAction(RESOURCES))
    assert frozen[0].app_version == "v1"
    assert frozen[0].action_name == "collect_policy_observations"
    frozen[0].action_input["nested"]["values"].append(2)
    assert source.action_input == {"nested": {"values": [1]}}
    assert frozen[1].action_input == {"nested": {"values": [1]}}


@pytest.mark.asyncio
async def test_missing_resource_falls_back_to_id():
    with pytest.raises(CollectionError) as exc:
        await freeze_targets(PLAN, [item([{"resource_id": "gone"}])], repository=Repo(RESOURCES))
    assert (exc.value.code, exc.value.status, exc.value.message) == ("RESOURCE_NOT_FOUND", 409, "gone")
