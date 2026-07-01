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
    role_table_sql = sql.split('CREATE TABLE IF NOT EXISTS "digital_employee_skill"', 1)[0]
    assert '"description" TEXT' not in role_table_sql


def test_followup_migration_repairs_existing_timestamp_columns() -> None:
    sql = (ROOT / "migrations/models/42_20260625193000_fix_digital_employee_timestamptz.py").read_text(
        encoding="utf-8"
    )

    for table_name, column_name in TIME_COLUMNS:
        assert f"ARRAY['{table_name}', '{column_name}']" in sql
    assert "data_type = 'timestamp without time zone'" in sql
    assert "TYPE TIMESTAMPTZ" in sql


def test_role_prompt_field_migration_normalizes_prompt_fields_to_string() -> None:
    sql = (
        ROOT / "migrations/models/49_20260630093000_normalize_digital_employee_role_prompt_fields.py"
    ).read_text(encoding="utf-8")

    for field in ("identity", "principles", "responsibilities", "workflows", "safety_rules", "tools_notes"):
        assert f"'{{{field}}}'" in sql
        assert f"jsonb_typeof(\"meta\"->'{field}') <> 'string'" in sql
    assert "pg_temp.digital_employee_role_prompt_text" in sql
    assert '"digital_employee_role"' in sql


def test_role_description_removal_migration_drops_column_and_meta_key() -> None:
    sql = (
        ROOT / "migrations/models/52_20260701110000_remove_digital_employee_role_description.py"
    ).read_text(encoding="utf-8")

    assert '"meta" = "meta" - \'description\'' in sql
    assert 'ALTER TABLE "digital_employee_role" DROP COLUMN IF EXISTS "description"' in sql
    assert 'UPDATE "command_center_agent"' in sql
    assert 'SET "description" = NULL' in sql
    assert 'WHERE "role_key" IS NOT NULL' in sql
