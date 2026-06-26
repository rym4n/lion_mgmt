# coding=utf-8
from sanic import Blueprint, Request, response
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions, requires_permission
from models.command_center import CommandAgent, CommandTask
from models.digital_employee import DigitalEmployeeRole, DigitalEmployeeSkill
from models.employee_report import EmployeeReport
from models.report import ReportDocument
from services.command_center.manager import create_command_task

from .cognee_credential_manager import delete_employee_memory_dataset, get_or_refresh_jwt
from .employee_report import compose
from .lion_agent_client import LionAgentClient
from .manager import (
    BUILTIN_TENANT_ID,
    create_employee,
    ensure_employee_can_delete,
    get_visible_role,
    get_visible_skills,
    sync_employee_workspace,
    update_employee_skills,
    upgrade_role_for_existing_employees,
)
from .package_importer import export_package, import_package
from .runtime_callbacks import fetch_runtime_transcript, save_runtime_report, save_runtime_transcript

digital_employee_bp = Blueprint("digital_employee", url_prefix="/digital_employee")
__all__ = ["digital_employee_bp"]


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


async def _employee_or_404(tenant_id: str, employee_id: str) -> CommandAgent:
    if not employee_id:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_EMPLOYEE_NOT_FOUND)
    employee = await CommandAgent.filter(Q(id=employee_id) | Q(agent_key=employee_id), tenant_id=tenant_id, deleted=False).first()
    if not employee or not employee.role_key:
        raise CustomError(ErrorCode.DIGITAL_EMPLOYEE_EMPLOYEE_NOT_FOUND)
    return employee


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
        resource_scope=data.get("resource_scope") or {},
        description=data.get("description"),
        max_concurrent=data.get("max_concurrent", 1),
        agent_key=data.get("agent_key"),
    )
    return response_ok(employee.json)


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
        condition &= Q(name__icontains=keyword) | Q(agent_key__icontains=keyword) | Q(description__icontains=keyword)
    queryset = CommandAgent.filter(condition)
    total = await queryset.count()
    employees = await queryset.order_by("-utime").offset(offset).limit(size).all()
    return response_ok({"list": [item.json for item in employees], "total": total, "page": page, "size": size})


@digital_employee_bp.post("/employee/detail")
@requires_permission("digital_employee:r")
async def employee_detail(request: Request):
    employee = await _employee_or_404(_tenant_id(request), _json(request).get("employee_id"))
    role = await employee.role
    skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
    tasks = await CommandTask.filter(tenant_id=employee.tenant_id, assigned_agent_id=employee.id, deleted=False).order_by("-ctime").limit(10).all()
    payload = employee.json
    payload.update(
        {
            "role": role.json if role else None,
            "skills": [skill.json for skill in skills],
            "recent_tasks": await _task_payloads_with_reports(employee.tenant_id, tasks),
        }
    )
    return response_ok(payload)


@digital_employee_bp.post("/employee/update")
@requires_permission("digital_employee:u")
async def employee_update(request: Request):
    data = _json(request)
    employee = await _employee_or_404(_tenant_id(request), data.get("employee_id"))
    for field in ["name", "description", "resource_scope"]:
        if field in data:
            setattr(employee, field, data[field])
    if "max_concurrent" in data:
        employee.max_concurrent = max(1, int(data["max_concurrent"] or 1))
    employee.workspace_etag = None
    await employee.save()
    await sync_employee_workspace(employee)
    return response_ok(employee.json)


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
    return response_ok()


@digital_employee_bp.post("/employee/skills/update")
@requires_permission("digital_employee:u")
async def employee_skills_update(request: Request):
    data = _json(request)
    employee = await _employee_or_404(_tenant_id(request), data.get("employee_id"))
    employee = await update_employee_skills(employee, data.get("skill_keys") or [])
    return response_ok(employee.json)


@digital_employee_bp.post("/employee/scope/update")
@requires_permission("digital_employee:u")
async def employee_scope_update(request: Request):
    data = _json(request)
    employee = await _employee_or_404(_tenant_id(request), data.get("employee_id"))
    employee.resource_scope = data.get("resource_scope") or {}
    employee.workspace_etag = None
    await employee.save(update_fields=["resource_scope", "workspace_etag", "utime"])
    await sync_employee_workspace(employee)
    return response_ok(employee.json)


@digital_employee_bp.post("/employee/repair")
@requires_permission("digital_employee:u")
async def employee_repair(request: Request):
    employee = await _employee_or_404(_tenant_id(request), _json(request).get("employee_id"))
    employee.workspace_etag = None
    await employee.save(update_fields=["workspace_etag", "utime"])
    await sync_employee_workspace(employee)
    return response_ok(employee.json)


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
    return await create_command_task(
        tenant_id,
        account_info,
        {
            "title": data.get("title") or (f"{employee.name} 任务" if employee else "数字员工任务"),
            "description": data.get("description"),
            "source": data.get("source", "manual"),
            "priority": data.get("priority", 3),
            "payload": task_payload,
            "agent_id": employee.agent_key if employee else data.get("agent_id") or data.get("requested_agent_id"),
            "room_id": data.get("room_id"),
            "required_capabilities": data.get("required_capabilities") or (employee.capabilities if employee else []),
        },
    )


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
@requires_permission("digital_employee:role:r")
async def role_list(request: Request):
    data = _json(request)
    page, size, offset = _page(data)
    condition = _visible_filter(_tenant_id(request))
    if data.get("keyword"):
        keyword = data["keyword"]
        condition &= Q(name__icontains=keyword) | Q(role_key__icontains=keyword) | Q(description__icontains=keyword)
    queryset = DigitalEmployeeRole.filter(condition)
    total = await queryset.count()
    roles = await queryset.order_by("-is_builtin", "-utime").offset(offset).limit(size).all()
    return response_ok({"list": [role.json for role in roles], "total": total, "page": page, "size": size})


@digital_employee_bp.post("/role/detail")
@requires_permission("digital_employee:role:r")
async def role_detail(request: Request):
    role = await get_visible_role(_tenant_id(request), _json(request).get("role_key"))
    return response_ok(role.json)


@digital_employee_bp.post("/role/save")
async def role_save(request: Request):
    data = _json(request)
    tenant_id = _tenant_id(request)
    role_key = str(data.get("role_key") or data.get("key") or "").strip()
    if not role_key or not data.get("name"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    exists = await DigitalEmployeeRole.filter(tenant_id=tenant_id, role_key=role_key, deleted=False).exists()
    await _require_save_permission(request, exists, "digital_employee:role:c", "digital_employee:role:u")
    role, _ = await DigitalEmployeeRole.update_or_create(
        tenant_id=tenant_id,
        role_key=role_key,
        defaults={
            "name": data["name"],
            "description": data.get("description"),
            "template_dir": data.get("template_dir") or "",
            "default_skill_keys": data.get("default_skill_keys") or data.get("default_skills") or [],
            "default_rbac_role_id": data.get("default_rbac_role_id"),
            "license_required": bool(data.get("license_required", False)),
            "version": int(data.get("version") or 1),
            "enabled": bool(data.get("enabled", True)),
            "meta": data,
            "deleted": False,
        },
    )
    return response_ok(role.json)


@digital_employee_bp.post("/role/delete")
@requires_permission("digital_employee:role:d")
async def role_delete(request: Request):
    role = await get_visible_role(_tenant_id(request), _json(request).get("role_key"))
    if await CommandAgent.filter(tenant_id=_tenant_id(request), role_key=role.role_key, deleted=False).count():
        raise CustomError(ErrorCode.ROLE_IN_USE_BY_EMPLOYEES)
    role.deleted = True
    await role.save(update_fields=["deleted", "utime"])
    return response_ok()


@digital_employee_bp.post("/role/upgrade-employees")
@requires_permission("digital_employee:role:u")
async def role_upgrade_employees(request: Request):
    role = await get_visible_role(_tenant_id(request), _json(request).get("role_key"))
    count = await upgrade_role_for_existing_employees(role)
    return response_ok({"count": count})


@digital_employee_bp.post("/skill/list")
@requires_permission("digital_employee:skill:r")
async def skill_list(request: Request):
    data = _json(request)
    page, size, offset = _page(data)
    condition = _visible_filter(_tenant_id(request))
    if data.get("keyword"):
        keyword = data["keyword"]
        condition &= Q(name__icontains=keyword) | Q(skill_key__icontains=keyword) | Q(description__icontains=keyword)
    queryset = DigitalEmployeeSkill.filter(condition)
    total = await queryset.count()
    skills = await queryset.order_by("-is_builtin", "-utime").offset(offset).limit(size).all()
    return response_ok({"list": [skill.json for skill in skills], "total": total, "page": page, "size": size})


@digital_employee_bp.post("/skill/detail")
@requires_permission("digital_employee:skill:r")
async def skill_detail(request: Request):
    skills = await get_visible_skills(_tenant_id(request), [_json(request).get("skill_key")])
    return response_ok(skills[0].json)


@digital_employee_bp.post("/skill/save")
async def skill_save(request: Request):
    data = _json(request)
    tenant_id = _tenant_id(request)
    skill_key = str(data.get("skill_key") or data.get("key") or "").strip()
    if not skill_key or not data.get("name"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    exists = await DigitalEmployeeSkill.filter(tenant_id=tenant_id, skill_key=skill_key, deleted=False).exists()
    await _require_save_permission(request, exists, "digital_employee:skill:c", "digital_employee:skill:u")
    skill, _ = await DigitalEmployeeSkill.update_or_create(
        tenant_id=tenant_id,
        skill_key=skill_key,
        defaults={
            "name": data["name"],
            "description": data.get("description"),
            "skill_dir": data.get("skill_dir") or "",
            "bindings": data.get("bindings") or {},
            "require_approval": bool(data.get("require_approval", False)),
            "version": int(data.get("version") or 1),
            "enabled": bool(data.get("enabled", True)),
            "meta": data,
            "deleted": False,
        },
    )
    return response_ok(skill.json)


@digital_employee_bp.post("/skill/delete")
@requires_permission("digital_employee:skill:d")
async def skill_delete(request: Request):
    skills = await get_visible_skills(_tenant_id(request), [_json(request).get("skill_key")])
    skill = skills[0]
    if await CommandAgent.filter(tenant_id=_tenant_id(request), skill_keys__contains=[skill.skill_key], deleted=False).count():
        raise CustomError(ErrorCode.SKILL_IN_USE_BY_EMPLOYEES)
    skill.deleted = True
    await skill.save(update_fields=["deleted", "utime"])
    return response_ok()


@digital_employee_bp.post("/package/import")
@requires_permission("digital_employee:package:import")
async def package_import(request: Request):
    uploaded = request.files.get("file") if request.files else None
    if uploaded:
        file_bytes = uploaded.body
    elif request.body:
        file_bytes = request.body
    else:
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    is_builtin_value = request.form.get("is_builtin") if request.form else False
    is_builtin = str(is_builtin_value).lower() in {"1", "true", "yes", "on"}
    result = await import_package(file_bytes, _tenant_id(request), is_builtin, _operator(request), request=request)
    return response_ok(result.json)


@digital_employee_bp.post("/package/export")
@requires_permission("digital_employee:package:export")
async def package_export(request: Request):
    data = _json(request)
    data_bytes = await export_package(data.get("role_key"), data.get("skill_key"), bool(data.get("bundle", False)))
    return response.raw(data_bytes, content_type="application/zip")


@digital_employee_bp.post("/package/list")
@requires_permission("digital_employee:package:r")
async def package_list(request: Request):
    tenant_id = _tenant_id(request)
    roles = await DigitalEmployeeRole.filter(_visible_filter(tenant_id)).all()
    skills = await DigitalEmployeeSkill.filter(_visible_filter(tenant_id)).all()
    return response_ok({"roles": [role.json for role in roles], "skills": [skill.json for skill in skills]})


@digital_employee_bp.post("/package/delete")
@requires_permission("digital_employee:package:d")
async def package_delete(request: Request):
    data = _json(request)
    if data.get("role_key"):
        role = await get_visible_role(_tenant_id(request), data["role_key"])
        if await CommandAgent.filter(tenant_id=_tenant_id(request), role_key=role.role_key, deleted=False).count():
            raise CustomError(ErrorCode.ROLE_IN_USE_BY_EMPLOYEES)
        role.deleted = True
        await role.save(update_fields=["deleted", "utime"])
    elif data.get("skill_key"):
        skills = await get_visible_skills(_tenant_id(request), [data["skill_key"]])
        if await CommandAgent.filter(tenant_id=_tenant_id(request), skill_keys__contains=[skills[0].skill_key], deleted=False).count():
            raise CustomError(ErrorCode.SKILL_IN_USE_BY_EMPLOYEES)
        skills[0].deleted = True
        await skills[0].save(update_fields=["deleted", "utime"])
    else:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok()


@digital_employee_bp.post("/report/list")
@requires_permission("digital_employee:report:r")
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
@requires_permission("digital_employee:report:r")
async def report_detail(request: Request):
    report = await EmployeeReport.get_or_none(id=_json(request).get("report_id"), tenant_id=_tenant_id(request), deleted=False)
    if not report:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(await _report_detail_payload(report))


@digital_employee_bp.post("/report/edit")
@requires_permission("digital_employee:report:u")
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
@requires_permission("digital_employee:report:u")
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
