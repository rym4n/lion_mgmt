# coding=utf-8
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NESTED_DIGITAL_EMPLOYEE_PERMISSIONS = (
    "digital_employee:role:",
    "digital_employee:skill:",
    "digital_employee:report:",
    "digital_employee:package:",
)


def test_digital_employee_routes_use_platform_two_segment_permissions():
    source = (ROOT / "services/digital_employee/views.py").read_text(encoding="utf-8")

    for permission_prefix in NESTED_DIGITAL_EMPLOYEE_PERMISSIONS:
        assert permission_prefix not in source

    for permission in (
        'requires_permission("digital_employee:c")',
        'requires_permission("digital_employee:r")',
        'requires_permission("digital_employee:u")',
        'requires_permission("digital_employee:d")',
        'requires_permission("digital_employee_skill:r", "digital_employee:r")',
        'requires_permission("digital_employee_skill:r")',
        'requires_permission("digital_employee_skill:d")',
        '"digital_employee_skill:c"',
        '"digital_employee_skill:u"',
    ):
        assert permission in source


def test_platform_wildcard_matches_flat_digital_employee_permissions():
    import ast

    path = ROOT / "libs/security/authorization.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    check_wildcard_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "check_wildcard"
    )
    module = ast.Module(body=[check_wildcard_node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {}
    exec(compile(module, str(path), "exec"), namespace)
    check_wildcard = namespace["check_wildcard"]

    assert check_wildcard("*:*", "digital_employee:r") is True
    assert check_wildcard("digital_employee:*", "digital_employee:r") is True
    assert check_wildcard("*:*", "digital_employee_skill:r") is True
    assert check_wildcard("digital_employee_skill:*", "digital_employee_skill:r") is True
    assert check_wildcard("*:*", "digital_employee:role:r") is False
    assert check_wildcard("*:*", "digital_employee:skill:r") is False


def test_digital_employee_permission_migrations_flatten_existing_data():
    initial_sql = (ROOT / "migrations/models/40_20260624_add_digital_employee.py").read_text(encoding="utf-8")
    repair_sql = (
        ROOT / "migrations/models/43_20260627161500_flatten_digital_employee_permissions.py"
    ).read_text(encoding="utf-8")

    assert '["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"]' in initial_sql
    for permission_prefix in NESTED_DIGITAL_EMPLOYEE_PERMISSIONS:
        assert permission_prefix not in initial_sql

    assert "WHEN 'digital_employee:role:r' THEN 'digital_employee:r'" in repair_sql
    assert "WHEN 'digital_employee:skill:r' THEN 'digital_employee:r'" in repair_sql
    assert "WHEN 'digital_employee:report:r' THEN 'digital_employee:r'" in repair_sql
    assert "WHEN 'digital_employee:package:r' THEN 'digital_employee:r'" in repair_sql
