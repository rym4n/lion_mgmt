# coding=utf-8
from sanic import Blueprint, Request, response
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions, requires_permission
from models.command_center import CommandAgent, CommandTask, CommandTaskRun
from models.digital_employee import DigitalEmployeeRole, DigitalEmployeeSkill
from models.employee_report import EmployeeReport
from models.report import ReportDocument
from services.command_center.manager import create_command_task
from services.command_center.realtime import publish_command_center_update

from .aioe_credential_manager import delete_employee_api_account
from .cognee_credential_manager import delete_employee_memory_dataset, get_or_refresh_jwt
from .employee_report import compose
from .lion_agent_client import LionAgentClient
from .manager import (
    BUILTIN_TENANT_ID,
    create_employee,
    ensure_employee_can_delete,
    effective_employee_skill_keys,
    get_visible_role,
    get_visible_skills,
    prefer_tenant_records,
    sync_employee_workspace,
    update_employee_skills,
    upgrade_role_for_existing_employees,
)
from .package_format import PackageFormatError, UnsupportedPackageTypeError, inspect_package
from .package_importer import export_package_for_tenant, import_package, normalize_role_prompt_fields, require_role_identity
from .rbac import require_existing_platform_role
from .runtime_callbacks import fetch_runtime_transcript, save_runtime_report, save_runtime_transcript

digital_employee_bp = Blueprint("digital_employee", url_prefix="/digital_employee")
__all__ = ["digital_employee_bp"]
_ROLE_UNSET = object()


def _json(request: Request) -> dict:
    return request.json or {}


def _tenant_id(request: Request) -> str:
    return getattr(request.ctx, "session", {}).get("tenant_id")


def _require_tenant_id(tenant_id: str | None) -> str:
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return tenant_id


def _operator(request: Request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("id")
    return getattr(account, "id", None)


def _visible_filter(tenant_id: str):
    return Q(deleted=False) & (Q(tenant_id=tenant_id) | Q(is_builtin=True))


def _page(data: dict) -> tuple[int, int, int]:
    page = max(1, int(data.get("page", 1)))
    size = max(1, min(100, int(data.get("size", 20))))
    return page, size, (page - 1) * size


async def _require_save_permission(request: Request, exists: bool, create_permission: str, update_permission: str) -> None:
    await check_permissions(request, update_permission if exists else create_permission)


def _package_type_or_error(file_bytes: bytes) -> str:
    try:
        package_type, _ = inspect_package(file_bytes)
    except UnsupportedPackageTypeError as exc:
        raise CustomError(ErrorCode.PACKAGE_TYPE_UNSUPPORTED) from exc
    except PackageFormatError as exc:
        raise CustomError(ErrorCode.PACKAGE_INVALID) from exc
    return package_type


async def _require_package_import_permission(request: Request, file_bytes: bytes) -> None:
    permission = "digital_employee_skill:c" if _package_type_or_error(file_bytes) == "skill" else "digital_employee:c"
    await check_permissions(request, permission)


async def _require_package_export_permission(request: Request, data: dict) -> None:
    permission = "digital_employee_skill:r" if data.get("skill_key") and not data.get("role_key") else "digital_employee:r"
    await check_permissions(request, permission)


async def _require_package_delete_permission(request: Request, data: dict) -> None:
    permission = "digital_employee_skill:d" if data.get("skill_key") and not data.get("role_key") else "digital_employee:d"
    await check_permissions(request, permission)


async def _employee_or_404(tenant_id: str, employee_id: str) -> CommandAgent:
    if not employee_id:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_EMPLOYEE_NOT_FOUND)
    employee = await CommandAgent.filter(Q(id=employee_id) | Q(agent_key=employee_id), tenant_id=tenant_id, deleted=False).first()
    if not employee or not employee.role_key:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_EMPLOYEE_NOT_FOUND)
    return employee


async def _tenant_skill_or_404(tenant_id: str, skill_key: str) -> DigitalEmployeeSkill:
    skill = await DigitalEmployeeSkill.get_or_none(
        tenant_id=tenant_id,
        skill_key=skill_key,
        deleted=False,
    )
    if not skill:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_SKILL_NOT_FOUND)
    return skill


async def _visible_roles_by_key(tenant_id: str, role_keys: list[str]) -> dict[str, DigitalEmployeeRole]:
    keys = [key for key in dict.fromkeys(role_keys) if key]
    if not keys:
        return {}
    roles = await DigitalEmployeeRole.filter(_visible_filter(tenant_id), role_key__in=keys, enabled=True).all()
    return {role.role_key: role for role in prefer_tenant_records(roles, tenant_id, "role_key")}


async def _employee_payload(employee: CommandAgent, role=_ROLE_UNSET) -> dict:
    payload = employee.json
    payload.pop("description", None)
    resolved_role = None
    if role is _ROLE_UNSET:
        if employee.role_key:
            resolved_role = await get_visible_role(employee.tenant_id, employee.role_key)
    else:
        resolved_role = role
    payload["role"] = resolved_role.json if resolved_role else None
    return payload


def _report_summary(report: EmployeeReport | None) -> dict | None:
    if not report:
        return None
    return {
        "id": report.id,
        "ctime": report.json.get("ctime"),
        "utime": report.json.get("utime"),
        "title": report.title,
        "severity": report.severity,
        "conclusion": report.conclusion,
        "auto_generated": report.auto_generated,
    }


async def _task_payloads_with_reports(tenant_id: str, tasks: list[CommandTask]) -> list[dict]:
    payloads = [task.json for task in tasks]
    if not tasks:
        return payloads
    reports = await EmployeeReport.filter(
        tenant_id=tenant_id,
        task_id__in=[task.id for task in tasks],
        deleted=False,
    ).order_by("-utime").all()
    report_by_task_id: dict[str, EmployeeReport] = {}
    for report in reports:
        report_by_task_id.setdefault(report.task_id, report)
    for payload in payloads:
        payload["employee_report"] = _report_summary(report_by_task_id.get(payload["id"]))
    return payloads


async def _report_detail_payload(report: EmployeeReport) -> dict:
    payload = report.json
    task = await report.task
    payload["task"] = task.json if task else None
    payload["room_id"] = getattr(task, "room_id", None)
    payload["runtime_transcript"] = await fetch_runtime_transcript(report.tenant_id, report.task_id)
    payload["report_document"] = None
    if report.report_document_id:
        document = await ReportDocument.get_or_none(
            id=report.report_document_id,
            tenant_id=report.tenant_id,
            deleted=False,
        )
        payload["report_document"] = document.json if document else None
    return payload


@digital_employee_bp.post("/employee/create")
@requires_permission("digital_employee:c")
async def employee_create(request: Request):
    data = _json(request)
    if not data.get("role_key") or not data.get("name"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    employee = await create_employee(
        tenant_id=_tenant_id(request),
        role_key=data.get("role_key"),
        name=data.get("name"),
        skill_keys=data.get("skill_keys"),
        max_concurrent=data.get("max_concurrent", 1),
        agent_key=data.get("agent_key"),
        rbac_role_id=data.get("rbac_role_id"),
    )
    await publish_command_center_update(employee.tenant_id, "employee_created", employee.id)
    return response_ok(await _employee_payload(employee))


@digital_employee_bp.post("/employee/list")
@requires_permission("digital_employee:r")
async def employee_list(request: Request):
    data = _json(request)
    page, size, offset = _page(data)
    condition = Q(tenant_id=_tenant_id(request), deleted=False) & ~Q(role_key=None)
    if data.get("role_key"):
        condition &= Q(role_key=data["role_key"])
    if data.get("status"):
        condition &= Q(status=data["status"])
    if data.get("keyword"):
        keyword = data["keyword"]
        condition &= Q(name__icontains=keyword) | Q(agent_key__icontains=keyword)
    queryset = CommandAgent.filter(condition)
    total = await queryset.count()
    employees = await queryset.order_by("-utime").offset(offset).limit(size).all()
    roles_by_key = await _visible_roles_by_key(_tenant_id(request), [item.role_key for item in employees])
    return response_ok(
        {
            "list": [await _employee_payload(item, role=roles_by_key.get(item.role_key)) for item in employees],
            "total": total,
            "page": page,
            "size": size,
        }
    )


@digital_employee_bp.post("/employee/detail")
@requires_permission("digital_employee:r")
async def employee_detail(request: Request):
    employee = await _employee_or_404(_tenant_id(request), _json(request).get("employee_id"))
    role = await get_visible_role(employee.tenant_id, employee.role_key)
    skills = await get_visible_skills(
        employee.tenant_id,
        effective_employee_skill_keys(employee.skill_keys or []),
        enabled=None,
    )
    tasks = await CommandTask.filter(tenant_id=employee.tenant_id, assigned_agent_id=employee.id, deleted=False).order_by("-ctime").limit(10).all()
    payload = await _employee_payload(employee, role=role)
    payload.update(
        {
            "role": role.json if role else None,
            "skills": [skill.json for skill in skills],
            "recent_tasks": await _task_payloads_with_reports(employee.tenant_id, tasks),
            "token_usage": await _employee_token_usage(employee),
        }
    )
    return response_ok(payload)


async def _employee_token_usage(employee: CommandAgent) -> dict:
    """聚合该数字员工累计的大模型 token 消耗。"""
    from services.command_center.summary import aggregate_usage

    runs = await CommandTaskRun.filter(tenant_id=employee.tenant_id, employee_id=employee.id, deleted=False).all()
    return aggregate_usage(runs)


@digital_employee_bp.post("/employee/update")
@requires_permission("digital_employee:u")
async def employee_update(request: Request):
    data = _json(request)
    tenant_id = _tenant_id(request)
    employee = await _employee_or_404(tenant_id, data.get("employee_id"))
    if "rbac_role_id" in data:
        await require_existing_platform_role(data.get("rbac_role_id"), tenant_id)
    for field in ["name", "rbac_role_id"]:
        if field in data:
            setattr(employee, field, data[field])
    if "max_concurrent" in data:
        employee.max_concurrent = max(1, int(data["max_concurrent"] or 1))
    employee.workspace_etag = None
    await employee.save()
    await sync_employee_workspace(employee)
    await publish_command_center_update(employee.tenant_id, "employee_updated", employee.id)
    return response_ok(await _employee_payload(employee))


@digital_employee_bp.post("/employee/toggle")
@requires_permission("digital_employee:u")
async def employee_toggle(request: Request):
    data = _json(request)
    employee = await _employee_or_404(_tenant_id(request), data.get("employee_id"))
    employee.enabled = bool(data.get("enabled"))
    await employee.save(update_fields=["enabled", "utime"])
    await publish_command_center_update(employee.tenant_id, "employee_updated", employee.id)
    return response_ok(await _employee_payload(employee))


@digital_employee_bp.post("/employee/delete")
@requires_permission("digital_employee:d")
async def employee_delete(request: Request):
    employee = await _employee_or_404(_tenant_id(request), _json(request).get("employee_id"))
    await ensure_employee_can_delete(employee)
    employee.deleted = True
    employee.status = "offline"
    employee.load = 0
    employee.current_task_id = None
    await employee.save()
    await LionAgentClient().delete_workspace(employee.id)
    await delete_employee_memory_dataset(employee.tenant_id, employee.id)
    await delete_employee_api_account(employee)
    await publish_command_center_update(employee.tenant_id, "employee_deleted", employee.id)
    return response_ok()


@digital_employee_bp.post("/employee/skills/update")
@requires_permission("digital_employee:u")
async def employee_skills_update(request: Request):
    data = _json(request)
    employee = await _employee_or_404(_tenant_id(request), data.get("employee_id"))
    employee = await update_employee_skills(employee, data.get("skill_keys") or [])
    await publish_command_center_update(employee.tenant_id, "employee_skills_updated", employee.id)
    return response_ok(await _employee_payload(employee))


@digital_employee_bp.post("/employee/scope/update")
@requires_permission("digital_employee:u")
async def employee_scope_update(request: Request):
    data = _json(request)
    employee = await _employee_or_404(_tenant_id(request), data.get("employee_id"))
    employee.resource_scope = {}
    employee.workspace_etag = None
    await employee.save(update_fields=["resource_scope", "workspace_etag", "utime"])
    await sync_employee_workspace(employee)
    await publish_command_center_update(employee.tenant_id, "employee_scope_updated", employee.id)
    return response_ok(await _employee_payload(employee))


@digital_employee_bp.post("/employee/repair")
@requires_permission("digital_employee:u")
async def employee_repair(request: Request):
    raise CustomError(ErrorCode.PARAM_ERROR)


async def _repair_employee_workspace(tenant_id: str, employee_id: str):
    employee = await _employee_or_404(tenant_id, employee_id)
    employee.workspace_etag = None
    await employee.save(update_fields=["workspace_etag", "utime"])
    await sync_employee_workspace(employee, force=True)
    return employee


@digital_employee_bp.post("/employee/dispatch")
@requires_permission("command_center:c")
async def employee_dispatch(request: Request):
    task = await _dispatch_employee_task(request, _tenant_id(request), getattr(request.ctx, "session", {}).get("account_info"))
    return response_ok(task.json)


async def _dispatch_employee_task(request: Request, tenant_id: str, account_info):
    data = _json(request)
    employee_key = data.get("employee_id") or data.get("employee_agent_key") or data.get("agent_id") or data.get("requested_agent_id")
    employee = None
    if employee_key:
        employee = await _employee_or_404(tenant_id, employee_key)
    task_payload = dict(data.get("payload") or {})
    task_payload["_dispatch_scope"] = "digital_employee"
    command_task_data = {
        "description": data.get("description"),
        "source": data.get("source", "manual"),
        "priority": data.get("priority", 3),
        "payload": task_payload,
        "agent_id": employee.agent_key if employee else data.get("agent_id") or data.get("requested_agent_id"),
        "room_id": data.get("room_id"),
        "required_capabilities": data.get("required_capabilities") or (employee.capabilities if employee else []),
    }
    if data.get("title"):
        command_task_data["title"] = data["title"]
    return await create_command_task(tenant_id, account_info, command_task_data)


@digital_employee_bp.post("/employee/tasks/list")
@requires_permission("command_center:r")
async def employee_tasks_list(request: Request):
    return response_ok(await _employee_tasks_payload(_tenant_id(request), _json(request)))


async def _employee_tasks_payload(tenant_id: str, data: dict) -> dict:
    page, size, offset = _page(data)
    employee = await _employee_or_404(tenant_id, data.get("employee_id"))
    queryset = CommandTask.filter(tenant_id=employee.tenant_id, assigned_agent_id=employee.id, deleted=False)
    total = await queryset.count()
    tasks = await queryset.order_by("-ctime").offset(offset).limit(size).all()
    return {"list": await _task_payloads_with_reports(employee.tenant_id, tasks), "total": total, "page": page, "size": size}


@digital_employee_bp.post("/role/list")
@requires_permission("digital_employee:r")
async def role_list(request: Request):
    data = _json(request)
    page, size, offset = _page(data)
    condition = _visible_filter(_tenant_id(request))
    if data.get("keyword"):
        keyword = data["keyword"]
        condition &= Q(name__icontains=keyword) | Q(role_key__icontains=keyword)
    queryset = DigitalEmployeeRole.filter(condition)
    roles = await queryset.order_by("-is_builtin", "-utime").all()
    roles = prefer_tenant_records(roles, _tenant_id(request), "role_key")
    total = len(roles)
    roles = roles[offset : offset + size]
    return response_ok({"list": [role.json for role in roles], "total": total, "page": page, "size": size})


@digital_employee_bp.post("/role/detail")
@requires_permission("digital_employee:r")
async def role_detail(request: Request):
    role = await get_visible_role(_tenant_id(request), _json(request).get("role_key"))
    return response_ok(role.json)


@digital_employee_bp.post("/role/save")
async def role_save(request: Request):
    data = normalize_role_prompt_fields(_json(request))
    tenant_id = _tenant_id(request)
    role_key = str(data.get("role_key") or data.get("key") or "").strip()
    if not role_key or not data.get("name"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    require_role_identity(data)
    exists = await DigitalEmployeeRole.filter(tenant_id=tenant_id, role_key=role_key, deleted=False).exists()
    await _require_save_permission(request, exists, "digital_employee:c", "digital_employee:u")
    role, _ = await DigitalEmployeeRole.update_or_create(
        tenant_id=tenant_id,
        role_key=role_key,
        defaults={
            "name": data["name"],
            "template_dir": data.get("template_dir") or "",
            "default_skill_keys": data.get("default_skill_keys") or data.get("default_skills") or [],
            "default_rbac_role_id": None,
            "license_required": bool(data.get("license_required", False)),
            "version": int(data.get("version") or 1),
            "enabled": bool(data.get("enabled", True)),
            "meta": data,
            "deleted": False,
        },
    )
    await publish_command_center_update(tenant_id, "role_saved", role.role_key)
    return response_ok(role.json)


@digital_employee_bp.post("/role/delete")
@requires_permission("digital_employee:d")
async def role_delete(request: Request):
    role = await get_visible_role(_tenant_id(request), _json(request).get("role_key"))
    if await CommandAgent.filter(tenant_id=_tenant_id(request), role_key=role.role_key, deleted=False).count():
        raise CustomError(ErrorCode.ROLE_IN_USE_BY_EMPLOYEES)
    role.deleted = True
    await role.save(update_fields=["deleted", "utime"])
    await publish_command_center_update(role.tenant_id, "role_deleted", role.role_key)
    return response_ok()


@digital_employee_bp.post("/role/upgrade-employees")
@requires_permission("digital_employee:u")
async def role_upgrade_employees(request: Request):
    role = await get_visible_role(_tenant_id(request), _json(request).get("role_key"))
    count = await upgrade_role_for_existing_employees(role)
    return response_ok({"count": count})


@digital_employee_bp.post("/skill/list")
@requires_permission("digital_employee_skill:r", "digital_employee:r")
async def skill_list(request: Request):
    data = _json(request)
    page, size, offset = _page(data)
    condition = _visible_filter(_tenant_id(request))
    if data.get("keyword"):
        keyword = data["keyword"]
        condition &= Q(name__icontains=keyword) | Q(skill_key__icontains=keyword) | Q(description__icontains=keyword)
    queryset = DigitalEmployeeSkill.filter(condition)
    skills = await queryset.order_by("-is_builtin", "-utime").all()
    skills = prefer_tenant_records(skills, _tenant_id(request), "skill_key")
    total = len(skills)
    skills = skills[offset : offset + size]
    return response_ok({"list": [skill.json for skill in skills], "total": total, "page": page, "size": size})


@digital_employee_bp.post("/skill/detail")
@requires_permission("digital_employee_skill:r")
async def skill_detail(request: Request):
    skills = await get_visible_skills(_tenant_id(request), [_json(request).get("skill_key")], enabled=None)
    return response_ok(skills[0].json)


@digital_employee_bp.post("/skill/save")
async def skill_save(request: Request):
    data = _json(request)
    tenant_id = _tenant_id(request)
    skill_key = str(data.get("skill_key") or data.get("key") or "").strip()
    if not skill_key or not data.get("name"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    visible = await DigitalEmployeeSkill.filter(_visible_filter(tenant_id), skill_key=skill_key).all()
    existing = next(iter(prefer_tenant_records(visible, tenant_id, "skill_key")), None)
    await _require_save_permission(request, existing is not None, "digital_employee_skill:c", "digital_employee_skill:u")
    content_changed = not existing or any(
        (
            existing.name != data["name"],
            existing.description != data.get("description"),
            existing.skill_dir != (data.get("skill_dir") or ""),
            existing.bindings != (data.get("bindings") or {}),
            (existing.meta or {}).get("files") != data.get("files"),
        )
    )
    skill, _ = await DigitalEmployeeSkill.update_or_create(
        tenant_id=tenant_id,
        skill_key=skill_key,
        defaults={
            "name": data["name"],
            "description": data.get("description"),
            "skill_dir": data.get("skill_dir") or "",
            "bindings": data.get("bindings") or {},
            "require_approval": existing.require_approval if existing else False,
            "version": existing.version + 1 if existing and content_changed else (existing.version if existing else 1),
            "enabled": existing.enabled if existing else True,
            "meta": data,
            "deleted": False,
        },
    )
    return response_ok(skill.json)


@digital_employee_bp.post("/skill/toggle")
@requires_permission("digital_employee_skill:u")
async def skill_toggle(request: Request):
    data = _json(request)
    tenant_id = _tenant_id(request)
    skills = await get_visible_skills(tenant_id, [data.get("skill_key")], enabled=None)
    skill = skills[0]
    if skill.tenant_id != tenant_id:
        skill, _ = await DigitalEmployeeSkill.update_or_create(
            tenant_id=tenant_id,
            skill_key=skill.skill_key,
            defaults={
                "name": skill.name,
                "description": skill.description,
                "skill_dir": skill.skill_dir,
                "bindings": skill.bindings,
                "require_approval": skill.require_approval,
                "version": skill.version,
                "enabled": bool(data.get("enabled")),
                "is_builtin": False,
                "meta": skill.meta,
                "deleted": False,
            },
        )
    else:
        skill.enabled = bool(data.get("enabled"))
        await skill.save(update_fields=["enabled", "utime"])
    return response_ok(skill.json)


@digital_employee_bp.post("/skill/delete")
@requires_permission("digital_employee_skill:d")
async def skill_delete(request: Request):
    tenant_id = _tenant_id(request)
    skill = await _tenant_skill_or_404(tenant_id, _json(request).get("skill_key"))
    if await CommandAgent.filter(tenant_id=tenant_id, skill_keys__contains=[skill.skill_key], deleted=False).count():
        raise CustomError(ErrorCode.SKILL_IN_USE_BY_EMPLOYEES)
    skill.deleted = True
    await skill.save(update_fields=["deleted", "utime"])
    return response_ok()


@digital_employee_bp.post("/package/import")
async def package_import(request: Request):
    uploaded = request.files.get("file") if request.files else None
    if uploaded:
        file_bytes = uploaded.body
    elif request.body:
        file_bytes = request.body
    else:
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    await _require_package_import_permission(request, file_bytes)
    is_builtin_value = request.form.get("is_builtin") if request.form else False
    is_builtin = str(is_builtin_value).lower() in {"1", "true", "yes", "on"}
    result = await import_package(file_bytes, _tenant_id(request), is_builtin, _operator(request), request=request)
    return response_ok(result.json)


@digital_employee_bp.post("/package/export")
async def package_export(request: Request):
    data = _json(request)
    await _require_package_export_permission(request, data)
    data_bytes = await export_package_for_tenant(
        _tenant_id(request),
        data.get("role_key"),
        data.get("skill_key"),
        bool(data.get("bundle", False)),
    )
    return response.raw(data_bytes, content_type="application/zip")


@digital_employee_bp.post("/package/list")
@requires_permission("digital_employee:r")
async def package_list(request: Request):
    tenant_id = _tenant_id(request)
    roles = await DigitalEmployeeRole.filter(_visible_filter(tenant_id)).all()
    skills = await DigitalEmployeeSkill.filter(_visible_filter(tenant_id)).all()
    roles = prefer_tenant_records(roles, tenant_id, "role_key")
    skills = prefer_tenant_records(skills, tenant_id, "skill_key")
    return response_ok({"roles": [role.json for role in roles], "skills": [skill.json for skill in skills]})


@digital_employee_bp.post("/package/delete")
async def package_delete(request: Request):
    data = _json(request)
    tenant_id = _tenant_id(request)
    await _require_package_delete_permission(request, data)
    if data.get("role_key"):
        role = await get_visible_role(tenant_id, data["role_key"])
        if await CommandAgent.filter(tenant_id=tenant_id, role_key=role.role_key, deleted=False).count():
            raise CustomError(ErrorCode.ROLE_IN_USE_BY_EMPLOYEES)
        role.deleted = True
        await role.save(update_fields=["deleted", "utime"])
    elif data.get("skill_key"):
        skill = await _tenant_skill_or_404(tenant_id, data["skill_key"])
        if await CommandAgent.filter(tenant_id=tenant_id, skill_keys__contains=[skill.skill_key], deleted=False).count():
            raise CustomError(ErrorCode.SKILL_IN_USE_BY_EMPLOYEES)
        skill.deleted = True
        await skill.save(update_fields=["deleted", "utime"])
    else:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok()


@digital_employee_bp.post("/report/list")
@requires_permission("digital_employee:r")
async def report_list(request: Request):
    data = _json(request)
    page, size, offset = _page(data)
    condition = Q(tenant_id=_tenant_id(request), deleted=False)
    for field in ["role_key", "severity", "employee_id", "task_id"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    queryset = EmployeeReport.filter(condition)
    total = await queryset.count()
    reports = await queryset.order_by("-ctime").offset(offset).limit(size).all()
    return response_ok({"list": [report.json for report in reports], "total": total, "page": page, "size": size})


@digital_employee_bp.post("/report/detail")
@requires_permission("digital_employee:r")
async def report_detail(request: Request):
    report = await EmployeeReport.get_or_none(id=_json(request).get("report_id"), tenant_id=_tenant_id(request), deleted=False)
    if not report:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(await _report_detail_payload(report))


@digital_employee_bp.post("/report/edit")
@requires_permission("digital_employee:u")
async def report_edit(request: Request):
    data = _json(request)
    report = await EmployeeReport.get_or_none(id=data.get("report_id"), tenant_id=_tenant_id(request), deleted=False)
    if not report:
        raise CustomError(ErrorCode.NOT_FOUND)
    for field in ["title", "severity", "observation", "analysis", "actions", "conclusion", "evidence_urls"]:
        if field in data:
            setattr(report, field, data[field])
    report.auto_generated = False
    await report.save()
    return response_ok(report.json)


@digital_employee_bp.post("/report/regenerate")
@requires_permission("digital_employee:u")
async def report_regenerate(request: Request):
    report = await EmployeeReport.get_or_none(id=_json(request).get("report_id"), tenant_id=_tenant_id(request), deleted=False)
    if not report:
        raise CustomError(ErrorCode.NOT_FOUND)
    task = await report.task
    transcript = await fetch_runtime_transcript(report.tenant_id, task.id)
    report = await compose(task, transcript.get("actions_draft") or transcript.get("actions") or report.actions)
    return response_ok(report.json)


def _check_internal(request: Request):
    from settings import LION_AGENT_INTERNAL_TOKEN

    token = request.headers.get("X-Internal-Token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if LION_AGENT_INTERNAL_TOKEN and token != LION_AGENT_INTERNAL_TOKEN:
        raise CustomError(ErrorCode.UNAUTH)


@digital_employee_bp.post("/internal/employee/dispatch")
async def internal_employee_dispatch(request: Request):
    _check_internal(request)
    data = _json(request)
    task = await _dispatch_employee_task(request, _require_tenant_id(data.get("tenant_id") or _tenant_id(request)), None)
    return response_ok(task.json)


@digital_employee_bp.post("/internal/employee/tasks/list")
async def internal_employee_tasks_list(request: Request):
    _check_internal(request)
    data = _json(request)
    return response_ok(await _employee_tasks_payload(_require_tenant_id(data.get("tenant_id") or _tenant_id(request)), data))


@digital_employee_bp.post("/internal/employee/repair")
async def internal_employee_repair(request: Request):
    _check_internal(request)
    data = _json(request)
    employee = await _repair_employee_workspace(
        _require_tenant_id(data.get("tenant_id") or _tenant_id(request)),
        data.get("employee_id"),
    )
    return response_ok(employee.json)


@digital_employee_bp.post("/internal/task/detail")
async def internal_task_detail(request: Request):
    _check_internal(request)
    data = _json(request)
    tenant_id = _require_tenant_id(data.get("tenant_id") or _tenant_id(request))
    task = await CommandTask.get_or_none(id=data.get("task_id") or data.get("id"), tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok({"task": task.json})


@digital_employee_bp.post("/runtime/transcript/save")
async def runtime_transcript_save(request: Request):
    _check_internal(request)
    data = _json(request)
    tenant_id = _require_tenant_id(data.get("tenant_id") or _tenant_id(request))
    return response_ok(await save_runtime_transcript(tenant_id, data))


@digital_employee_bp.post("/runtime/transcript/fetch")
async def runtime_transcript_fetch(request: Request):
    _check_internal(request)
    data = _json(request)
    tenant_id = _require_tenant_id(data.get("tenant_id") or _tenant_id(request))
    return response_ok(await fetch_runtime_transcript(tenant_id, data.get("task_id") or data.get("id")))


@digital_employee_bp.post("/runtime/report/save")
async def runtime_report_save(request: Request):
    _check_internal(request)
    data = _json(request)
    tenant_id = _require_tenant_id(data.get("tenant_id") or _tenant_id(request))
    report = await save_runtime_report(tenant_id, data)
    return response_ok(report.json)


@digital_employee_bp.post("/runtime/cognee/refresh-jwt")
async def runtime_cognee_refresh_jwt(request: Request):
    _check_internal(request)
    data = _json(request)
    return response_ok({"jwt": await get_or_refresh_jwt(data.get("tenant_id") or _tenant_id(request) or BUILTIN_TENANT_ID, force=True)})
