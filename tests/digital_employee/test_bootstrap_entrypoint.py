# coding=utf-8
from pathlib import Path


def test_entrypoint_invokes_bootstrap_by_import_for_nuitka_runtime():
    root = Path(__file__).resolve().parents[2]
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "python -m services.digital_employee.bootstrap" not in entrypoint
    assert "from services.digital_employee.bootstrap import main" in entrypoint
