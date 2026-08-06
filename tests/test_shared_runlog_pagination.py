# coding=utf-8

import inspect
from datetime import datetime, timezone

import pytest
from tortoise.expressions import Q

from libs.error_code.exception import CustomError
from libs.runlog_pagination import (
    decode_cursor,
    encode_cursor,
    node_summary,
    runlog_node_page,
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

    def annotate(self, **annotations):
        assert "node_type_rank" in annotations
        return self

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    def limit(self, row_limit):
        self.row_limit = row_limit
        return self

    async def values(self, *fields):
        assert self.ordering == ("node_type_rank", "ctime", "id")
        ranks = {"start-node": 0, "end-node": 2}
        rows = [row for row in self.rows if matches_q(row, self.condition)]
        rows.sort(
            key=lambda row: (
                ranks.get(row["node_type"], 1),
                row["ctime"],
                row["id"],
            )
        )
        return [
            {field: row.get(field) for field in fields}
            for row in rows[: self.row_limit]
        ]


class SharedRunlogNode:
    rows = []

    @classmethod
    def filter(cls, condition):
        return SharedRunlogQuery(cls.rows, condition)


async def shared_node_page(*, cursor=None, limit=50):
    return await runlog_node_page(
        SharedRunlogNode,
        Q(),
        run_id="run-1",
        cursor=cursor,
        limit=limit,
        cursor_kind="test-node",
        cursor_scope={"tenant_id": "tenant-1"},
    )


async def create_node(node_id, node_type, ctime):
    SharedRunlogNode.rows.append(
        {
            "id": node_id,
            "run_id": "run-1",
            "node_id": node_id,
            "node_name": node_id,
            "node_type": node_type,
            "ctime": ctime,
            "utime": ctime,
        }
    )


@pytest.fixture(autouse=True)
def clear_runlog_rows():
    SharedRunlogNode.rows = []


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
        id="log-1",
    )

    payload = decode_cursor(cursor, "event-node", event_id="event-1", run_id="run-1")
    assert payload["id"] == "log-1"
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
        id="log-1",
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
