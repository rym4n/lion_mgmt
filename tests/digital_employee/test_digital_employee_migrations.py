# coding=utf-8
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TIME_COLUMNS = (
    ("digital_employee_role", "ctime"),
    ("digital_employee_role", "utime"),
    ("digital_employee_skill", "ctime"),
    ("digital_employee_skill", "utime"),
    ("tenant_cognee_credential", "ctime"),
    ("tenant_cognee_credential", "utime"),
    ("tenant_cognee_credential", "cognee_jwt_expires_at"),
    ("command_center_agent", "workspace_synced_at"),
    ("digital_employee_report", "ctime"),
    ("digital_employee_report", "utime"),
)


def test_initial_digital_employee_migration_uses_timestamptz() -> None:
    sql = (ROOT / "migrations/models/40_20260624_add_digital_employee.py").read_text(encoding="utf-8")

    assert not re.search(r'"(?:ctime|utime|cognee_jwt_expires_at|workspace_synced_at)" TIMESTAMP(?!TZ)\b', sql)
    assert sql.count("TIMESTAMPTZ") >= len(TIME_COLUMNS)


def test_followup_migration_repairs_existing_timestamp_columns() -> None:
    sql = (ROOT / "migrations/models/42_20260625193000_fix_digital_employee_timestamptz.py").read_text(
        encoding="utf-8"
    )

    for table_name, column_name in TIME_COLUMNS:
        assert f"ARRAY['{table_name}', '{column_name}']" in sql
    assert "data_type = 'timestamp without time zone'" in sql
    assert "TYPE TIMESTAMPTZ" in sql
