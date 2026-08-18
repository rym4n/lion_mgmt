# coding=utf-8
"""Report schedule timer integration.

This module does not start an in-process scheduler. Report schedules are
mirrored to internal Timed rows and published through the existing timer Kafka
contract. The timer fires the lion_mgmt run_report_schedule action, which
calls back into lion_mgmt's internal schedule execution endpoint.
"""

import logging
from datetime import datetime
from typing import Any

from croniter import croniter

from libs.kafka import aproduce
from models import Account, Timed
from models.report import ReportDocument, ReportSchedule
from settings import TASK_TOPIC, TZ

logger = logging.getLogger(__name__)

REPORT_TIMER_END_TIME = "2099-12-31 23:59:59"
REPORT_TIMER_TYPE_ACTION = 1
REPORT_TIMER_STATUS_CRON = 1
REPORT_TIMER_APP_NAME = "lion_mgmt"
REPORT_TIMER_APP_VER = "v1"
REPORT_TIMER_MAIN = "main.py"
REPORT_TIMER_ACTION = "run_report_schedule"


def report_timer_name(schedule_id: str) -> str:
    return f"report_schedule_{schedule_id}"


def calculate_next_run_at(cron_expr: str, now: datetime | None = None) -> datetime:
    base = now or datetime.now(TZ)
    if base.tzinfo is None:
        base = TZ.localize(base)
    return croniter(cron_expr, base).get_next(datetime)


def build_report_timer_run_args(schedule: Any) -> dict:
    schedule_id = str(schedule.id)
    document_id = str(schedule.document_id)
    operator_id = str(getattr(schedule, "creator_id", "") or "")
    return {
        "resource": "",
        "node_name": getattr(schedule, "name", "") or "report_schedule",
        "action": REPORT_TIMER_ACTION,
        "action_name": REPORT_TIMER_ACTION,
        "app_name": REPORT_TIMER_APP_NAME,
        "name": getattr(schedule, "name", "") or "report_schedule",
        "app_ver": REPORT_TIMER_APP_VER,
        "main": REPORT_TIMER_MAIN,
        "action_input": {
            "schedule_id": schedule_id,
            "document_id": document_id,
            "tenant_id": getattr(schedule, "tenant_id", "") or "",
            "export_format": getattr(schedule, "export_format", "docx") or "docx",
            "timeout": 600,
        },
        "retry_times": 1,
        "timeout": 600,
        "executor": operator_id,
    }


def build_report_timer_message(schedule: Any, timed: Any, *, remove: bool = False) -> dict:
    message = {
        "run_type": "action",
        "run_args": build_report_timer_run_args(schedule),
        "timer_id": str(timed.id),
        "tenant_id": schedule.tenant_id,
    }
    if remove:
        message["timer"] = "remove"
        message["timer_rule"] = {}
    else:
        message["timer"] = "cron"
        message["timer_rule"] = {
            "end_date": getattr(timed, "end_time", None) or REPORT_TIMER_END_TIME,
            "crontab": schedule.cron,
        }
    return message


async def _find_timed(schedule: ReportSchedule) -> Timed | None:
    timed_id = getattr(schedule, "timed_id", None)
    if timed_id:
        timed = await Timed.get_or_none(id=timed_id, tenant_id=schedule.tenant_id, deleted=False)
        if timed:
            return timed
    return await Timed.filter(
        name=report_timer_name(str(schedule.id)),
        tenant_id=schedule.tenant_id,
        deleted=False,
    ).first()


async def sync_report_schedule_timer(
    schedule: ReportSchedule,
    operator: Account,
    *,
    publish: bool = True,
) -> dict:
    """Create or update the internal Timed row and publish its timer message."""

    existed = True
    timed = await _find_timed(schedule)
    run_args = build_report_timer_run_args(schedule)
    enable = 1 if schedule.enabled else 0

    if timed:
        timed.name = report_timer_name(str(schedule.id))
        timed.type = REPORT_TIMER_TYPE_ACTION
        timed.playbook = None
        timed.playbook_version = None
        timed.playbook_input = {}
        timed.action = None
        timed.action_input = run_args
        timed.status = REPORT_TIMER_STATUS_CRON
        timed.tactics = schedule.cron
        timed.end_time = REPORT_TIMER_END_TIME
        timed.enable = enable
        timed.internal = True
        timed.updator = operator
        if not getattr(timed, "creator_id", None):
            timed.creator = operator
        await timed.save()
    else:
        existed = False
        timed = await Timed.create(
            name=report_timer_name(str(schedule.id)),
            type=REPORT_TIMER_TYPE_ACTION,
            playbook=None,
            playbook_version=None,
            playbook_input={},
            action=None,
            action_input=run_args,
            status=REPORT_TIMER_STATUS_CRON,
            tactics=schedule.cron,
            end_time=REPORT_TIMER_END_TIME,
            enable=enable,
            internal=True,
            creator=operator,
            updator=operator,
            tenant_id=schedule.tenant_id,
        )

    schedule.timed_id = timed.id
    schedule.next_run_at = calculate_next_run_at(schedule.cron) if schedule.enabled else None
    await schedule.save(update_fields=["timed_id", "next_run_at", "utime"])

    message = build_report_timer_message(schedule, timed, remove=not schedule.enabled)
    published = False
    if publish and (schedule.enabled or existed):
        await aproduce(TASK_TOPIC, message)
        published = True

    return {
        "timed": timed,
        "message": message,
        "published": published,
        "publish_supported": True,
    }


async def delete_report_schedule_timer(
    schedule: ReportSchedule,
    operator: Account,
    *,
    publish: bool = True,
) -> dict:
    timed = await _find_timed(schedule)
    if not timed:
        return {"timed": None, "message": None, "published": False, "publish_supported": True}

    message = build_report_timer_message(schedule, timed, remove=True)
    if publish:
        await aproduce(TASK_TOPIC, message)

    timed.enable = 0
    timed.deleted = True
    timed.updator = operator
    await timed.save(update_fields=["enable", "deleted", "updator_id", "utime"])
    return {
        "timed": timed,
        "message": message,
        "published": bool(publish),
        "publish_supported": True,
    }


async def execute_report_schedule(schedule_id: str, tenant_id: str | None = None) -> dict:
    """Execute one report schedule."""

    query = ReportSchedule.filter(id=schedule_id, deleted=False)
    if tenant_id:
        query = query.filter(tenant_id=tenant_id)
    schedule = await query.prefetch_related("creator").first()
    if not schedule:
        return {"status": "skipped", "reason": "schedule_not_found"}
    if not schedule.enabled:
        return {"status": "skipped", "reason": "schedule_disabled"}

    now = datetime.now(TZ)
    try:
        document = await ReportDocument.get_or_none(
            id=schedule.document_id,
            tenant_id=schedule.tenant_id,
            deleted=False,
        )
        if not document or document.deleted:
            raise RuntimeError("report document not found")

        from services.agent.views import send_report_notify_for_document

        result = await send_report_notify_for_document(
            document,
            tenant_id=schedule.tenant_id,
            owner_id=str(document.owner_id or ""),
            operator_id=str(schedule.creator_id or document.owner_id or ""),
            fmt=schedule.export_format,
        )
        schedule.last_status = "ok"
        schedule.last_error = None
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("report schedule %s run failed", schedule_id)
        schedule.last_status = "failed"
        schedule.last_error = str(exc)[:2048]
        return {"status": "failed", "error": schedule.last_error}
    finally:
        schedule.last_run_at = now
        schedule.next_run_at = calculate_next_run_at(schedule.cron, now)
        await schedule.save(update_fields=["last_run_at", "last_status", "last_error", "next_run_at", "utime"])
