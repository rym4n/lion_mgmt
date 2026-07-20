# coding=utf-8
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VIEWS_PATH = ROOT / "services/digital_employee/views.py"
MANAGER_PATH = ROOT / "services/command_center/manager.py"
COMMAND_VIEWS_PATH = ROOT / "services/command_center/views.py"


def _function(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _source(path: Path, name: str) -> str:
    return ast.unparse(_function(path, name))


def test_skill_save_maintains_version_and_lifecycle_fields_server_side():
    source = _source(VIEWS_PATH, "skill_save")

    assert "existing.version + 1 if existing and content_changed" in source
    assert "(existing.meta or {}).get('files') != data.get('files')" in source
    assert "existing.enabled if existing else True" in source
    assert "existing.require_approval if existing else False" in source
    assert "data.get('version')" not in source
    assert "_visible_filter(tenant_id)" in source


def test_employee_and_skill_toggles_have_update_permissions():
    employee = _function(VIEWS_PATH, "employee_toggle")
    skill = _function(VIEWS_PATH, "skill_toggle")

    assert "digital_employee:u" in ast.unparse(employee.decorator_list[1])
    assert "digital_employee_skill:u" in ast.unparse(skill.decorator_list[1])
    assert "skill.tenant_id != tenant_id" in ast.unparse(skill)


def test_disabled_skills_remain_manageable_and_delete_stays_tenant_scoped():
    employee_detail_source = _source(VIEWS_PATH, "employee_detail")
    detail_source = _source(VIEWS_PATH, "skill_detail")
    lookup_source = _source(VIEWS_PATH, "_tenant_skill_or_404")
    delete_source = _source(VIEWS_PATH, "skill_delete")
    package_delete_source = _source(VIEWS_PATH, "package_delete")

    assert "enabled=None" in employee_detail_source
    assert "enabled=None" in detail_source
    assert "tenant_id=tenant_id" in lookup_source
    assert "deleted=False" in lookup_source
    assert "get_visible_skills" not in lookup_source
    assert "_tenant_skill_or_404" in delete_source
    assert "SKILL_IN_USE_BY_EMPLOYEES" in delete_source
    assert "_tenant_skill_or_404" in package_delete_source
    assert "SKILL_IN_USE_BY_EMPLOYEES" in package_delete_source


def test_dispatch_only_loads_enabled_employees():
    source = _source(MANAGER_PATH, "dispatch_task")

    assert "CommandAgent.filter(tenant_id=task.tenant_id, deleted=False, enabled=True)" in source


def test_command_center_agent_list_only_returns_enabled_employees():
    source = _source(COMMAND_VIEWS_PATH, "list_agents")

    assert "'enabled': True" in source
