# coding=utf-8
from models.command_center import CommandTask
from models.employee_report import EmployeeReport

from .employee_report_payload import compose_payload


async def compose(task: CommandTask, actions_draft: list[dict] | None = None) -> EmployeeReport:
    employee = await task.assigned_agent
    payload = compose_payload(task, actions_draft)
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
