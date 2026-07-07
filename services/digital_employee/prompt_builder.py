# coding=utf-8
import hashlib
import json
from typing import Any


WORKSPACE_TEXT_FILES = ("system_prompt.md",)
WORKSPACE_JSON_FILES = ("manifest.json", "policy.json")
AIOE_CLI_SKILL_KEY = "aioe-cli"
AIOE_CLI_SKILL_VERSION = 1
AIOE_CLI_DESCRIPTION = (
    "内置 AIOE 平台操作技能。负责读取当前员工的 AIOE 平台 RBAC 角色授权，"
    "按授权调用 AIOE API 完成资产、告警、任务、剧本、报告、应用和系统配置等平台操作；"
    "未配置 RBAC 角色时不得调用任何 AIOE API。"
)
PLATFORM_BINDING_KEYS = {
    "tool_groups",
    "tool_group_keys",
    "tools",
    "tool_keys",
    "action_keys",
    "playbook_ids",
    "mcp_tools",
    "aioe_api",
    "aioe_apps",
    "rbac_roles",
}


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


def _effective_workflows(role_meta: dict) -> str:
    principles = _prompt_text(role_meta.get("principles"), "").strip()
    workflows = _prompt_text(role_meta.get("workflows"), "").strip()
    if principles and workflows:
        if principles in workflows:
            return workflows
        return f"### 判断原则\n{principles}\n\n{workflows}"
    return workflows or principles


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


def _derived_tool_usage_notes(skill_entries: list[dict]) -> str:
    lines: list[str] = []
    for skill in skill_entries:
        name = skill.get("name") or skill.get("skill_key") or ""
        description = skill.get("description") or ""
        body = ": ".join(item for item in (name, description) if item)
        if body:
            lines.append(body)
    return "\n".join(lines)


def _skill_payload(skill: Any) -> dict:
    raw_bindings = _public_payload(getattr(skill, "bindings", {}) or {})
    bindings = {
        key: value
        for key, value in raw_bindings.items()
        if key not in PLATFORM_BINDING_KEYS
    }
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


def _builtin_aioe_cli_skill_payload(rbac_role_id: str | None) -> dict:
    return {
        "skill_key": AIOE_CLI_SKILL_KEY,
        "name": "AIOE CLI",
        "description": AIOE_CLI_DESCRIPTION,
        "version": AIOE_CLI_SKILL_VERSION,
        "risk_level": "high",
        "require_approval": False,
        "bindings": {
            "runtime_skills": [AIOE_CLI_SKILL_KEY],
            "aioe_api": {
                "requires_rbac_role": True,
                "rbac_role_id": rbac_role_id,
            },
        },
        "meta": {
            "builtin": True,
            "authorization_source": "aioe_platform_role",
            "usage_contract": [
                "所有 AIOE 平台 API 调用必须通过员工实例绑定的 RBAC 角色授权。",
                "未配置 RBAC 角色时，不得调用 AIOE 任何接口，只能说明缺少人工授权。",
                "调用前明确目标接口、参数、影响范围和审计证据；高风险写操作先输出审批建议。",
            ],
        },
    }


def _with_builtin_skills(skill_entries: list[dict], rbac_role_id: str | None) -> list[dict]:
    entries = list(skill_entries)
    if not any(item.get("skill_key") == AIOE_CLI_SKILL_KEY for item in entries):
        entries.append(_builtin_aioe_cli_skill_payload(rbac_role_id))
    return sorted(entries, key=lambda item: item["skill_key"])


def _render_skill_instructions(skill: dict) -> str:
    usage_contract = _prompt_text((skill.get("meta") or {}).get("usage_contract"), "")
    return "\n".join(
        [
            f"# {skill.get('name') or ''}",
            "",
            skill.get("description") or "",
            "",
            "## Bindings",
            _json_block(skill.get("bindings") or {}),
            "",
            "## Approval",
            "Required" if skill.get("require_approval") else "Not required",
            "",
            "## Usage Contract",
            usage_contract,
            "",
        ]
    )


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
    skill_entries = _with_builtin_skills([_skill_payload(skill) for skill in skills], rbac_role_id)
    tool_usage_notes = _derived_tool_usage_notes(skill_entries) or _prompt_text(role_meta.get("tools_notes"), "")

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
        "context_config": context_config,
    }

    policy = {
        "schema_version": 1,
        "authorization": {
            "source": "aioe_platform_role",
            "rbac_role_id": rbac_role_id,
            "aioe_api": {
                "mode": "allow" if rbac_role_id else "deny",
                "required": True,
            },
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
    skill_versions = {
        skill["skill_key"]: skill.get("version")
        for skill in _with_builtin_skills([_skill_payload(skill) for skill in skills or []], getattr(employee, "rbac_role_id", None))
    }
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
    workflows = _effective_workflows(role_meta)

    system_prompt = [
        f"# {getattr(employee, 'name', '')}",
        "",
        _prompt_text(role_meta.get("identity")),
        "",
        "## Responsibilities",
        _prompt_text(role_meta.get("responsibilities"), "按任务目标、授权边界和可用技能完成工作，并在完成后给出可审计结果。"),
        "",
    ]
    if workflows:
        system_prompt.extend(["## Workflows", workflows, ""])
    runtime_lines = [
        "以当前任务和平台强制访问控制为准。",
        f"AIOE 平台操作必须使用内置 `{AIOE_CLI_SKILL_KEY}` skill；未配置平台 RBAC 角色时不得调用 AIOE 任何接口。",
    ]
    system_prompt.extend(["## Runtime", "\n".join(runtime_lines), ""])

    rendered_skills = {
        skill["skill_key"]: {"instructions.md": _render_skill_instructions(skill)}
        for skill in payload["manifest"].get("skills", [])
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
