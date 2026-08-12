import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_CHECK = REPO_ROOT / "scripts" / "check_legacy_approvement.sh"
MIGRATION_VALIDATION = REPO_ROOT / "scripts" / "validate_ticket_migration.sh"


def test_gate_scripts_exist_and_are_executable():
    for script_path in (LEGACY_CHECK, MIGRATION_VALIDATION):
        assert script_path.is_file()
        assert os.access(script_path, os.X_OK)


def test_legacy_approvement_check_passes():
    result = subprocess.run(
        [str(LEGACY_CHECK)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        f"Legacy approvement check failed:\n{result.stdout}\n{result.stderr}"
    )


def test_ticket_migration_validation_passes():
    result = subprocess.run(
        [str(MIGRATION_VALIDATION)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, (
        f"Ticket migration validation failed:\n{result.stdout}\n{result.stderr}"
    )
