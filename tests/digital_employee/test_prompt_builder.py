# coding=utf-8
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prompt_builder = _load_module(
    "services.digital_employee.prompt_builder",
    "services/digital_employee/prompt_builder.py",
)
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
    assert "所有 .docx、.xlsx、.pptx 文件的读取、创建和修改必须调用工具 `officecli`" in files["system_prompt.md"]
    assert "避免把完整文件结果放入上下文" in files["system_prompt.md"]
    assert "执行剧本必须先调用 playbook-input" in files["system_prompt.md"]
    assert "不得用 '{}' 执行有入参的剧本" in files["system_prompt.md"]
    assert "playbook-status --flow <剧本版本id> --run-id <run_id>" in files["system_prompt.md"]
    assert "不得重新执行主剧本或拆分执行子剧本" in files["system_prompt.md"]
    assert "非空 run_id 且 terminal=false" in files["system_prompt.md"]
    assert "查询同一 run_id" in files["system_prompt.md"]
    assert "state=unknown 或 retryable=false" in files["system_prompt.md"]
    assert "AgentScope Toolkit" not in files["system_prompt.md"]
    assert "内置 `aioe-cli` skill" not in files["system_prompt.md"]
    assert files["skills"] == {}
    assert '"agent_key": "monitor-1"' in files["manifest.json"]
    manifest = json.loads(files["manifest.json"])
    assert manifest["role"]["tool_usage_notes"] == "只用授权工具"
    assert manifest["employee"]["max_iters"] == 200
    assert manifest["skills"] == [
        {
            "description": "判断告警优先级",
            "name": "告警分诊",
            "skill_key": "alert_triage",
            "version": 1,
        }
    ]
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
    assert json.loads(files["manifest.json"])["skills"] == []


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

    assert "你是 AIOE 数字伙伴。" not in files["system_prompt.md"]
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

    assert manifest["skills"] == []
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
    skill = SimpleNamespace(skill_key="alert_triage", version=1)
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
    skill = SimpleNamespace(skill_key="alert_triage", version=1)
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
    assert snapshots["policy_snapshot"]["approval"] == {"default": "allow"}


def test_approval_policy_keeps_global_default_without_skill_overrides():
    role = SimpleNamespace(role_key="monitor", version=1, meta={})
    safe_skill = SimpleNamespace(
        skill_key="capacity_read",
        version=1,
        name="容量查询",
        description="只读查询云主机数量",
    )
    risky_skill = SimpleNamespace(
        skill_key="restart_host",
        version=1,
        name="重启主机",
        description="变更主机运行状态",
    )
    employee = SimpleNamespace(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        role_key="monitor",
        name="小狮",
        rbac_role_id="role-1",
        metadata={},
        runtime="lion_agent",
    )

    files = render_workspace_files(employee, role, [safe_skill, risky_skill])
    policy = json.loads(files["policy.json"])

    assert policy["approval"] == {"default": "allow"}


def test_build_runtime_prompt_contains_payload():
    task = SimpleNamespace(
        title="旧兼容标题",
        source="manual",
        description="检查数据库",
        payload={"target": "db01", "_dispatch_scope": "digital_employee"},
    )
    employee = SimpleNamespace(name="DBA", agent_key="dba-1")

    prompt = build_runtime_prompt(task, employee)

    assert "任务内容：\n检查数据库" in prompt
    assert "旧兼容标题" not in prompt
    assert "任务标题" not in prompt
    assert "dba-1" in prompt
    assert "db01" in prompt
    assert "_dispatch_scope" not in prompt


def test_build_runtime_prompt_exposes_rich_content_and_attachment_references():
    task = SimpleNamespace(
        title="旧兼容标题",
        source="manual",
        description="请核对图片中的拓扑",
        payload={
            "content_html": "<p>请核对 <strong>图片</strong> 中的拓扑</p>",
            "attachments": [
                {
                    "file_id": "command_center/tenant-1/diagram.png",
                    "filename": "diagram.png",
                    "size": 128,
                    "mime_type": "image/png",
                    "kind": "image",
                    "url": "/command_center/tasks/attachments/command_center%2Ftenant-1%2Fdiagram.png",
                }
            ],
        },
    )
    employee = SimpleNamespace(name="网络工程师", agent_key="network-1")

    prompt = build_runtime_prompt(task, employee)

    assert "任务富文本（HTML）：\n<p>请核对 <strong>图片</strong> 中的拓扑</p>" in prompt
    assert '"file_id": "command_center/tenant-1/diagram.png"' in prompt
    assert '"filename": "diagram.png"' in prompt


def test_workspace_skills_are_verbatim_files(tmp_path, monkeypatch):
    monkeypatch.setenv("RY_SKILL_ROOT", str(tmp_path))
    skill_dir = tmp_path / "tenant-1" / "db_backup_check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: db-backup-check\ndescription: d\n---\n\n# 正文\n",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "note.md").write_text("ref", encoding="utf-8")

    employee = SimpleNamespace(
        id="e1",
        tenant_id="tenant-1",
        name="员工",
        runtime="lion_agent",
        rbac_role_id="r1",
        agent_key="k",
        metadata={},
    )
    role = SimpleNamespace(
        role_key="monitor",
        name="监控",
        meta={"identity": "i", "responsibilities": "r"},
        version=1,
    )
    skill = SimpleNamespace(
        skill_key="db_backup_check",
        name="备份检查",
        description="d",
        skill_dir="tenant-1/db_backup_check",
        version=1,
        enabled=True,
    )

    files = render_workspace_files(employee, role, [skill])

    assert files["skills"]["db_backup_check"]["SKILL.md"].startswith("---")
    assert files["skills"]["db_backup_check"]["references/note.md"] == "ref"
    assert "instructions.md" not in files["skills"]["db_backup_check"]


def test_rendered_skill_name_matches_manifest_key_without_modifying_source(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("RY_SKILL_ROOT", str(tmp_path))
    skill_dir = tmp_path / "tenant-1" / "ops_check"
    skill_dir.mkdir(parents=True)
    source = "---\nname: 中文技能名称\ndescription: 描述\nmetadata:\n  owner: 值班组\n---\n\n# 正文\n保留内容。\n"
    (skill_dir / "SKILL.md").write_text(source, encoding="utf-8")
    skill = SimpleNamespace(
        skill_key="ops_check", skill_dir="tenant-1/ops_check", name="中文技能名称",
        description="描述", version=1,
    )
    employee = SimpleNamespace(name="员工", agent_key="ops-1", max_iters=450, metadata={})
    files = render_workspace_files(employee, None, [skill])
    rendered = files["skills"]["ops_check"]["SKILL.md"]
    frontmatter, body = rendered.split("---\n", 2)[1:]

    assert yaml.safe_load(frontmatter) == {
        "name": "ops_check", "description": "描述", "metadata": {"owner": "值班组"},
    }
    assert body == "\n# 正文\n保留内容。\n"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == source
    manifest = json.loads(files["manifest.json"])
    assert manifest["skills"][0]["skill_key"] == "ops_check"
    assert manifest["employee"]["max_iters"] == 450


def test_workspace_etag_changes_when_employee_max_iters_changes():
    employee = SimpleNamespace(name="员工", agent_key="ops-1", max_iters=200, metadata={})
    role = SimpleNamespace(role_key="ops", meta={"identity": "operator"}, version=1)
    before = workspace_etag(role, [], employee)
    employee.max_iters = 300
    assert workspace_etag(role, [], employee) != before


def test_workspace_skills_encode_image_and_office_files(tmp_path, monkeypatch):
    monkeypatch.setenv("RY_SKILL_ROOT", str(tmp_path))
    skill_dir = tmp_path / "tenant-1" / "visual_inspection"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: visual-inspection\ndescription: d\n---\n",
        encoding="utf-8",
    )
    (skill_dir / "topology.png").write_bytes(b"\x89PNG\r\n\x1a")
    (skill_dir / "runbook.docx").write_bytes(b"PK\x03\x04office")
    employee = SimpleNamespace(
        id="e1",
        tenant_id="tenant-1",
        name="employee",
        runtime="lion_agent",
        rbac_role_id="r1",
        agent_key="k",
        metadata={},
    )
    role = SimpleNamespace(
        role_key="monitor",
        name="monitor",
        meta={"identity": "i", "responsibilities": "r"},
        version=1,
    )
    skill = SimpleNamespace(
        skill_key="visual_inspection",
        name="visual inspection",
        description="d",
        skill_dir="tenant-1/visual_inspection",
        version=1,
        enabled=True,
    )

    files = render_workspace_files(employee, role, [skill])["skills"]["visual_inspection"]

    assert files["topology.png"] == {"encoding": "base64", "data": "iVBORw0KGg=="}
    assert files["runbook.docx"] == {"encoding": "base64", "data": "UEsDBG9mZmljZQ=="}


def test_workspace_skills_do_not_sync_officecli_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("RY_SKILL_ROOT", str(tmp_path))
    skill_dir = tmp_path / "tenant-1" / "officecli"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: officecli\ndescription: d\n---\n", encoding="utf-8"
    )
    (skill_dir / "officecli-linux-x64").write_bytes(b"ELF\x00binary")

    employee = SimpleNamespace(id="e1", tenant_id="tenant-1", metadata={})
    skill = SimpleNamespace(skill_key="officecli", skill_dir="tenant-1/officecli")

    files = render_workspace_files(employee, None, [skill])["skills"]["officecli"]

    assert "officecli-linux-x64" not in files
    assert "SKILL.md" in files


def test_system_prompt_has_no_skill_listing(tmp_path, monkeypatch):
    """技能清单由 AgentScope 自动注入，system_prompt 不得重复拼接。"""
    monkeypatch.setenv("RY_SKILL_ROOT", str(tmp_path))
    skill_dir = tmp_path / "tenant-1" / "db_backup_check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: db-backup-check\ndescription: d\n---\n\n# 正文\n",
        encoding="utf-8",
    )

    employee = SimpleNamespace(
        id="e1",
        tenant_id="tenant-1",
        name="员工",
        runtime="lion_agent",
        rbac_role_id="r1",
        agent_key="k",
        metadata={},
    )
    role = SimpleNamespace(
        role_key="monitor",
        name="监控",
        meta={"identity": "i", "responsibilities": "r"},
        version=1,
    )
    skill = SimpleNamespace(
        skill_key="db_backup_check",
        name="备份检查",
        description="d",
        skill_dir="tenant-1/db_backup_check",
        version=1,
        enabled=True,
    )

    prompt = render_workspace_files(employee, role, [skill])["system_prompt.md"]

    assert "db-backup-check" not in prompt
    assert "agent-skills" not in prompt
    assert "## Runtime" in prompt
