# coding=utf-8
import subprocess
import sys
from pathlib import Path


def test_entrypoint_invokes_bootstrap_by_import_for_nuitka_runtime():
    root = Path(__file__).resolve().parents[2]
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "python -m services.digital_employee.bootstrap" not in entrypoint
    assert "from services.digital_employee.bootstrap import main" in entrypoint


def test_bootstrap_import_does_not_load_full_app_factory():
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; "
        "from services.digital_employee.bootstrap import main; "
        "assert 'services.app_factory' not in sys.modules; "
        "print(main.__name__)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == "main"
