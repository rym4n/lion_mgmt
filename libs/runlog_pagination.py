# coding=utf-8

import base64
import binascii
import json
import re
from datetime import datetime

from tortoise.expressions import Q
from tortoise.functions import Count, Max, Min

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError


RUNLOG_CURSOR_VERSION = 1
RUNLOG_NODE_DEFAULT_LIMIT = 50
RUNLOG_NODE_MAX_LIMIT = 200
RUNLOG_BATCH_DEFAULT_SIZE = 10
RUNLOG_BATCH_MAX_SIZE = 50
RUNLOG_LOOP_RE = re.compile(r"【第(\d+)次循环】")
RUNLOG_RETRY_RE = re.compile(r"（(\d+)次重试）")
RUNLOG_NODE_FIELDS = (
    "id",
    "run_id",
    "node_id",
    "node_name",
    "node_type",
    "tool_name",
    "tool_version",
    "tool_display_name",
    "action_name",
    "action_display_name",
    "status",
    "start_time",
    "end_time",
    "ctime",
    "utime",
    "resource_name",
    "playbook_name",
)


def parse_bounded_int(value, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR) from None
    if parsed < 1:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    return min(parsed, maximum)


def encode_cursor(kind: str, **payload) -> str:
    raw = json.dumps(
        {"v": RUNLOG_CURSOR_VERSION, "kind": kind, **payload},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token, kind: str, **scope) -> dict | None:
    if token in (None, ""):
        return None
    if not isinstance(token, str) or len(token) > 2048:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR) from None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != RUNLOG_CURSOR_VERSION
        or payload.get("kind") != kind
    ):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    if any(payload.get(key) != value for key, value in scope.items()):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    return payload


def time_json(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value


def node_summary(row: dict) -> dict:
    node_name = row.get("node_name") or ""
    loop_match = RUNLOG_LOOP_RE.search(node_name)
    retry_match = RUNLOG_RETRY_RE.search(node_name)
    summary = {key: row.get(key) for key in RUNLOG_NODE_FIELDS}
    for key in ("start_time", "end_time", "ctime", "utime"):
        summary[key] = time_json(summary.get(key))
    summary["loop_index"] = int(loop_match.group(1)) if loop_match else None
    summary["retry_index"] = int(retry_match.group(1)) if retry_match else 0
    return summary


def batch_status(row: dict) -> int:
    if int(row.get("failed_count") or 0) > 0:
        return -1
    if int(row.get("running_count") or 0) > 0:
        return 1
    if int(row.get("log_count") or 0) and int(row.get("success_count") or 0) == int(
        row.get("log_count") or 0
    ):
        return 0
    return -1


async def runlog_batch_summaries(
    model, condition, run_ids: list[str]
) -> dict[str, dict]:
    if not run_ids:
        return {}
    rows = (
        await model.filter(condition, run_id__in=run_ids)
        .annotate(
            log_count=Count("id"),
            node_count=Count("node_id", distinct=True),
            loop_count=Count(
                "node_name", distinct=True, _filter=Q(node_name__contains="【第")
            ),
            failed_count=Count("id", _filter=Q(status__in=[-1, 3])),
            running_count=Count("id", _filter=Q(status__in=[1, 2])),
            success_count=Count("id", _filter=Q(status=0)),
            started_at=Min("start_time"),
            finished_at=Max("end_time"),
            playbook_name=Max("playbook_name"),
            action_name=Max("action_name"),
            action_display_name=Max("action_display_name"),
            tool_display_name=Max("tool_display_name"),
        )
        .group_by("run_id")
        .values(
            "run_id",
            "log_count",
            "node_count",
            "loop_count",
            "failed_count",
            "running_count",
            "success_count",
            "started_at",
            "finished_at",
            "playbook_name",
            "action_name",
            "action_display_name",
            "tool_display_name",
        )
    )
    summaries = {}
    for row in rows:
        summaries[row["run_id"]] = {
            "run_id": row["run_id"],
            "playbook_name": row.get("playbook_name"),
            "action_name": row.get("action_name"),
            "action_display_name": row.get("action_display_name"),
            "tool_display_name": row.get("tool_display_name"),
            "status": batch_status(row),
            "started_at": time_json(row.get("started_at")),
            "finished_at": time_json(row.get("finished_at")),
            "log_count": int(row.get("log_count") or 0),
            "node_count": int(row.get("node_count") or 0),
            "loop_count": int(row.get("loop_count") or 0),
        }
    return summaries


async def paginated_runlog_summaries(
    model, condition, page: int, size: int
) -> tuple[int, list[dict]]:
    page = parse_bounded_int(page, default=1, maximum=1_000_000)
    size = parse_bounded_int(
        size, default=RUNLOG_BATCH_DEFAULT_SIZE, maximum=RUNLOG_BATCH_MAX_SIZE
    )
    run_id_rows = (
        await model.filter(condition)
        .annotate(latest_start_time=Max("start_time"), latest_ctime=Max("ctime"))
        .group_by("run_id")
        .order_by("-latest_start_time", "-latest_ctime", "-run_id")
        .offset((page - 1) * size)
        .limit(size)
        .values("run_id")
    )
    run_ids = [row["run_id"] for row in run_id_rows]
    total_rows = (
        await model.filter(condition)
        .annotate(total=Count("run_id", distinct=True))
        .values("total")
    )
    total = int(total_rows[0]["total"]) if total_rows else 0
    summaries = await runlog_batch_summaries(model, condition, run_ids)
    return total, [summaries[run_id] for run_id in run_ids if run_id in summaries]


async def runlog_node_page(
    model,
    condition,
    *,
    run_id: str,
    cursor,
    limit,
    cursor_kind: str,
    cursor_scope: dict,
) -> dict:
    bounded_limit = parse_bounded_int(
        limit,
        default=RUNLOG_NODE_DEFAULT_LIMIT,
        maximum=RUNLOG_NODE_MAX_LIMIT,
    )
    decoded = decode_cursor(cursor, cursor_kind, run_id=run_id, **cursor_scope)
    scoped_condition = condition & Q(run_id=run_id)
    if decoded:
        try:
            cursor_time = datetime.fromisoformat(decoded["ctime"])
            cursor_id = str(decoded["id"])
        except (KeyError, TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_VALUE_ERROR) from None
        scoped_condition &= Q(ctime__gt=cursor_time) | Q(
            ctime=cursor_time, id__gt=cursor_id
        )
    rows = (
        await model.filter(scoped_condition)
        .order_by("ctime", "id")
        .limit(bounded_limit + 1)
        .values(*RUNLOG_NODE_FIELDS)
    )
    has_more = len(rows) > bounded_limit
    page = rows[:bounded_limit]
    next_cursor = None
    if has_more and page:
        next_cursor = encode_cursor(
            cursor_kind,
            run_id=run_id,
            ctime=time_json(page[-1]["ctime"]),
            id=page[-1]["id"],
            **cursor_scope,
        )
    return {
        "nodes": [node_summary(row) for row in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
