# coding=utf-8
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.agent import report_fetch, report_render


@pytest.mark.asyncio
async def test_resolve_placeholders_replaces_span_text(monkeypatch):
    async def fake_execute(action_cfg, tenant_id, operator_id):
        return '{"data": {"total": 128}}'

    monkeypatch.setattr(report_fetch, "execute_fetch_action", fake_execute)
    html = (
        '<p>本月告警 '
        '<span data-placeholder-id="alarm" data-result-path="total">'
        "{alarm.total}</span> 起</p>"
    )
    result = await report_render.resolve_placeholders_in_html(
        html, {"alarm": {"action": "count"}}, "t1", "u1"
    )
    assert "128" in result
    assert "{alarm.total}" not in result
    assert 'data-placeholder-id="alarm"' in result


@pytest.mark.asyncio
async def test_resolve_placeholders_returns_html_unchanged_without_tokens():
    html = "<p>没有占位符</p>"
    result = await report_render.resolve_placeholders_in_html(html, {}, "t1", "u1")
    assert "没有占位符" in result


@pytest.mark.asyncio
async def test_resolve_placeholders_keeps_attribute_braces(monkeypatch):
    async def fake_execute(action_cfg, tenant_id, operator_id):
        return '{"data": 5}'

    monkeypatch.setattr(report_fetch, "execute_fetch_action", fake_execute)
    html = (
        '<p data-config="{alarm.data}">'
        '<span data-placeholder-id="alarm" data-result-path="">{alarm}</span>'
        "</p>"
    )
    result = await report_render.resolve_placeholders_in_html(
        html, {"alarm": {"action": "count"}}, "t1", "u1"
    )
    assert 'data-config="{alarm.data}"' in result
    assert ">5<" in result


@pytest.mark.asyncio
async def test_resolve_placeholders_fails_on_missing_config():
    html = "<p>{unknown.result}</p>"
    with pytest.raises(CustomError) as excinfo:
        await report_render.resolve_placeholders_in_html(html, {}, "t1", "u1")
    assert excinfo.value.enum == ErrorCode.SERVER_ERROR


@pytest.mark.asyncio
async def test_resolve_placeholders_replaces_token_split_across_nodes(monkeypatch):
    async def fake_execute(action_cfg, tenant_id, operator_id):
        return '{"data": {"total": 42}}'

    monkeypatch.setattr(report_fetch, "execute_fetch_action", fake_execute)
    html = "<p>{alarm<strong></strong>.total}</p>"
    result = await report_render.resolve_placeholders_in_html(
        html, {"alarm": {"action": "count"}}, "t1", "u1"
    )
    assert "42" in result
    assert "{alarm" not in result
