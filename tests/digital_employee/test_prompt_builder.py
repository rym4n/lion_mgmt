# coding=utf-8
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prompt_builder = _load_module("prompt_builder", "services/digital_employee/prompt_builder.py")
build_workspace_snapshots = prompt_builder.build_workspace_snapshots
build_runtime_prompt = prompt_builder.build_runtime_prompt
render_workspace_files = prompt_builder.render_workspace_files
workspace_etag = prompt_builder.workspace_etag


def test_render_workspace_files_includes_role_skill_and_minimal_runtime_notice():
    role = SimpleNamespace(
        role_key="monitor",
        version=2,
        meta={
            "identity": "监控值班员",
            "responsibilities": "分诊告警",
            "tools_notes": "只用授权工具",
            "approval_policy": {"filesystem.write": "approval_required"},
        },
    )
    skill = SimpleNamespace(
        skill_key="alert_triage",
        version=1,
        name="告警分诊",
        description="判断告警优先级",
        bindings={"action_keys": ["notify"], "mcp_tools": ["metrics.query"]},
        require_approval=False,
    )
    employee = SimpleNamespace(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        name="小狮",
        role_key="monitor",
        runtime="lion_agent",
        rbac_role_id="role-1",
        resource_scope={"assets": ["db01"]},
        metadata={"filesystem_policy": {"write": "deny"}},
    )

    files = render_workspace_files(employee, role, [skill])

    assert set(files) == {"system_prompt.md", "manifest.json", "policy.json", "skills"}
    assert "监控值班员" in files["system_prompt.md"]
    assert "分诊告警" in files["system_prompt.md"]
    assert "只用授权工具" not in files["system_prompt.md"]
    assert "以当前任务和平台强制访问控制为准。" in files["system_prompt.md"]
    assert "AgentScope Toolkit" not in files["system_prompt.md"]
    assert "AIOE CLI: 内置 AIOE 平台操作技能" in files["manifest.json"]
    assert "告警分诊: 判断告警优先级" in files["manifest.json"]
    assert "alert_triage" in files["skills"]
    assert "aioe-cli" in files["skills"]
    assert "未配置 RBAC 角色时不得调用 AIOE 任何接口" in files["skills"]["aioe-cli"]["instructions.md"].replace("，", "")
    assert "notify" not in files["skills"]["alert_triage"]["instructions.md"]
    assert '"agent_key": "monitor-1"' in files["manifest.json"]
    manifest = json.loads(files["manifest.json"])
    assert "description" not in manifest["employee"]
    assert "description" not in manifest["role"]
    assert '"workspace_etag":' in files["manifest.json"]
    assert '"metrics.query"' not in files["manifest.json"]
    assert '"enabled_tool_groups"' not in files["manifest.json"]
    assert '"enabled_actions"' not in files["manifest.json"]
    assert '"enabled_playbooks"' not in files["manifest.json"]
    assert '"source": "aioe_platform_role"' in files["policy.json"]
    assert '"rbac_role_id": "role-1"' in files["policy.json"]
    assert '"mode": "allow"' in files["policy.json"]
    assert '"resource_scope"' not in files["policy.json"]
    assert '"tool_permissions"' not in files["policy.json"]


def test_render_workspace_files_coerces_legacy_prompt_arrays_to_text():
    role = SimpleNamespace(
        role_key="monitor",
        version=1,
        meta={
            "identity": ["监控值班员"],
            "responsibilities": ["接收告警", "判断影响范围"],
            "principles": ["先确认影响范围", "结论必须可追溯"],
            "workflows": [
                {
                    "scene": "告警分诊",
                    "steps": ["读取告警源", "输出严重性"],
                }
            ],
            "safety_rules": ["不直接执行破坏性变更"],
            "tools_notes": ["只使用授权工具"],
        },
    )
    employee = SimpleNamespace(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        name="小狮",
        role_key="monitor",
        runtime="lion_agent",
        rbac_role_id="role-1",
        metadata={},
    )

    files = render_workspace_files(employee, role, [])

    assert isinstance(files["system_prompt.md"], str)
    assert set(files) == {"system_prompt.md", "manifest.json", "policy.json", "skills"}
    assert "- 接收告警" in files["system_prompt.md"]
    assert "## Principles" not in files["system_prompt.md"]
    assert "## Safety" not in files["system_prompt.md"]
    assert "## Workflows\n### 判断原则\n- 先确认影响范围" in files["system_prompt.md"]
    assert "### 告警分诊" in files["system_prompt.md"]
    assert "- 输出严重性" in files["system_prompt.md"]
    assert "- 不直接执行破坏性变更" not in files["system_prompt.md"]
    assert "AIOE CLI: 内置 AIOE 平台操作技能" in files["manifest.json"]


def test_render_workspace_files_does_not_fallback_to_description_fields():
    role = SimpleNamespace(
        role_key="monitor",
        version=1,
        description="旧岗位描述不应进入提示词",
        meta={},
    )
    employee = SimpleNamespace(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        name="小狮",
        description="旧员工描述不应进入提示词",
        role_key="monitor",
        runtime="lion_agent",
        rbac_role_id="role-1",
        metadata={},
    )

    files = render_workspace_files(employee, role, [])

    assert "你是 AIOE 数字员工。" not in files["system_prompt.md"]
    assert "旧岗位描述不应进入提示词" not in files["system_prompt.md"]
    assert "旧员工描述不应进入提示词" not in files["system_prompt.md"]
    manifest = json.loads(files["manifest.json"])
    assert "description" not in manifest["employee"]
    assert "description" not in manifest["role"]


def test_render_workspace_files_denies_aioe_api_without_employee_rbac_role():
    role = SimpleNamespace(role_key="monitor", version=1, meta={"identity": "监控值班员"})
    employee = SimpleNamespace(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        name="小狮",
        role_key="monitor",
        runtime="lion_agent",
        rbac_role_id=None,
        metadata={},
    )

    files = render_workspace_files(employee, role, [])
    manifest = json.loads(files["manifest.json"])
    policy = json.loads(files["policy.json"])

    assert manifest["skills"][0]["skill_key"] == "aioe-cli"
    assert manifest["skills"][0]["bindings"]["aioe_api"]["requires_rbac_role"] is True
    assert policy["authorization"]["rbac_role_id"] is None
    assert policy["authorization"]["aioe_api"]["mode"] == "deny"
    assert "未配置平台 RBAC 角色时不得调用 AIOE 任何接口" in files["system_prompt.md"]


def test_workspace_etag_changes_with_skill_version():
    role = SimpleNamespace(role_key="monitor", version=1)
    employee = SimpleNamespace(resource_scope={"assets": ["db01"]}, metadata={}, runtime="lion_agent", rbac_role_id="role-1")
    old_skill = SimpleNamespace(skill_key="alert_triage", version=1)
    new_skill = SimpleNamespace(skill_key="alert_triage", version=2)

    assert workspace_etag(role, [old_skill], employee) != workspace_etag(role, [new_skill], employee)


def test_workspace_etag_ignores_resource_scope_and_tracks_role_policy():
    role = SimpleNamespace(role_key="monitor", version=1, meta={})
    skill = SimpleNamespace(skill_key="alert_triage", version=1, bindings={}, require_approval=False)
    employee_a = SimpleNamespace(resource_scope={"assets": ["db01"]}, metadata={}, runtime="lion_agent", rbac_role_id="role-1")
    employee_b = SimpleNamespace(resource_scope={"assets": ["db02"]}, metadata={}, runtime="lion_agent", rbac_role_id="role-1")
    employee_c = SimpleNamespace(
        resource_scope={"assets": ["db01"]},
        metadata={"approval_policy": {"default": "approval_required"}},
        runtime="lion_agent",
        rbac_role_id="role-1",
    )
    employee_d = SimpleNamespace(
        resource_scope={"assets": ["db01"]},
        metadata={},
        runtime="lion_agent",
        rbac_role_id="role-2",
    )

    assert workspace_etag(role, [skill], employee_a) == workspace_etag(role, [skill], employee_b)
    assert workspace_etag(role, [skill], employee_a) != workspace_etag(role, [skill], employee_c)
    assert workspace_etag(role, [skill], employee_a) != workspace_etag(role, [skill], employee_d)


def test_workspace_snapshots_match_rendered_files_without_recursive_etag():
    role = SimpleNamespace(role_key="monitor", version=1, meta={})
    skill = SimpleNamespace(skill_key="alert_triage", version=1, bindings={}, require_approval=True)
    employee = SimpleNamespace(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        role_key="monitor",
        name="小狮",
        resource_scope={"assets": ["db01"]},
        rbac_role_id="role-1",
        metadata={},
        runtime="lion_agent",
    )

    snapshots = build_workspace_snapshots(employee, role, [skill])
    files = render_workspace_files(employee, role, [skill])

    assert snapshots["manifest_snapshot"]["workspace_etag"] == snapshots["workspace_etag"]
    assert f'"workspace_etag": "{snapshots["workspace_etag"]}"' in files["manifest.json"]
    assert snapshots["policy_snapshot"]["authorization"] == {
        "source": "aioe_platform_role",
        "rbac_role_id": "role-1",
        "aioe_api": {
            "mode": "allow",
            "required": True,
        },
    }
    assert "resource_scope" not in snapshots["policy_snapshot"]
    assert snapshots["policy_snapshot"]["approval"]["skills"]["alert_triage"] == "approval_required"


def test_build_runtime_prompt_contains_payload():
    task = SimpleNamespace(
        title="巡检",
        source="manual",
        description="检查数据库",
        payload={"target": "db01", "_dispatch_scope": "digital_employee"},
    )
    employee = SimpleNamespace(name="DBA", agent_key="dba-1")

    prompt = build_runtime_prompt(task, employee)

    assert "巡检" in prompt
    assert "dba-1" in prompt
    assert "db01" in prompt
    assert "_dispatch_scope" not in prompt
