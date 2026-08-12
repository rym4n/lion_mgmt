"""Phase C keeps migration rebuilding outside the runtime-model cleanup."""

from pathlib import Path

from models import network_topology_v2


ROOT = Path(__file__).resolve().parents[2]


def test_phase_c_does_not_remove_existing_migration_history() -> None:
    migration_root = ROOT / "migrations" / "models"
    assert migration_root.is_dir()
    assert any(migration_root.glob("92_*_network_topology_v2_additive.py"))


def test_runtime_model_surface_no_longer_exports_retired_tables() -> None:
    retired = {
        "TopologyBackfillReport",
        "TopologyFeatureRollout",
        "TopologyMigrationState",
        "TopologyOutboxEvent",
        "TopologyServiceBinding",
        "TopologyServiceInstance",
        "TopologyWorkerInstance",
        "TopologyWorkloadIdentity",
    }
    assert retired.isdisjoint(network_topology_v2.__all__)


def test_migration_92_forward_sql_is_repeatable() -> None:
    migration = next((ROOT / "migrations" / "models").glob(
        "92_*_network_topology_v2_additive.py"
    )).read_text()
    assert "CREATE TABLE IF NOT EXISTS" in migration
    assert "ADD COLUMN IF NOT EXISTS" in migration
    assert "INDEX IF NOT EXISTS" in migration
    assert "IF NOT EXISTS (" in migration
