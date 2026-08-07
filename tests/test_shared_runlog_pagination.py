# coding=utf-8

import inspect
from datetime import datetime, timezone

import pytest
from tortoise.expressions import Q

from libs.error_code.exception import CustomError
from libs.runlog_pagination import (
    decode_cursor,
    encode_cursor,
    group_loop_rows,
    node_summary,
    runlog_node_page,
    slice_node_keys,
    sort_node_keys,
)
from services.event import views as event_views
from services.timed import views as timed_views
from services.warroom import views as warroom_views
from services.workflow import views as workflow_views


def matches_q(row, condition):
    if condition.children:
        matches = [matches_q(row, child) for child in condition.children]
        result = all(matches) if condition.join_type == "AND" else any(matches)
    else:
        result = True
        for lookup, expected in condition.filters.items():
            field, _, operator = lookup.partition("__")
            actual = row.get(field)
            if operator == "gt":
                result = result and actual > expected
            elif operator == "in":
                result = result and actual in expected
            elif operator == "isnull":
                result = result and (actual is None) is expected
            else:
                result = result and actual == expected
    return not result if condition._is_negated else result


class SharedRunlogQuery:
    def __init__(self, rows, condition):
        self.rows = rows
        self.condition = condition
        self.ordering = ()
        self.row_limit = None
        self.grouped_by = None

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    def limit(self, row_limit):
        self.row_limit = row_limit
        return self

    def annotate(self, **_annotations):
        return self

    def group_by(self, *fields):
        self.grouped_by = fields
        return self

    async def values(self, *fields):
        rows = [row for row in self.rows if matches_q(row, self.condition)]
        if self.grouped_by == ("node_id",):
            return self._aggregate_by_node(rows)
        assert self.ordering == ("ctime", "id")
        rows.sort(key=lambda row: (row["ctime"], row["id"]))
        if self.row_limit is not None:
            rows = rows[: self.row_limit]
        return [
            {field: row.get(field) for field in fields}
            for row in rows
        ]

    @staticmethod
    def _aggregate_by_node(rows):
        grouped = {}
        for row in rows:
            grouped.setdefault(row["node_id"], []).append(row)
        aggregated = []
        for node_id, node_rows in grouped.items():
            statuses = [row.get("status") for row in node_rows]
            aggregated.append(
                {
                    "node_id": node_id,
                    "first_ctime": min(row["ctime"] for row in node_rows),
                    "node_type_max": max(row["node_type"] for row in node_rows),
                    "log_count": len(node_rows),
                    "failed_count": sum(1 for s in statuses if s in (-1, 3)),
                    "running_count": sum(1 for s in statuses if s in (1, 2)),
                    "success_count": sum(1 for s in statuses if s == 0),
                }
            )
        return aggregated


class SharedRunlogNode:
    rows = []
    queries = []

    @classmethod
    def filter(cls, condition):
        query = SharedRunlogQuery(cls.rows, condition)
        cls.queries.append(query)
        return query


async def shared_node_page(*, cursor=None, limit=50, node_id=None, rows_cursor=None, loop_limit=None):
    return await runlog_node_page(
        SharedRunlogNode,
        Q(),
        run_id="run-1",
        cursor=cursor,
        limit=limit,
        cursor_kind="test-node",
        cursor_scope={"tenant_id": "tenant-1"},
        node_id=node_id,
        rows_cursor=rows_cursor,
        loop_limit=loop_limit,
    )


async def create_node(
    node_id,
    node_type,
    ctime,
    *,
    row_id=None,
    node_name=None,
    status=0,
    resource_name=None,
):
    SharedRunlogNode.rows.append(
        {
            "id": row_id or node_id,
            "run_id": "run-1",
            "node_id": node_id,
            "node_name": node_name or node_id,
            "node_type": node_type,
            "status": status,
            "resource_name": resource_name,
            "ctime": ctime,
            "utime": ctime,
        }
    )


def loop_row(row_id, loop_index, retry_index=0, status=0, second=0):
    suffix = f"（{retry_index}次重试）" if retry_index else ""
    return {
        "id": row_id,
        "run_id": "run-1",
        "node_id": "node-1",
        "node_name": f"【第{loop_index}次循环】循环动作{suffix}",
        "node_type": "action-node",
        "status": status,
        "ctime": datetime(2026, 8, 7, 8, 0, second, tzinfo=timezone.utc),
    }


@pytest.fixture(autouse=True)
def clear_runlog_rows():
    SharedRunlogNode.rows = []
    SharedRunlogNode.queries = []


@pytest.mark.asyncio
async def test_node_order_ranks_types_before_same_time_random_ids():
    same_time = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)
    await create_node("z-start", "start-node", same_time)
    await create_node("a-action", "action-node", same_time)
    await create_node("m-action", "action-node", same_time)
    await create_node("0-end", "end-node", same_time)

    page = await shared_node_page()

    assert [node["id"] for node in page["nodes"]] == [
        "z-start",
        "a-action",
        "m-action",
        "0-end",
    ]
    assert page["next_cursor"] is None
    assert page["has_more"] is False


@pytest.mark.asyncio
async def test_cursor_boundary_preserves_rank_order_without_duplicates():
    same_time = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)
    await create_node("z-start", "start-node", same_time)
    await create_node("a-action", "action-node", same_time)
    await create_node("m-action", "action-node", same_time)
    await create_node("0-end", "end-node", same_time)

    first_page = await shared_node_page(limit=2)
    assert SharedRunlogNode.queries[0].grouped_by == ("node_id",)
    assert SharedRunlogNode.queries[0].row_limit is None
    assert [
        query.row_limit
        for query in SharedRunlogNode.queries
        if query.grouped_by is None
    ] == [501, 501]
    second_page = await shared_node_page(
        cursor=first_page["next_cursor"], limit=2
    )

    assert [node["id"] for node in first_page["nodes"]] == [
        "z-start",
        "a-action",
    ]
    assert [node["id"] for node in second_page["nodes"]] == [
        "m-action",
        "0-end",
    ]
    assert second_page["next_cursor"] is None
    assert second_page["has_more"] is False


@pytest.mark.asyncio
async def test_cursor_keeps_subsecond_precision_at_business_node_boundary():
    early_time = datetime(2026, 8, 7, 8, 0, 0, 100, tzinfo=timezone.utc)
    late_time = datetime(2026, 8, 7, 8, 0, 0, 900, tzinfo=timezone.utc)
    await create_node("z-early", "action-node", early_time)
    await create_node("a-late", "action-node", late_time)

    first_page = await shared_node_page(limit=1)
    second_page = await shared_node_page(
        cursor=first_page["next_cursor"], limit=1
    )

    assert [node["id"] for node in first_page["nodes"]] == ["z-early"]
    assert [node["id"] for node in second_page["nodes"]] == ["a-late"]


def test_shared_node_summary_never_contains_heavy_payloads():
    summary = node_summary(
        {
            "id": "log-1",
            "run_id": "run-1",
            "node_id": "node-1",
            "node_name": "循环动作【第8次循环】（3次重试）",
            "node_type": "action-node",
            "status": 0,
            "args": {"secret": "not-in-summary"},
            "result": "not-in-summary",
        }
    )

    assert "args" not in summary
    assert "result" not in summary
    assert summary["loop_index"] == 8
    assert summary["retry_index"] == 3


def test_shared_cursor_is_bound_to_business_scope():
    cursor = encode_cursor(
        "event-node",
        event_id="event-1",
        run_id="run-1",
        node_type_rank=1,
        ctime="2026-08-03T12:00:00+08:00",
        node_id="node-1",
    )

    payload = decode_cursor(cursor, "event-node", event_id="event-1", run_id="run-1")
    assert payload["node_id"] == "node-1"
    with pytest.raises(CustomError):
        decode_cursor(cursor, "event-node", event_id="event-2", run_id="run-1")


@pytest.mark.asyncio
async def test_node_page_rejects_invalid_cursor_rank():
    cursor = encode_cursor(
        "test-node",
        tenant_id="tenant-1",
        run_id="run-1",
        node_type_rank=99,
        ctime="2026-08-03T12:00:00+00:00",
        node_id="node-1",
    )

    with pytest.raises(CustomError):
        await shared_node_page(cursor=cursor)


def test_all_runlog_consumers_keep_domain_permission_and_tenant_detail_scope():
    handlers = (
        (timed_views.get_run_log_nodes, "timed:r"),
        (timed_views.get_run_log_detail, "timed:r"),
        (workflow_views.get_version_runlog_nodes, "workflow:r"),
        (workflow_views.get_version_runlog_detail, "workflow:r"),
        (event_views.get_event_runlog_nodes, "event:r"),
        (event_views.get_event_runlog_detail, "event:r"),
        (warroom_views.get_warroom_runlog_nodes, "warroom:r"),
        (warroom_views.get_warroom_runlog_detail, "warroom:r"),
    )
    for handler, permission in handlers:
        source = inspect.getsource(handler)
        assert f'requires_permission("{permission}")' in source

    for handler in (
        timed_views.get_run_log_detail,
        workflow_views.get_version_runlog_detail,
        event_views.get_event_runlog_detail,
        warroom_views.get_warroom_runlog_detail,
    ):
        source = inspect.getsource(handler)
        for condition in (
            "id=runlog_id",
            "run_id=run_id",
            "tenant_id=tenant_id",
            "deleted=False",
        ):
            assert condition in source


def test_batch_entrypoints_offer_summary_only_responses():
    assert "paginated_runlog_summaries" in inspect.getsource(timed_views.get_run_log)
    assert "paginated_runlog_summaries" in inspect.getsource(
        workflow_views.get_version_runlog
    )
    assert "paginated_runlog_summaries" in inspect.getsource(
        event_views.get_event_runlogs
    )
    query_source = inspect.getsource(warroom_views.query_messages)
    assert "compact_runlogs" in query_source
    assert "runlog_batch_summaries" in query_source


def test_group_loop_rows_keeps_every_retry_row_in_loop_order():
    rows = [
        loop_row("r1", 1, second=1),
        loop_row("r2", 2, second=2),
        loop_row("r3", 2, retry_index=1, second=3),
    ]

    loops, has_more = group_loop_rows(rows, loop_limit=20, truncated=False)

    assert [(item["loop_index"], item["retry_index"]) for item in loops] == [
        (1, 0),
        (2, 0),
        (2, 1),
    ]
    assert has_more is False
    assert "args" not in loops[0] and "result" not in loops[0]


def test_group_loop_rows_returns_empty_for_non_loop_node():
    rows = [
        {
            "id": "r1",
            "node_id": "node-1",
            "node_name": "普通动作",
            "node_type": "action-node",
            "status": 0,
            "ctime": datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc),
        }
    ]

    assert group_loop_rows(rows, loop_limit=20, truncated=False) == ([], False)


def test_group_loop_rows_drops_trailing_incomplete_group_when_truncated():
    rows = [
        loop_row("r1", 1, second=1),
        loop_row("r2", 2, second=2),
        loop_row("r3", 3, second=3),
    ]

    loops, has_more = group_loop_rows(rows, loop_limit=20, truncated=True)

    assert [item["loop_index"] for item in loops] == [1, 2]
    assert has_more is True


def test_group_loop_rows_keeps_sole_group_even_when_truncated():
    rows = [
        loop_row("r1", 1, second=1),
        loop_row("r2", 1, retry_index=1, second=2),
    ]

    loops, has_more = group_loop_rows(rows, loop_limit=20, truncated=True)

    assert [item["retry_index"] for item in loops] == [0, 1]
    assert has_more is True


def test_group_loop_rows_caps_group_count_at_loop_limit():
    rows = [loop_row(f"r{index}", index, second=index) for index in range(1, 6)]

    loops, has_more = group_loop_rows(rows, loop_limit=3, truncated=False)

    assert [item["loop_index"] for item in loops] == [1, 2, 3]
    assert has_more is True


def test_group_loop_rows_keeps_every_resource_row_of_a_loop():
    rows = [
        {**loop_row("r1", 1, second=1), "resource_name": "web-01"},
        {**loop_row("r2", 1, second=2), "resource_name": "web-02"},
    ]

    loops, has_more = group_loop_rows(rows, loop_limit=20, truncated=False)

    assert [item["resource_name"] for item in loops] == ["web-01", "web-02"]
    assert has_more is False


def node_key(node_id, node_type="action-node", second=0):
    return {
        "node_id": node_id,
        "node_type": node_type,
        "first_ctime": datetime(2026, 8, 7, 8, 0, second, tzinfo=timezone.utc),
        "log_count": 2,
        "failed_count": 0,
        "running_count": 0,
        "success_count": 2,
    }


def test_sort_node_keys_puts_start_first_and_end_last():
    keys = [
        node_key("z-end", "end-node", second=9),
        node_key("m-action", second=5),
        node_key("a-start", "start-node", second=1),
    ]

    assert [item["node_id"] for item in sort_node_keys(keys)] == [
        "a-start",
        "m-action",
        "z-end",
    ]


def test_sort_node_keys_breaks_ties_by_node_id():
    keys = [node_key("b-action", second=3), node_key("a-action", second=3)]

    assert [item["node_id"] for item in sort_node_keys(keys)] == [
        "a-action",
        "b-action",
    ]


def test_slice_node_keys_resumes_after_cursor_without_duplicates():
    keys = sort_node_keys(
        [node_key(f"node-{index}", second=index) for index in range(1, 6)]
    )

    first, first_more = slice_node_keys(keys, None, 2)
    assert [item["node_id"] for item in first] == ["node-1", "node-2"]
    assert first_more is True

    decoded = {
        "node_type_rank": 1,
        "ctime": first[-1]["first_ctime"].isoformat(),
        "node_id": "node-2",
    }
    second, second_more = slice_node_keys(keys, decoded, 2)
    assert [item["node_id"] for item in second] == ["node-3", "node-4"]
    assert second_more is True


def test_slice_node_keys_reports_exhausted_page():
    keys = sort_node_keys([node_key("only-node", second=1)])

    page, has_more = slice_node_keys(keys, None, 50)

    assert [item["node_id"] for item in page] == ["only-node"]
    assert has_more is False


@pytest.mark.asyncio
async def test_loop_iterations_fold_into_one_node():
    base = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)
    for index in range(1, 4):
        await create_node(
            "loop-node",
            "action-node",
            base.replace(second=index),
            row_id=f"row-{index}",
            node_name=f"【第{index}次循环】循环动作",
        )

    page = await shared_node_page()

    assert len(page["nodes"]) == 1
    node = page["nodes"][0]
    assert node["node_id"] == "loop-node"
    assert [row["loop_index"] for row in node["rows"]] == [1, 2, 3]
    assert node["rows_has_more"] is False
    assert page["has_more"] is False


@pytest.mark.asyncio
async def test_node_status_reflects_all_rows_not_just_returned_batch():
    base = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)
    await create_node(
        "loop-node", "action-node", base.replace(second=1), row_id="row-1",
        node_name="【第1次循环】循环动作", status=0,
    )
    await create_node(
        "loop-node", "action-node", base.replace(second=2), row_id="row-2",
        node_name="【第2次循环】循环动作", status=-1,
    )

    page = await shared_node_page()

    assert page["nodes"][0]["status"] == -1


@pytest.mark.asyncio
async def test_row_batch_mode_returns_next_loop_slice_only():
    base = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)
    for index in range(1, 4):
        await create_node(
            "loop-node", "action-node", base.replace(second=index),
            row_id=f"row-{index}", node_name=f"【第{index}次循环】循环动作",
        )

    first = await runlog_node_page(
        SharedRunlogNode, Q(), run_id="run-1", cursor=None, limit=50,
        cursor_kind="test-node", cursor_scope={"tenant_id": "tenant-1"}, loop_limit=2,
    )
    node = first["nodes"][0]
    assert [row["loop_index"] for row in node["rows"]] == [1, 2]
    assert node["rows_has_more"] is True

    second = await runlog_node_page(
        SharedRunlogNode, Q(), run_id="run-1", cursor=None, limit=50,
        cursor_kind="test-node", cursor_scope={"tenant_id": "tenant-1"},
        node_id="loop-node", rows_cursor=node["rows_cursor"], loop_limit=2,
    )
    assert second["node_id"] == "loop-node"
    assert [row["loop_index"] for row in second["rows"]] == [3]
    assert second["rows_has_more"] is False
    assert second["nodes"] == []
