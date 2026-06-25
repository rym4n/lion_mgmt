# coding=utf-8
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prompt_builder = _load_module("prompt_builder", "services/digital_employee/prompt_builder.py")
build_runtime_prompt = prompt_builder.build_runtime_prompt
render_workspace_files = prompt_builder.render_workspace_files
workspace_etag = prompt_builder.workspace_etag


def test_render_workspace_files_includes_role_skill_and_scope():
    role = SimpleNamespace(
        role_key="monitor",
        version=2,
        description="值班",
        meta={"identity": "监控值班员", "responsibilities": "分诊告警", "tools_notes": "只用授权工具"},
    )
    skill = SimpleNamespace(
        skill_key="alert_triage",
        version=1,
        name="告警分诊",
        description="判断告警优先级",
        bindings={"action_keys": ["notify"]},
        require_approval=False,
    )
    employee = SimpleNamespace(name="小狮", description="", resource_scope={"assets": ["db01"]})

    files = render_workspace_files(employee, role, [skill])

    assert "监控值班员" in files["SOUL.md"]
    assert "分诊告警" in files["AGENTS.md"]
    assert "只用授权工具" in files["TOOLS.md"]
    assert "alert_triage" in files["skills"]
    assert "notify" in files["skills"]["alert_triage"]["instructions.md"]


def test_workspace_etag_changes_with_skill_version():
    role = SimpleNamespace(role_key="monitor", version=1)
    old_skill = SimpleNamespace(skill_key="alert_triage", version=1)
    new_skill = SimpleNamespace(skill_key="alert_triage", version=2)

    assert workspace_etag(role, [old_skill]) != workspace_etag(role, [new_skill])


def test_build_runtime_prompt_contains_payload():
    task = SimpleNamespace(title="巡检", source="manual", description="检查数据库", payload={"target": "db01"})
    employee = SimpleNamespace(name="DBA", agent_key="dba-1")

    prompt = build_runtime_prompt(task, employee)

    assert "巡检" in prompt
    assert "dba-1" in prompt
    assert "db01" in prompt
