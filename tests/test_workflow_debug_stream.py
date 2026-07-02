# coding=utf-8

from datetime import datetime, timezone
from types import SimpleNamespace

from services.workflow.views import (
    _is_completed_flow_end_log,
    _should_stream_runlog,
)


def test_should_stream_runlog_resends_updated_terminal_state():
    sent_versions = {}
    log = SimpleNamespace(
        id="log-1",
        utime=datetime(2026, 7, 2, tzinfo=timezone.utc),
        status=1,
        result="执行中",
        end_time=None,
    )

    assert _should_stream_runlog(log, sent_versions) is True
    assert _should_stream_runlog(log, sent_versions) is False

    log.status = 0
    log.result = "执行成功"
    log.end_time = datetime(2026, 7, 2, 0, 0, 1, tzinfo=timezone.utc)

    assert _should_stream_runlog(log, sent_versions) is True


def test_completed_flow_end_log_requires_terminal_state():
    running_end_log = SimpleNamespace(
        node_type="end-node",
        playbook_version_id="flow-1",
        status=1,
        result="执行中",
    )
    finished_end_log = SimpleNamespace(
        node_type="end-node",
        playbook_version_id="flow-1",
        status=0,
        result="执行成功",
    )

    assert _is_completed_flow_end_log(running_end_log, "flow-1") is False
    assert _is_completed_flow_end_log(finished_end_log, "flow-1") is True
    assert _is_completed_flow_end_log(finished_end_log, "flow-2") is False
