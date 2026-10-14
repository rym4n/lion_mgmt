"""Phase C inventory for the retained topology v2 business models."""

from pathlib import Path

from models import network_topology_v2
from models import _network_topology_v2_control as control
from models._network_topology_v2_base import TopologyTenantModel


ROOT = Path(__file__).resolve().parents[2]


def test_retired_security_and_infrastructure_model_modules_are_absent() -> None:
    for name in (
        "_network_topology_v2_security.py",
        "_network_topology_v2_infrastructure.py",
        "network_topology_assertion_replay.py",
        "network_topology_service_keys.py",
        "network_topology_service_runs.py",
    ):
        assert not (ROOT / "models" / name).exists()


def test_control_module_keeps_only_business_control_models() -> None:
    assert control.__all__ == ("TopologySyncAlert", "TopologyUpgradeIntent")
    assert not hasattr(control, "TopologyFeatureRollout")
    assert not hasattr(control, "TopologyOutboxEvent")


def test_public_v2_inventory_contains_no_retired_models() -> None:
    retired_fragments = (
        "Backfill",
        "FeatureRollout",
        "Migration",
        "Outbox",
        "Service",
        "Worker",
        "Workload",
    )
    assert not any(
        fragment in name
        for name in network_topology_v2.__all__
        for fragment in retired_fragments
    )


def test_every_public_v2_table_is_tenant_scoped_and_non_nullable() -> None:
    for model_name in network_topology_v2.__all__:
        model = getattr(network_topology_v2, model_name)
        if not isinstance(model, type):
            continue
        assert issubclass(model, TopologyTenantModel)
        assert model._meta.fields_map["tenant_id"].null is False
        assert "installation_id" not in model._meta.fields_map
