# coding=utf-8
import base64
import hashlib
import json
from typing import Any

from .skill_storage import (
    ALLOWED_SKILL_SUFFIXES,
    SKILL_BINARY_MAX_BYTES,
    SKILL_BINARY_SUFFIXES,
    skill_storage_root,
)


WORKSPACE_TEXT_FILES = ("system_prompt.md",)
WORKSPACE_JSON_FILES = ("manifest.json", "policy.json")
MAX_SKILL_FILE_BYTES = 512 * 1024


def _read_skill_files(skill: Any) -> dict[str, Any]:
    """把技能目录下的文件原样读出。文件是权威，此处不做任何加工。"""
    skill_dir = str(getattr(skill, "skill_dir", "") or "").strip()
    if not skill_dir:
        return {}
    root = (skill_storage_root() / skill_dir).resolve()
    if not root.is_dir() or skill_storage_root() not in root.parents:
        return {}
    files: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_SKILL_SUFFIXES:
            continue
        size = path.stat().st_size
        if path.suffix.lower() in SKILL_BINARY_SUFFIXES:
            if size > SKILL_BINARY_MAX_BYTES:
                continue
            files[path.relative_to(root).as_posix()] = {
                "encoding": "base64",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
            continue
        if size > MAX_SKILL_FILE_BYTES:
            continue
        try:
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


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


def _skill_payload(skill: Any) -> dict:
    return {
        "skill_key": getattr(skill, "skill_key", "skill"),
        "name": getattr(skill, "name", ""),
        "description": getattr(skill, "description", ""),
        "version": getattr(skill, "version", None),
    }


def _merge_approval_policy(base: Any, skill_entries: list[dict]) -> dict:
    policy = dict(base) if isinstance(base, dict) else {}
    if "default" not in policy:
        policy["default"] = "allow"
    return policy


def build_workspace_payload(employee: Any, role: Any | None = None, skills: list[Any] | None = None) -> dict:
    role = role or getattr(employee, "role", None)
    skills = skills or []
    role_meta = _public_payload(getattr(role, "meta", {}) or {})
    employee_metadata = _public_payload(getattr(employee, "metadata", {}) or {})
    rbac_role_id = getattr(employee, "rbac_role_id", None)
    skill_entries = [_skill_payload(skill) for skill in skills]
    tool_usage_notes = _prompt_text(role_meta.get("tools_notes"), "")

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
                if not isinstance(filename, str):
                    continue
                if isinstance(content, str):
                    flattened[f"skills/{skill_key}/{filename}"] = content.encode("utf-8")
                elif (
                    isinstance(content, dict)
                    and content.get("encoding") == "base64"
                    and isinstance(content.get("data"), str)
                ):
                    try:
                        flattened[f"skills/{skill_key}/{filename}"] = base64.b64decode(
                            content["data"], validate=True
                        )
                    except ValueError:
                        continue
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
        for skill in [_skill_payload(skill) for skill in skills or []]
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
        "AIOE 平台操作必须调用工具 `aioe_cli`；未配置平台 RBAC 角色时不得调用 AIOE 任何接口。",
        "`aioe_cli` 的唯一参数 command 是命令行参数字符串，可用子命令：",
        "- `apps`：列出可见应用；",
        "- `actions --app <应用英文标识>`：列出应用的动作与入参；",
        "- `resources --app <应用英文标识>`：列出应用可用资源（id/name）；",
        "- `action --app <应用英文标识> --action <动作英文标识> [--input '<JSON对象>'] [--resource <id或name>]`：执行动作。",
        "不要凭空猜测应用/动作/参数名，先用 apps/actions/resources 查询确认，再执行 action。",
        "例：查询深信服托管云主机清单 → command=\"action --app sangfor_managed_cloud --action GetCloudHostList --input '{}' --resource <资源名>\"。",
        "- `playbooks`：列出有权执行的已发布剧本；",
        "- `playbook-input --flow <剧本版本id>`：查询剧本入参 schema/default；",
        "- `playbook --flow <剧本版本id> --input '<JSON对象>'`：执行剧本。",
        "- `playbook-status --flow <剧本版本id> --run-id <run_id>`：查询同一次剧本运行的状态。",
        "执行剧本必须先调用 playbook-input，严格按返回的 schema/default 组装 --input；不得用 '{}' 执行有入参的剧本，无入参时也必须显式传 --input '{}'。",
        "playbook 返回 state=running 或非空 run_id 且 terminal=false 时，只能用 playbook-status 查询同一 run_id，直到 terminal=true。",
        "同一 run_id 终态前不得重新执行主剧本或拆分执行子剧本；state=unknown 或 retryable=false 时也禁止自动重试。",
    ]
    system_prompt.extend(["## Runtime", "\n".join(runtime_lines), ""])

    rendered_skills = {
        getattr(skill, "skill_key"): _read_skill_files(skill)
        for skill in (skills or [])
    }
    rendered_skills = {key: value for key, value in rendered_skills.items() if value}

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
    task_content = getattr(task, "description", None) or getattr(task, "title", "")
    task_payload = getattr(task, "payload", {}) or {}
    content_html = task_payload.get("content_html") if isinstance(task_payload, dict) else None
    rich_content = ("任务富文本（HTML）：\n" + content_html) if isinstance(content_html, str) and content_html else ""
    return "\n".join(
        [
            f"任务来源：{getattr(task, 'source', '')}",
            f"执行员工：{getattr(employee, 'name', '')} ({getattr(employee, 'agent_key', '')})",
            "",
            "任务内容：",
            task_content,
            "",
            rich_content,
            "",
            "任务载荷：",
            _json_block(_public_payload(task_payload)),
        ]
    )
