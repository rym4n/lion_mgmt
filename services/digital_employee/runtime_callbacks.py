# coding=utf-8
from typing import Any

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import CommandTask, CommandTaskEvent
from models.employee_report import EmployeeReport


TRANSCRIPT_EVENT_TYPE = "runtime_transcript_saved"


async def _get_task(tenant_id: str, task_id: str | None) -> CommandTask:
    if not task_id:
        raise CustomError(ErrorCode.NOT_FOUND)
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    return task


def _actions_from(data: dict[str, Any]) -> list[dict]:
    for key in ("actions_draft", "actions"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    report = data.get("report")
    if isinstance(report, dict) and isinstance(report.get("actions"), list):
        return report["actions"]
    return []


def _transcript_from(data: dict[str, Any]) -> list[dict]:
    for key in ("transcript", "messages", "events"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


async def save_runtime_transcript(tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
    task = await _get_task(tenant_id, data.get("task_id") or data.get("id"))
    actions = _actions_from(data)
    metadata = {
        "task_id": task.id,
        "run_id": data.get("run_id") or task.run_id,
        "transcript": _transcript_from(data),
        "actions": actions,
        "actions_draft": actions,
        "content": data.get("content") or data.get("result"),
    }
    await CommandTaskEvent.create(
        tenant_id=task.tenant_id,
        task=task,
        event_type=TRANSCRIPT_EVENT_TYPE,
        status=task.status,
        progress=task.progress,
        agent_key=task.assigned_agent_key,
        message=data.get("message") or "runtime transcript saved",
        metadata=metadata,
        operator_id=data.get("operator_id"),
    )
    return metadata


async def fetch_runtime_transcript(tenant_id: str, task_id: str | None) -> dict[str, Any]:
    task = await _get_task(tenant_id, task_id)
    event = await CommandTaskEvent.filter(
        tenant_id=tenant_id,
        task=task,
        event_type=TRANSCRIPT_EVENT_TYPE,
        deleted=False,
    ).order_by("-ctime").first()
    if not event:
        return {"task_id": task.id, "run_id": task.run_id, "transcript": [], "actions": [], "actions_draft": []}
    metadata = event.metadata or {}
    actions = metadata.get("actions_draft") or metadata.get("actions") or []
    return {
        "task_id": task.id,
        "run_id": metadata.get("run_id") or task.run_id,
        "transcript": metadata.get("transcript") or [],
        "actions": actions,
        "actions_draft": actions,
        "content": metadata.get("content"),
    }


def _report_payload(task: CommandTask, data: dict[str, Any]) -> dict[str, Any]:
    report = data.get("report") if isinstance(data.get("report"), dict) else data
    task_content = task.description or task.title
    return {
        "title": (report.get("title") or task_content)[:255],
        "severity": report.get("severity") or "info",
        "observation": report.get("observation"),
        "analysis": report.get("analysis"),
        "actions": _actions_from(report),
        "conclusion": report.get("conclusion") or report.get("content") or report.get("result"),
        "evidence_urls": report.get("evidence_urls") or [],
        "report_document_id": report.get("report_document_id"),
        "auto_generated": bool(report.get("auto_generated", True)),
    }


async def save_runtime_report(tenant_id: str, data: dict[str, Any]) -> EmployeeReport:
    task = await _get_task(tenant_id, data.get("task_id") or data.get("id"))
    employee = await task.assigned_agent
    payload = _report_payload(task, data)
    report, created = await EmployeeReport.get_or_create(
        tenant_id=task.tenant_id,
        task=task,
        defaults={
            "employee": employee,
            "role_key": getattr(employee, "role_key", None) or "",
            **payload,
        },
    )
    if not created:
        for key, value in payload.items():
            setattr(report, key, value)
        report.employee = employee
        report.role_key = getattr(employee, "role_key", None) or report.role_key
        await report.save()
    return report
