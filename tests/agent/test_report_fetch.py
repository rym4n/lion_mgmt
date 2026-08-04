# coding=utf-8
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.agent import report_fetch


def test_placeholder_regex_captures_key_and_path():
    match = report_fetch.FETCH_PLACEHOLDER_RE.search("总量 {alarm_count.data.total} 起")
    assert match.group(1) == "alarm_count"
    assert match.group(2) == ".data.total"


def test_get_fetch_value_by_path_walks_dict_and_list():
    data = {"rows": [{"name": "web"}]}
    assert report_fetch.get_fetch_value_by_path(data, ".rows.0.name") == "web"
    assert report_fetch.get_fetch_value_by_path(data, "") == data
    assert (
        report_fetch.get_fetch_value_by_path(data, ".rows.9.name")
        is report_fetch.MISSING_FETCH_VALUE
    )


def test_stringify_fetch_serializes_containers():
    assert report_fetch.stringify_fetch(None) == ""
    assert report_fetch.stringify_fetch(12) == "12"
    assert report_fetch.stringify_fetch({"a": 1}) == '{"a":1}'


@pytest.mark.asyncio
async def test_resolve_fetch_values_returns_empty_without_placeholders():
    result = await report_fetch.resolve_fetch_values("纯文本无占位符", {}, "t1", "u1")
    assert result == {}


@pytest.mark.asyncio
async def test_resolve_fetch_values_rejects_missing_config():
    with pytest.raises(CustomError) as excinfo:
        await report_fetch.resolve_fetch_values("{unknown_key.result}", {}, "t1", "u1")
    assert excinfo.value.enum == ErrorCode.SERVER_ERROR


@pytest.mark.asyncio
async def test_resolve_fetch_values_executes_actions_concurrently(monkeypatch):
    calls = []

    async def fake_execute(action_cfg, tenant_id, operator_id):
        calls.append(action_cfg["action"])
        return '{"data": {"total": 7}}'

    monkeypatch.setattr(report_fetch, "execute_fetch_action", fake_execute)
    results = await report_fetch.resolve_fetch_values(
        "{alarm.total}",
        {"alarm": {"action": "count"}},
        "t1",
        "u1",
    )
    assert calls == ["count"]
    assert results == {"alarm": {"total": 7}}


@pytest.mark.asyncio
async def test_resolve_fetch_values_rejects_missing_path(monkeypatch):
    async def fake_execute(action_cfg, tenant_id, operator_id):
        return '{"data": {"total": 7}}'

    monkeypatch.setattr(report_fetch, "execute_fetch_action", fake_execute)
    with pytest.raises(CustomError) as excinfo:
        await report_fetch.resolve_fetch_values(
            "{alarm.absent}",
            {"alarm": {"action": "count"}},
            "t1",
            "u1",
        )
    assert excinfo.value.enum == ErrorCode.SERVER_ERROR


def test_build_placeholder_replacer_stringifies_matched_path():
    replacer = report_fetch.build_placeholder_replacer({"alarm": {"total": 7}})
    match = report_fetch.FETCH_PLACEHOLDER_RE.search("{alarm.total}")
    assert replacer(match) == "7"
    unknown = report_fetch.FETCH_PLACEHOLDER_RE.search("{other.total}")
    assert replacer(unknown) is None
