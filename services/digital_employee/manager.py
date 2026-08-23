# coding=utf-8
import shutil
import uuid
from datetime import datetime

from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.maintenance import DEFAULT_TENANT_ID
from libs.error_code.exception import CustomError
from models.command_center import CommandAgent, CommandTask
from models.digital_employee import DigitalEmployeeRole, DigitalEmployeeSkill
from settings import TZ

from .aioe_credential_manager import create_employee_api_account
from .lion_agent_client import LionAgentClient
from .prompt_builder import render_workspace_files, workspace_etag
from .rbac import require_existing_platform_role
from .skill_storage import relative_skill_dir, skill_storage_root, tenant_skill_root


BUILTIN_TENANT_ID = "__builtin__"
TERMINAL_TASK_STATUS = {"completed", "failed", "cancelled"}
_UNSET = object()


async def _require_default_tenant_config(config_id: str | None) -> None:
    """员工绑定校验（SPEC §5.6 / 契约 §4 / D14）。

    模型配置已收敛到 default 租户，员工只能绑其中的配置。
    拒绝非 default 租户的 config_id——租户迁移完成后这些记录会被清理，
    但在存量清理前必须防止绑定脏数据（对照 D14 的四条行为）。
    """
    if config_id is None:
        return
    from models import LlmModelConfig

    record = await LlmModelConfig.get_or_none(id=config_id, deleted=False)
    # D14-1：不存在 / 已软删 → 拒绝（config_id 是指针，不能悬空）
    if record is None:
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)
    # D14-2：存在但非 default 租户 → 拒绝（不能绑脏数据）
    if record.tenant_id != DEFAULT_TENANT_ID:
        raise CustomError(ErrorCode.LLM_CONFIG_NOT_CONFIGURED)
    # D14-3 / D14-4：enabled=False 的配置**允许绑**——管理员可能先停用、
    # 再批量改绑、最后删配置。禁用不代表不可用，只是 resolve_llm 不取。


def _visible_condition(tenant_id: str):
    return Q(deleted=False) & (Q(tenant_id=tenant_id) | Q(is_builtin=True))


def skill_candidate_condition(tenant_id: str):
    """Load tenant rows before deciding whether a builtin baseline is visible."""
    return Q(tenant_id=tenant_id) | Q(
        tenant_id=BUILTIN_TENANT_ID,
        is_builtin=True,
        deleted=False,
    )


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


def prefer_visible_skills(records: list, tenant_id: str) -> list:
    """Apply tenant preference before filtering tenant-scoped tombstones."""
    preferred = prefer_tenant_records(records, tenant_id, "skill_key")
    return [skill for skill in preferred if not bool(getattr(skill, "deleted", False))]


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
    skills = await DigitalEmployeeSkill.filter(skill_candidate_condition(tenant_id), skill_key__in=requested_keys).all()
    preferred = prefer_visible_skills(skills, tenant_id)
    if enabled is not None:
        preferred = [skill for skill in preferred if bool(skill.enabled) is enabled]
    skills_by_key = {skill.skill_key: skill for skill in preferred}
    if set(skills_by_key) != set(requested_keys):
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_SKILL_NOT_FOUND)
    return [skills_by_key[key] for key in requested_keys]


def derive_capabilities(role: DigitalEmployeeRole, skills: list[DigitalEmployeeSkill]) -> list[str]:
    """能力集 = 岗位声明 + 岗位 key + 持有技能 key。技能侧不再提供细粒度标签（SKILL.md 严守规范、无 AIOE 私有字段），
    细粒度标签统一在 role.yaml 的 capabilities 中声明。
    aioe_cli 是工具不是技能，能否调用由 RBAC 角色决定，不进能力集。
    """
    capabilities = set((role.meta or {}).get("capabilities") or [])
    capabilities.add(role.role_key)
    for skill in skills:
        capabilities.add(skill.skill_key)
    return sorted(str(item) for item in capabilities if item)


def persisted_employee_skill_keys(skill_keys: list[str] | None) -> list[str]:
    return [key for key in dict.fromkeys(str(item) for item in (skill_keys or []) if item) if key]


def validate_knowledge_binding(
    space_keys, write_space: str | None
) -> tuple[list[str], str | None]:
    """校验知识空间绑定，返回归一化结果。

    可写空间必须在可见空间内：否则员工会有一个写得进去
    但自己读不到的空间，行为诡异且难排查。
    """
    if space_keys is None:
        space_keys = []
    if not isinstance(space_keys, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    normalized = [str(key) for key in space_keys if str(key).strip()]

    if write_space:
        if str(write_space) not in normalized:
            raise CustomError(ErrorCode.PARAM_ERROR)
        return normalized, str(write_space)

    return normalized, None


async def create_employee(
    tenant_id: str,
    role_key: str,
    name: str,
    skill_keys: list[str] | None = None,
    max_concurrent: int = 1,
    agent_key: str | None = None,
    rbac_role_id: str | None = None,
    llm_config_id: str | None = None,
    knowledge_space_keys: list[str] | None = None,
    knowledge_write_space: str | None = None,
) -> CommandAgent:
    from services.llm_config.manager import assert_bindable_config

    role = await get_visible_role(tenant_id, role_key)
    selected_rbac_role_id = rbac_role_id
    await require_existing_platform_role(selected_rbac_role_id, tenant_id)
    await assert_bindable_config(llm_config_id)
    normalized_space_keys, normalized_write_space = validate_knowledge_binding(
        knowledge_space_keys, knowledge_write_space
    )
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
        knowledge_space_keys=normalized_space_keys,
        knowledge_write_space=normalized_write_space,
        rbac_role_id=selected_rbac_role_id,
        llm_config_id=llm_config_id or None,
        resource_scope={},
    )
    await sync_employee_workspace(employee, role=role, skills=skills)
    await create_employee_api_account(employee)
    return employee


async def update_employee(
    employee: CommandAgent,
    *,
    name=_UNSET,
    rbac_role_id=_UNSET,
    max_concurrent=_UNSET,
    llm_config_id=_UNSET,
    knowledge_space_keys=_UNSET,
    knowledge_write_space=_UNSET,
) -> CommandAgent:
    if rbac_role_id is not _UNSET:
        await require_existing_platform_role(rbac_role_id, employee.tenant_id)
    if name is not _UNSET:
        employee.name = name
    if rbac_role_id is not _UNSET:
        employee.rbac_role_id = rbac_role_id
    if max_concurrent is not _UNSET:
        employee.max_concurrent = max(1, int(max_concurrent or 1))
    if llm_config_id is not _UNSET:
        from services.llm_config.manager import assert_bindable_config
        await assert_bindable_config(llm_config_id)
        employee.llm_config_id = llm_config_id or None
    if knowledge_space_keys is not _UNSET or knowledge_write_space is not _UNSET:
        normalized_space_keys, normalized_write_space = validate_knowledge_binding(
            getattr(employee, "knowledge_space_keys", None)
            if knowledge_space_keys is _UNSET
            else knowledge_space_keys,
            getattr(employee, "knowledge_write_space", None)
            if knowledge_write_space is _UNSET
            else knowledge_write_space,
        )
        employee.knowledge_space_keys = normalized_space_keys
        employee.knowledge_write_space = normalized_write_space
    employee.workspace_etag = None
    await employee.save()
    return await sync_employee_workspace(employee)


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
        skill_versions={skill.skill_key: skill.version for skill in skills},
        tenant_id=employee.tenant_id,
        employee_id=employee.id,
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


async def clone_builtin_skill_to_tenant(skill: DigitalEmployeeSkill, tenant_id: str) -> DigitalEmployeeSkill:
    """把内置技能整目录复制到租户根下，返回租户自己的技能记录。内置目录本身不改动，保持只读基线。"""
    source = (skill_storage_root() / skill.skill_dir).resolve()
    target = tenant_skill_root(tenant_id) / skill.skill_key
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    cloned, _ = await DigitalEmployeeSkill.update_or_create(
        tenant_id=tenant_id,
        skill_key=skill.skill_key,
        defaults={
            "name": skill.name,
            "description": skill.description,
            "skill_dir": relative_skill_dir(target),
            "version": skill.version,
            "is_builtin": False,
            "enabled": True,
            "deleted": False,
        },
    )
    return cloned
