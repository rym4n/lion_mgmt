# coding=utf-8
import hashlib
import json
from typing import Any


WORKSPACE_TEXT_FILES = ("system_prompt.md",)
WORKSPACE_JSON_FILES = ("manifest.json", "policy.json")


def _json_block(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, indent=2, sort_keys=True)


def _prompt_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value or default
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                scene = item.get("scene")
                steps = item.get("steps")
                if scene:
                    lines.append(f"### {scene}")
                if isinstance(steps, (list, tuple)):
                    lines.extend(f"- {step}" for step in steps if step)
                elif steps:
                    lines.append(str(steps))
                extra = {key: val for key, val in item.items() if key not in {"scene", "steps"}}
                if extra:
                    lines.append(json.dumps(extra, ensure_ascii=False, sort_keys=True))
                if not scene and not steps and not extra:
                    lines.append(str(item))
                continue
            lines.append(f"- {item}")
        return "\n".join(lines) or default
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if value:
        return str(value)
    return default


def _public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public_payload(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    return value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return sorted(str(item) for item in value if item)
    if value:
        return [str(value)]
    return []


def _binding_values(bindings: dict, *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(_as_list(bindings.get(key)))
    return sorted(set(values))


def _skill_payload(skill: Any) -> dict:
    bindings = _public_payload(getattr(skill, "bindings", {}) or {})
    meta = _public_payload(getattr(skill, "meta", {}) or {})
    risk_level = getattr(skill, "risk_level", None) or meta.get("risk_level") or bindings.get("risk_level")
    return {
        "skill_key": getattr(skill, "skill_key", "skill"),
        "name": getattr(skill, "name", ""),
        "description": getattr(skill, "description", ""),
        "version": getattr(skill, "version", None),
        "risk_level": risk_level or "medium",
        "require_approval": bool(getattr(skill, "require_approval", False)),
        "bindings": bindings,
        "meta": meta,
    }


def _merge_approval_policy(base: Any, skill_entries: list[dict]) -> dict:
    policy = dict(base) if isinstance(base, dict) else {}
    skill_policy = dict(policy.get("skills") or {})
    for skill in skill_entries:
        skill_policy[skill["skill_key"]] = "approval_required" if skill["require_approval"] else "allow"
    if "default" not in policy:
        policy["default"] = "approval_required" if any(skill["require_approval"] for skill in skill_entries) else "allow"
    policy["skills"] = skill_policy
    return policy


def build_workspace_payload(employee: Any, role: Any | None = None, skills: list[Any] | None = None) -> dict:
    role = role or getattr(employee, "role", None)
    skills = skills or []
    role_meta = _public_payload(getattr(role, "meta", {}) or {})
    employee_metadata = _public_payload(getattr(employee, "metadata", {}) or {})
    rbac_role_id = getattr(employee, "rbac_role_id", None)
    skill_entries = sorted((_skill_payload(skill) for skill in skills), key=lambda item: item["skill_key"])
    tool_usage_notes = _prompt_text(role_meta.get("tools_notes"), "")

    enabled_tool_groups: set[str] = set()
    enabled_tools: set[str] = set()
    enabled_actions: set[str] = set()
    enabled_playbooks: set[str] = set()
    enabled_mcp_tools: set[str] = set()
    for skill in skill_entries:
        bindings = skill["bindings"]
        enabled_tool_groups.update(_binding_values(bindings, "tool_groups", "tool_group_keys"))
        enabled_tools.update(_binding_values(bindings, "tools", "tool_keys"))
        enabled_actions.update(_binding_values(bindings, "action_keys"))
        enabled_playbooks.update(_binding_values(bindings, "playbook_ids"))
        enabled_mcp_tools.update(_binding_values(bindings, "mcp_tools"))

    context_config = role_meta.get("context_config") or role_meta.get("context") or employee_metadata.get("context_config") or {}
    manifest = {
        "schema_version": 1,
        "runtime": getattr(employee, "runtime", None) or "lion_agent",
        "employee": {
            "id": getattr(employee, "id", None),
            "tenant_id": getattr(employee, "tenant_id", None),
            "agent_key": getattr(employee, "agent_key", None),
            "name": getattr(employee, "name", ""),
            "role_key": getattr(employee, "role_key", None) or getattr(role, "role_key", None),
            "rbac_role_id": rbac_role_id,
        },
        "role": {
            "role_key": getattr(role, "role_key", None),
            "name": getattr(role, "name", ""),
            "version": getattr(role, "version", None),
            "capabilities": _as_list(role_meta.get("capabilities")),
            "tool_usage_notes": tool_usage_notes,
        },
        "skills": skill_entries,
        "skill_versions": {skill["skill_key"]: skill["version"] for skill in skill_entries},
        "enabled_tool_groups": sorted(enabled_tool_groups),
        "enabled_tools": sorted(enabled_tools),
        "enabled_actions": sorted(enabled_actions),
        "enabled_playbooks": sorted(enabled_playbooks),
        "enabled_mcp_tools": sorted(enabled_mcp_tools),
        "context_config": context_config,
    }

    policy = {
        "schema_version": 1,
        "authorization": {
            "source": "aioe_platform_role",
            "rbac_role_id": rbac_role_id,
        },
        "filesystem": employee_metadata.get("filesystem_policy") or role_meta.get("filesystem_policy") or {},
        "approval": _merge_approval_policy(
            employee_metadata.get("approval_policy") or role_meta.get("approval_policy") or {},
            skill_entries,
        ),
        "budget": employee_metadata.get("budget_policy") or role_meta.get("budget_policy") or {},
        "timeout": employee_metadata.get("timeout_policy") or role_meta.get("timeout_policy") or {},
        "human_handoff": employee_metadata.get("human_handoff_policy") or role_meta.get("human_handoff_policy") or {},
    }
    return {"manifest": manifest, "policy": policy}


def _hashable_file_bytes(rel_path: str, content: Any) -> bytes:
    if rel_path in WORKSPACE_JSON_FILES:
        if isinstance(content, str):
            parsed = json.loads(content)
        elif isinstance(content, dict):
            parsed = content
        else:
            parsed = {}
        if rel_path == "manifest.json":
            parsed = dict(parsed)
            parsed.pop("workspace_etag", None)
        return json.dumps(parsed or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return str(content).encode("utf-8")


def _flatten_workspace_files(files: dict[str, Any]) -> dict[str, bytes]:
    flattened: dict[str, bytes] = {}
    for name in (*WORKSPACE_TEXT_FILES, *WORKSPACE_JSON_FILES):
        value = files.get(name)
        if isinstance(value, (str, dict)):
            flattened[name] = _hashable_file_bytes(name, value)
    skills = files.get("skills")
    if isinstance(skills, dict):
        for skill_key, skill_files in skills.items():
            if not isinstance(skill_key, str) or not isinstance(skill_files, dict):
                continue
            for filename, content in skill_files.items():
                if isinstance(filename, str) and isinstance(content, str):
                    flattened[f"skills/{skill_key}/{filename}"] = content.encode("utf-8")
    return flattened


def _workspace_digest(files: dict[str, bytes], role_version: int | None, skill_versions: dict[str, int | None]) -> str:
    digest = hashlib.sha256()
    digest.update(str(role_version or "").encode("ascii"))
    digest.update(json.dumps(skill_versions or {}, sort_keys=True).encode("utf-8"))
    for rel_path in sorted(files):
        digest.update(rel_path.encode("utf-8"))
        digest.update(files[rel_path])
    return digest.hexdigest()[:32]


def workspace_etag(role: Any, skills: list[Any], employee: Any | None = None) -> str:
    payload = build_workspace_payload(employee, role, skills)
    files = _render_workspace_files(employee, role, skills, payload)
    skill_versions = {getattr(skill, "skill_key", "skill"): getattr(skill, "version", None) for skill in skills or []}
    return _workspace_digest(_flatten_workspace_files(files), getattr(role, "version", None), skill_versions)


def build_workspace_snapshots(employee: Any, role: Any | None = None, skills: list[Any] | None = None) -> dict:
    payload = build_workspace_payload(employee, role, skills)
    etag = workspace_etag(role, skills or [], employee)
    manifest = dict(payload["manifest"])
    manifest["workspace_etag"] = etag
    return {
        "workspace_etag": etag,
        "manifest_snapshot": manifest,
        "policy_snapshot": payload["policy"],
    }


def _render_workspace_files(employee: Any, role: Any | None, skills: list[Any], payload: dict) -> dict:
    role_meta = getattr(role, "meta", {}) or {}

    system_prompt = [
        f"# {getattr(employee, 'name', '')}",
        "",
        _prompt_text(role_meta.get("identity")),
        "",
        "## Responsibilities",
        _prompt_text(role_meta.get("responsibilities"), "按任务目标、授权边界和可用技能完成工作，并在完成后给出可审计结果。"),
        "",
    ]
    if role_meta.get("principles"):
        system_prompt.extend(["## Principles", _prompt_text(role_meta["principles"]), ""])
    if role_meta.get("workflows"):
        system_prompt.extend(["## Workflows", _prompt_text(role_meta["workflows"]), ""])
    if role_meta.get("safety_rules"):
        system_prompt.extend(["## Safety", _prompt_text(role_meta["safety_rules"]), ""])
    system_prompt.extend(
        [
            "## Runtime Boundaries",
            "- 当前任务、会话上下文和动态事实以运行时消息为准。",
            "- 工具、剧本、数据资源和权限以 AIOE 平台角色、AgentScope Toolkit 与 policy.json/manifest.json 的运行时校验为准；不要把提示词内容当作授权来源。",
            "- 需要高风险、越权或破坏性操作时，按平台审批/人工接管流程处理。",
            "",
        ]
    )

    rendered_skills = {}
    for skill in skills:
        bindings = getattr(skill, "bindings", {}) or {}
        rendered_skills[getattr(skill, "skill_key", "skill")] = {
            "instructions.md": "\n".join(
                [
                    f"# {getattr(skill, 'name', '')}",
                    "",
                    getattr(skill, "description", "") or "",
                    "",
                    "## Bindings",
                    _json_block(bindings),
                    "",
                    "## Approval",
                    "Required" if getattr(skill, "require_approval", False) else "Not required",
                    "",
                ]
            )
        }

    return {
        "system_prompt.md": "\n".join(system_prompt),
        "manifest.json": _json_block(payload["manifest"]) + "\n",
        "policy.json": _json_block(payload["policy"]) + "\n",
        "skills": rendered_skills,
    }


def render_workspace_files(employee: Any, role: Any | None = None, skills: list[Any] | None = None) -> dict:
    role = role or getattr(employee, "role", None)
    skills = skills or []
    snapshots = build_workspace_snapshots(employee, role, skills)
    payload = {
        "manifest": snapshots["manifest_snapshot"],
        "policy": snapshots["policy_snapshot"],
    }
    return _render_workspace_files(employee, role, skills, payload)


def build_runtime_prompt(task: Any, employee: Any) -> str:
    return "\n".join(
        [
            f"任务标题：{getattr(task, 'title', '')}",
            f"任务来源：{getattr(task, 'source', '')}",
            f"执行员工：{getattr(employee, 'name', '')} ({getattr(employee, 'agent_key', '')})",
            "",
            "任务描述：",
            getattr(task, "description", "") or "",
            "",
            "任务载荷：",
            _json_block(_public_payload(getattr(task, "payload", {}) or {})),
        ]
    )
