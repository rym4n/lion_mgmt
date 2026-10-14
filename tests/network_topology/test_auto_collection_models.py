import pytest
from models.network_topology_v2 import (
    RUN_TERMINAL_STATES,
    TopologyCollectionItem,
    TopologyCollectionPlan,
    TopologyCollectionRun,
)
import models.network_topology_v2 as public_v2


def test_tables_and_defaults():
    assert TopologyCollectionPlan._meta.db_table == "network_topology_collection_plan"
    assert TopologyCollectionItem._meta.db_table == "network_topology_collection_item"
    assert TopologyCollectionRun._meta.db_table == "network_topology_collection_run"
    plan = TopologyCollectionPlan._meta.fields_map
    assert plan["run_timeout_seconds"].default == 3600
    assert TopologyCollectionRun._meta.fields_map["state"].default == "PENDING"
    assert RUN_TERMINAL_STATES == {"SUCCEEDED", "FAILED", "CANCELLED"}


def test_public_facade_exports_auto_collection_surface():
    assert {
        "RUN_STATES",
        "RUN_TERMINAL_STATES",
        "TopologyCollectionItem",
        "TopologyCollectionPlan",
        "TopologyCollectionRun",
    } <= set(public_v2.__all__)


@pytest.mark.asyncio
async def test_migration_132_downgrade_executes_and_drops_in_dependency_order():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "migrations/models/132_20261014160000_topology_auto_collection.py"
    spec = importlib.util.spec_from_file_location("migration132", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = await module.downgrade(None)
    assert sql.splitlines() == [
        'DROP TABLE IF EXISTS "network_topology_collection_run";',
        'DROP TABLE IF EXISTS "network_topology_collection_item";',
        'DROP TABLE IF EXISTS "network_topology_collection_plan";',
    ]
