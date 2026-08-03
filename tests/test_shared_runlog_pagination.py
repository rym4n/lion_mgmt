# coding=utf-8

import inspect

import pytest

from libs.error_code.exception import CustomError
from libs.runlog_pagination import decode_cursor, encode_cursor, node_summary
from services.event import views as event_views
from services.timed import views as timed_views
from services.warroom import views as warroom_views
from services.workflow import views as workflow_views


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
        ctime="2026-08-03T12:00:00+08:00",
        id="log-1",
    )

    payload = decode_cursor(cursor, "event-node", event_id="event-1", run_id="run-1")
    assert payload["id"] == "log-1"
    with pytest.raises(CustomError):
        decode_cursor(cursor, "event-node", event_id="event-2", run_id="run-1")


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
