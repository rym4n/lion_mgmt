from models.network_topology_v2 import (
    RUN_TERMINAL_STATES,
    TopologyCollectionItem,
    TopologyCollectionPlan,
    TopologyCollectionRun,
)


def test_tables_and_defaults():
    assert TopologyCollectionPlan._meta.db_table == "network_topology_collection_plan"
    assert TopologyCollectionItem._meta.db_table == "network_topology_collection_item"
    assert TopologyCollectionRun._meta.db_table == "network_topology_collection_run"
    plan = TopologyCollectionPlan._meta.fields_map
    assert plan["run_timeout_seconds"].default == 3600
    assert TopologyCollectionRun._meta.fields_map["state"].default == "PENDING"
    assert RUN_TERMINAL_STATES == {"SUCCEEDED", "FAILED", "CANCELLED"}
