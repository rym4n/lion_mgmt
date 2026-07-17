# coding=utf-8
import uuid
from datetime import datetime
from types import SimpleNamespace

from tortoise.expressions import Q

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent, CommandTask
from models.digital_employee import DigitalEmployeeRole, DigitalEmployeeSkill
from settings import TZ

from .aioe_credential_manager import create_employee_api_account
from .lion_agent_client import LionAgentClient
from .prompt_builder import AIOE_CLI_SKILL_KEY, AIOE_CLI_SKILL_VERSION, render_workspace_files, workspace_etag
from .rbac import require_existing_platform_role


BUILTIN_TENANT_ID = "__builtin__"
TERMINAL_TASK_STATUS = {"completed", "failed", "cancelled"}


def _visible_condition(tenant_id: str):
    return Q(deleted=False) & (Q(tenant_id=tenant_id) | Q(is_builtin=True))


def prefer_tenant_records(records: list, tenant_id: str, key_attr: str) -> list:
    """Prefer tenant-owned package rows over package-provided builtin baselines."""
    preferred = {}
    for record in records:
        key = getattr(record, key_attr, None)
        if not key:
            continue
        current = preferred.get(key)
        if current is None or (getattr(record, "tenant_id", None) == tenant_id and getattr(current, "tenant_id", None) != tenant_id):
            preferred[key] = record
    return list(preferred.values())


class BuiltinAioeCliSkill(SimpleNamespace):
    @property
    def json(self) -> dict:
        return {
            "id": AIOE_CLI_SKILL_KEY,
            "ctime": None,
            "utime": None,
            "tenant_id": BUILTIN_TENANT_ID,
            "skill_key": self.skill_key,
            "name": self.name,
            "description": self.description,
            "skill_dir": self.skill_dir,
            "bindings": self.bindings,
            "require_approval": self.require_approval,
            "version": self.version,
            "is_builtin": self.is_builtin,
            "enabled": self.enabled,
            "meta": self.meta,
        }


def _builtin_aioe_cli_skill() -> BuiltinAioeCliSkill:
    return BuiltinAioeCliSkill(
        skill_key=AIOE_CLI_SKILL_KEY,
        name="AIOE CLI",
        description="内置 AIOE 平台操作技能。",
        skill_dir="",
        bindings={
            "runtime_skills": [AIOE_CLI_SKILL_KEY],
            "aioe_api": {"requires_rbac_role": True},
        },
        require_approval=False,
        version=AIOE_CLI_SKILL_VERSION,
        is_builtin=True,
        enabled=True,
        meta={"builtin": True, "authorization_source": "aioe_platform_role"},
    )


async def get_visible_role(tenant_id: str, role_key: str) -> DigitalEmployeeRole:
    roles = await DigitalEmployeeRole.filter(_visible_condition(tenant_id), role_key=role_key, enabled=True).all()
    visible = prefer_tenant_records(roles, tenant_id, "role_key")
    role = visible[0] if visible else None
    if not role:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_ROLE_NOT_FOUND)
    return role


async def get_visible_skills(
    tenant_id: str, skill_keys: list[str], enabled: bool | None = True
) -> list[DigitalEmployeeSkill]:
    if not skill_keys:
        return []
    requested_keys = list(dict.fromkeys(skill_keys))
    query_keys = [key for key in requested_keys if key != AIOE_CLI_SKILL_KEY]
    skills = []
    if query_keys:
        filters = {"skill_key__in": query_keys}
        if enabled is not None:
            filters["enabled"] = enabled
        skills = await DigitalEmployeeSkill.filter(_visible_condition(tenant_id), **filters).all()
    preferred = prefer_tenant_records(skills, tenant_id, "skill_key")
    skills_by_key = {skill.skill_key: skill for skill in preferred}
    if AIOE_CLI_SKILL_KEY in requested_keys:
        skills_by_key[AIOE_CLI_SKILL_KEY] = _builtin_aioe_cli_skill()
    if set(skills_by_key) != set(requested_keys):
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_SKILL_NOT_FOUND)
    return [skills_by_key[key] for key in requested_keys]


def derive_capabilities(role: DigitalEmployeeRole, skills: list[DigitalEmployeeSkill]) -> list[str]:
    capabilities = set((role.meta or {}).get("capabilities") or [])
    capabilities.add(role.role_key)
    capabilities.add(AIOE_CLI_SKILL_KEY)
    for skill in skills:
        capabilities.add(skill.skill_key)
        capabilities.update((skill.meta or {}).get("capabilities") or [])
    return sorted(str(item) for item in capabilities if item)


def effective_employee_skill_keys(skill_keys: list[str] | None) -> list[str]:
    keys = [str(key) for key in (skill_keys or []) if key]
    if AIOE_CLI_SKILL_KEY not in keys:
        keys.append(AIOE_CLI_SKILL_KEY)
    return keys


def persisted_employee_skill_keys(skill_keys: list[str] | None) -> list[str]:
    return [key for key in dict.fromkeys(str(item) for item in (skill_keys or []) if item) if key != AIOE_CLI_SKILL_KEY]


async def create_employee(
    tenant_id: str,
    role_key: str,
    name: str,
    skill_keys: list[str] | None = None,
    max_concurrent: int = 1,
    agent_key: str | None = None,
    rbac_role_id: str | None = None,
) -> CommandAgent:
    role = await get_visible_role(tenant_id, role_key)
    selected_rbac_role_id = rbac_role_id
    await require_existing_platform_role(selected_rbac_role_id, tenant_id)
    keys = skill_keys if skill_keys is not None else list(role.default_skill_keys or [])
    skills = await get_visible_skills(tenant_id, keys)
    key = agent_key or f"{role_key}-{uuid.uuid4().hex[:8]}"
    if await CommandAgent.get_or_none(tenant_id=tenant_id, agent_key=key, deleted=False):
        raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
    employee = await CommandAgent.create(
        tenant_id=tenant_id,
        agent_key=key,
        name=name,
        capabilities=derive_capabilities(role, skills),
        status="idle",
        max_concurrent=max(1, int(max_concurrent or 1)),
        runtime="lion_agent",
        metadata={"digital_employee": True},
        role=role,
        role_key=role.role_key,
        skill_keys=persisted_employee_skill_keys([skill.skill_key for skill in skills]),
        rbac_role_id=selected_rbac_role_id,
        resource_scope={},
    )
    await sync_employee_workspace(employee, role=role, skills=skills)
    await create_employee_api_account(employee)
    return employee


async def sync_employee_workspace(
    employee: CommandAgent,
    role: DigitalEmployeeRole | None = None,
    skills: list[DigitalEmployeeSkill] | None = None,
    force: bool = False,
) -> CommandAgent:
    if role is None:
        role = await get_visible_role(employee.tenant_id, employee.role_key)
    if skills is None:
        skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
    etag = workspace_etag(role, skills, employee)
    if not force and employee.workspace_etag == etag and employee.workspace_synced_at:
        return employee
    files = render_workspace_files(employee, role, skills)
    result = await LionAgentClient().sync_workspace(
        employee.id,
        files,
        role_version=getattr(role, "version", None),
        skill_versions={
            **{skill.skill_key: skill.version for skill in skills},
            AIOE_CLI_SKILL_KEY: AIOE_CLI_SKILL_VERSION,
        },
    )
    employee.workspace_etag = result.get("etag") or etag
    employee.workspace_synced_at = datetime.now(TZ)
    await employee.save(update_fields=["workspace_etag", "workspace_synced_at", "utime"])
    return employee


async def update_employee_skills(employee: CommandAgent, skill_keys: list[str]) -> CommandAgent:
    role = await get_visible_role(employee.tenant_id, employee.role_key)
    skills = await get_visible_skills(employee.tenant_id, skill_keys)
    employee.skill_keys = persisted_employee_skill_keys([skill.skill_key for skill in skills])
    employee.capabilities = derive_capabilities(role, skills)
    employee.workspace_etag = None
    await employee.save(update_fields=["skill_keys", "capabilities", "workspace_etag", "utime"])
    return await sync_employee_workspace(employee, role=role, skills=skills)


async def upgrade_role_for_existing_employees(role: DigitalEmployeeRole) -> int:
    employees = await CommandAgent.filter(role_key=role.role_key, deleted=False).all()
    count = 0
    for employee in employees:
        employee.workspace_etag = None
        await employee.save(update_fields=["workspace_etag", "utime"])
        await sync_employee_workspace(employee, role=role)
        count += 1
    return count


async def ensure_employee_can_delete(employee: CommandAgent) -> None:
    pending = await CommandTask.filter(
        tenant_id=employee.tenant_id,
        assigned_agent_id=employee.id,
        deleted=False,
    ).exclude(status__in=TERMINAL_TASK_STATUS).count()
    if pending:
        raise CustomError(ErrorCode.EMPLOYEE_HAS_PENDING_TASKS)
