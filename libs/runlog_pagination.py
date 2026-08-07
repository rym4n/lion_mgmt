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


RUNLOG_CURSOR_VERSION = 3
RUNLOG_NODE_DEFAULT_LIMIT = 50
RUNLOG_NODE_MAX_LIMIT = 200
RUNLOG_LOOP_DEFAULT_LIMIT = 20
RUNLOG_LOOP_MAX_LIMIT = 100
# 单节点单批次取行上限。行数 = 循环 × 资源 × 重试 × 2（RUNNING + 结果），
# 摘要不含 args/result，500 行约 100KB 以内。
RUNLOG_NODE_ROW_CAP = 500
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


def node_type_rank(node_type: str) -> int:
    if node_type == "start-node":
        return 0
    if node_type == "end-node":
        return 2
    return 1


def node_rank_condition(rank: int) -> Q:
    if rank == 0:
        return Q(node_type="start-node")
    if rank == 1:
        return ~Q(node_type__in=["start-node", "end-node"])
    return Q(node_type="end-node")


def sort_node_keys(keys: list[dict]) -> list[dict]:
    """按 (node_type_rank, first_ctime, node_id) 排序节点键。"""
    return sorted(
        keys,
        key=lambda item: (
            node_type_rank(item["node_type"]),
            item["first_ctime"],
            item["node_id"],
        ),
    )


def slice_node_keys(
    sorted_keys: list[dict], decoded: dict | None, limit: int
) -> tuple[list[dict], bool]:
    """按游标切出一页节点键，返回 (page, has_more)。"""
    start = 0
    if decoded:
        try:
            rank = decoded["node_type_rank"]
            if type(rank) is not int or rank not in (0, 1, 2):
                raise ValueError
            anchor = (
                rank,
                datetime.fromisoformat(decoded["ctime"]),
                str(decoded["node_id"]),
            )
        except (KeyError, TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_VALUE_ERROR) from None
        for index, item in enumerate(sorted_keys):
            current = (
                node_type_rank(item["node_type"]),
                item["first_ctime"],
                item["node_id"],
            )
            if current > anchor:
                start = index
                break
        else:
            start = len(sorted_keys)
    page = sorted_keys[start : start + limit]
    return page, start + limit < len(sorted_keys)


def cursor_time_json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


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


def group_loop_rows(rows, loop_limit: int, truncated: bool) -> tuple[list[dict], bool]:
    """把同一节点的行按 loop_index 分组，返回该批次的全部行与 has_more。"""
    summaries = [node_summary(row) for row in rows]
    loop_summaries = [item for item in summaries if item["loop_index"] is not None]
    if not loop_summaries:
        return [], False

    grouped: dict[int, list[dict]] = {}
    order: list[int] = []
    for item in loop_summaries:
        index = item["loop_index"]
        if index not in grouped:
            grouped[index] = []
            order.append(index)
        grouped[index].append(item)

    has_more = truncated
    if truncated and len(order) > 1:
        order.pop()
    if len(order) > loop_limit:
        order = order[:loop_limit]
        has_more = True

    loops: list[dict] = []
    for index in sorted(order):
        loops.extend(sorted(grouped[index], key=lambda item: item["retry_index"]))
    return loops, has_more


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


async def root_playbook_names(model, condition, run_ids: list[str]) -> dict[str, str]:
    if not run_ids:
        return {}
    rows = (
        await model.filter(
            condition,
            run_id__in=run_ids,
            node_type="start-node",
        )
        .order_by("run_id", "ctime", "id")
        .values("run_id", "playbook_name")
    )
    names = {}
    for row in rows:
        if row.get("playbook_name"):
            names.setdefault(row["run_id"], row["playbook_name"])
    return names


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
    primary_playbooks = await root_playbook_names(model, condition, run_ids)
    summaries = {}
    for row in rows:
        summaries[row["run_id"]] = {
            "run_id": row["run_id"],
            "playbook_name": primary_playbooks.get(
                row["run_id"], row.get("playbook_name")
            ),
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


async def node_row_batch(
    model,
    condition,
    *,
    run_id: str,
    node_id: str,
    rows_cursor,
    loop_limit: int,
    cursor_kind: str,
    cursor_scope: dict,
) -> dict:
    """取单个节点的一批明细行。循环节点按 loop_index 对齐批次边界。"""
    decoded = decode_cursor(
        rows_cursor,
        f"{cursor_kind}-rows",
        run_id=run_id,
        node_id=node_id,
        **cursor_scope,
    )
    row_condition = condition & Q(run_id=run_id, node_id=node_id)
    if decoded:
        try:
            cursor_time = datetime.fromisoformat(decoded["ctime"])
            cursor_id = str(decoded["id"])
        except (KeyError, TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_VALUE_ERROR) from None
        row_condition &= Q(ctime__gt=cursor_time) | Q(
            ctime=cursor_time, id__gt=cursor_id
        )
    raw_rows = (
        await model.filter(row_condition)
        .order_by("ctime", "id")
        .limit(RUNLOG_NODE_ROW_CAP + 1)
        .values(*RUNLOG_NODE_FIELDS)
    )
    truncated = len(raw_rows) > RUNLOG_NODE_ROW_CAP
    bounded_rows = raw_rows[:RUNLOG_NODE_ROW_CAP]
    kept, has_more = group_loop_rows(bounded_rows, loop_limit, truncated)
    if not kept:
        kept = [node_summary(row) for row in bounded_rows]
        has_more = truncated
    next_rows_cursor = None
    if has_more and kept:
        kept_ids = {item["id"] for item in kept}
        anchor = max(
            (row for row in bounded_rows if row["id"] in kept_ids),
            key=lambda row: (row["ctime"], row["id"]),
        )
        next_rows_cursor = encode_cursor(
            f"{cursor_kind}-rows",
            run_id=run_id,
            node_id=node_id,
            ctime=cursor_time_json(anchor["ctime"]),
            id=anchor["id"],
            **cursor_scope,
        )
    return {
        "rows": kept,
        "rows_has_more": has_more,
        "rows_cursor": next_rows_cursor,
    }


async def runlog_node_page(
    model,
    condition,
    *,
    run_id: str,
    cursor,
    limit,
    cursor_kind: str,
    cursor_scope: dict,
    node_id=None,
    rows_cursor=None,
    loop_limit=None,
) -> dict:
    """按节点分页返回运行日志。"""
    bounded_loop_limit = parse_bounded_int(
        loop_limit,
        default=RUNLOG_LOOP_DEFAULT_LIMIT,
        maximum=RUNLOG_LOOP_MAX_LIMIT,
    )
    scoped_condition = condition & Q(run_id=run_id)

    if node_id:
        batch = await node_row_batch(
            model,
            condition,
            run_id=run_id,
            node_id=str(node_id),
            rows_cursor=rows_cursor,
            loop_limit=bounded_loop_limit,
            cursor_kind=cursor_kind,
            cursor_scope=cursor_scope,
        )
        return {
            "node_id": str(node_id),
            **batch,
            "nodes": [],
            "next_cursor": None,
            "has_more": False,
        }

    bounded_limit = parse_bounded_int(
        limit, default=RUNLOG_NODE_DEFAULT_LIMIT, maximum=RUNLOG_NODE_MAX_LIMIT
    )
    decoded = decode_cursor(cursor, cursor_kind, run_id=run_id, **cursor_scope)
    key_rows = (
        await model.filter(scoped_condition)
        .annotate(
            first_ctime=Min("ctime"),
            node_type_max=Max("node_type"),
            log_count=Count("id"),
            failed_count=Count("id", _filter=Q(status__in=[-1, 3])),
            running_count=Count("id", _filter=Q(status__in=[1, 2])),
            success_count=Count("id", _filter=Q(status=0)),
        )
        .group_by("node_id")
        .values(
            "node_id",
            "first_ctime",
            "node_type_max",
            "log_count",
            "failed_count",
            "running_count",
            "success_count",
        )
    )
    keys = [{**row, "node_type": row["node_type_max"]} for row in key_rows]
    page, has_more = slice_node_keys(sort_node_keys(keys), decoded, bounded_limit)

    nodes = []
    for key in page:
        batch = await node_row_batch(
            model,
            condition,
            run_id=run_id,
            node_id=key["node_id"],
            rows_cursor=None,
            loop_limit=bounded_loop_limit,
            cursor_kind=cursor_kind,
            cursor_scope=cursor_scope,
        )
        if not batch["rows"]:
            continue
        nodes.append(
            {
                **batch["rows"][0],
                "status": batch_status(key),
                **batch,
            }
        )

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            cursor_kind,
            run_id=run_id,
            node_type_rank=node_type_rank(last["node_type"]),
            ctime=cursor_time_json(last["first_ctime"]),
            node_id=last["node_id"],
            **cursor_scope,
        )
    return {"nodes": nodes, "next_cursor": next_cursor, "has_more": has_more}
