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
    assert "existing.enabled if existing else True" in source
    assert "data.get('version')" not in source
    assert "skill_candidate_condition(tenant_id)" in source
    assert "prefer_tenant_records(candidates, tenant_id, 'skill_key')" in source
    assert "existing is not None and (not existing.deleted)" in source
    assert "'is_builtin': False" in source
    assert "'deleted': False" in source
    assert "_visible_filter(tenant_id)" not in source
    assert "require_approval" not in source
    assert "meta" not in source


def test_employee_and_skill_toggles_have_update_permissions():
    employee = _function(VIEWS_PATH, "employee_toggle")
    skill = _function(VIEWS_PATH, "skill_toggle")

    assert "digital_employee:u" in ast.unparse(employee.decorator_list[1])
    assert "digital_employee_skill:u" in ast.unparse(skill.decorator_list[1])
    assert "skill.tenant_id != tenant_id" in ast.unparse(skill)


def test_disabled_and_builtin_skills_remain_manageable_with_tenant_scoped_delete():
    employee_detail_source = _source(VIEWS_PATH, "employee_detail")
    detail_source = _source(VIEWS_PATH, "skill_detail")
    lookup_source = _source(VIEWS_PATH, "_manageable_skill_or_404")
    soft_delete_source = _source(VIEWS_PATH, "_soft_delete_skill_for_tenant")
    delete_source = _source(VIEWS_PATH, "skill_delete")
    package_delete_source = _source(VIEWS_PATH, "package_delete")

    assert "enabled=None" in employee_detail_source
    assert "enabled=None" in detail_source
    assert "skill_candidate_condition" in lookup_source
    assert "prefer_visible_skills" in lookup_source
    assert "DigitalEmployeeSkill.update_or_create" in soft_delete_source
    assert "'is_builtin': False" in soft_delete_source
    assert "'deleted': True" in soft_delete_source
    assert "_delete_skill_for_tenant" in delete_source
    assert "_delete_skill_for_tenant" in package_delete_source


def test_dispatch_only_loads_enabled_employees():
    source = _source(MANAGER_PATH, "dispatch_task")

    assert "CommandAgent.filter(tenant_id=task.tenant_id, deleted=False, enabled=True)" in source


def test_command_center_agent_list_only_returns_enabled_employees():
    source = _source(COMMAND_VIEWS_PATH, "list_agents")

    assert "'enabled': True" in source
