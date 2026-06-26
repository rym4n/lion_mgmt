# coding=utf-8
import hashlib
import json
from typing import Any


def _json_block(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, indent=2, sort_keys=True)


def _public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if not str(key).startswith("_")}
    return value


def workspace_etag(role: Any, skills: list[Any]) -> str:
    payload = {
        "role_key": getattr(role, "role_key", None),
        "role_version": getattr(role, "version", None),
        "skills": sorted((getattr(skill, "skill_key", ""), getattr(skill, "version", 0)) for skill in skills),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def render_workspace_files(employee: Any, role: Any | None = None, skills: list[Any] | None = None) -> dict:
    role = role or getattr(employee, "role", None)
    skills = skills or []
    role_meta = getattr(role, "meta", {}) or {}
    resource_scope = getattr(employee, "resource_scope", {}) or {}

    soul = [
        f"# {getattr(employee, 'name', '')}",
        "",
        role_meta.get("identity") or getattr(role, "description", "") or getattr(employee, "description", "") or "你是 AIOE 数字员工。",
        "",
        "## Resource Scope",
        _json_block(resource_scope),
        "",
    ]

    agents = [
        "# Operating Contract",
        "",
        role_meta.get("responsibilities") or "按任务目标、授权边界和可用技能完成工作，并在完成后给出可审计结果。",
    ]
    if role_meta.get("principles"):
        agents.extend(["", "## Principles", str(role_meta["principles"])])
    if role_meta.get("workflows"):
        agents.extend(["", "## Workflows", _json_block(role_meta["workflows"])])
    if role_meta.get("safety_rules"):
        agents.extend(["", "## Safety", str(role_meta["safety_rules"])])

    tools = ["# Tools", "", role_meta.get("tools_notes") or "仅使用平台授权的剧本、动作、MCP 工具和数据资源。"]
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
        "SOUL.md": "\n".join(soul),
        "AGENTS.md": "\n".join(agents) + "\n",
        "TOOLS.md": "\n".join(tools) + "\n",
        "skills": rendered_skills,
    }


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
