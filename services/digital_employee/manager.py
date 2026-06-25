# coding=utf-8
import uuid
from datetime import datetime

from tortoise.expressions import Q

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent, CommandTask
from models.digital_employee import DigitalEmployeeRole, DigitalEmployeeSkill
from settings import TZ

from .lion_agent_client import LionAgentClient
from .prompt_builder import render_workspace_files, workspace_etag


BUILTIN_TENANT_ID = "__builtin__"
TERMINAL_TASK_STATUS = {"completed", "failed", "cancelled"}


def _visible_condition(tenant_id: str):
    return Q(deleted=False) & (Q(tenant_id=tenant_id) | Q(is_builtin=True))


async def get_visible_role(tenant_id: str, role_key: str) -> DigitalEmployeeRole:
    role = await DigitalEmployeeRole.filter(_visible_condition(tenant_id), role_key=role_key, enabled=True).first()
    if not role:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_ROLE_NOT_FOUND)
    return role


async def get_visible_skills(tenant_id: str, skill_keys: list[str]) -> list[DigitalEmployeeSkill]:
    if not skill_keys:
        return []
    skills = await DigitalEmployeeSkill.filter(_visible_condition(tenant_id), skill_key__in=skill_keys, enabled=True).all()
    if {skill.skill_key for skill in skills} != set(skill_keys):
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_SKILL_NOT_FOUND)
    return skills


def derive_capabilities(role: DigitalEmployeeRole, skills: list[DigitalEmployeeSkill]) -> list[str]:
    capabilities = set((role.meta or {}).get("capabilities") or [])
    capabilities.add(role.role_key)
    for skill in skills:
        capabilities.add(skill.skill_key)
        capabilities.update((skill.meta or {}).get("capabilities") or [])
        bindings = skill.bindings or {}
        capabilities.update(bindings.get("action_keys") or [])
        capabilities.update(bindings.get("playbook_ids") or [])
        capabilities.update(bindings.get("mcp_tools") or [])
    return sorted(str(item) for item in capabilities if item)


async def create_employee(
    tenant_id: str,
    role_key: str,
    name: str,
    skill_keys: list[str] | None = None,
    resource_scope: dict | None = None,
    description: str | None = None,
    max_concurrent: int = 1,
    agent_key: str | None = None,
) -> CommandAgent:
    role = await get_visible_role(tenant_id, role_key)
    keys = skill_keys if skill_keys is not None else list(role.default_skill_keys or [])
    skills = await get_visible_skills(tenant_id, keys)
    key = agent_key or f"{role_key}-{uuid.uuid4().hex[:8]}"
    if await CommandAgent.get_or_none(tenant_id=tenant_id, agent_key=key, deleted=False):
        raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
    employee = await CommandAgent.create(
        tenant_id=tenant_id,
        agent_key=key,
        name=name,
        description=description or role.description,
        capabilities=derive_capabilities(role, skills),
        status="idle",
        max_concurrent=max(1, int(max_concurrent or 1)),
        runtime="lion_agent",
        metadata={"digital_employee": True},
        role=role,
        role_key=role.role_key,
        skill_keys=[skill.skill_key for skill in skills],
        rbac_role_id=role.default_rbac_role_id,
        resource_scope=resource_scope or {},
    )
    await sync_employee_workspace(employee, role=role, skills=skills)
    return employee


async def sync_employee_workspace(
    employee: CommandAgent,
    role: DigitalEmployeeRole | None = None,
    skills: list[DigitalEmployeeSkill] | None = None,
) -> CommandAgent:
    if role is None:
        role = await employee.role
    if role is None:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_ROLE_NOT_FOUND)
    if skills is None:
        skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
    etag = workspace_etag(role, skills)
    if employee.workspace_etag == etag and employee.workspace_synced_at:
        return employee
    files = render_workspace_files(employee, role, skills)
    result = await LionAgentClient().sync_workspace(
        employee.id,
        files,
        role_version=getattr(role, "version", None),
        skill_versions={skill.skill_key: skill.version for skill in skills},
    )
    employee.workspace_etag = result.get("etag") or etag
    employee.workspace_synced_at = datetime.now(TZ)
    await employee.save(update_fields=["workspace_etag", "workspace_synced_at", "utime"])
    return employee


async def update_employee_skills(employee: CommandAgent, skill_keys: list[str]) -> CommandAgent:
    role = await employee.role
    if role is None:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_ROLE_NOT_FOUND)
    skills = await get_visible_skills(employee.tenant_id, skill_keys)
    employee.skill_keys = [skill.skill_key for skill in skills]
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
