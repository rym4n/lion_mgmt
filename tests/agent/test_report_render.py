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


def test_collect_annotation_marks_groups_multiple_marks_per_annotation():
    html = (
        '<p><mark data-annotation-ids="a1">前半</mark>'
        '<strong><mark data-annotation-ids="a1">后半</mark></strong></p>'
    )
    root = report_render._parse_document(html)
    marks = report_render.collect_annotation_marks(root)
    assert list(marks.keys()) == ["a1"]
    assert len(marks["a1"]) == 2
    assert report_render.annotation_region_text(marks["a1"]) == "前半后半"


def test_collect_annotation_marks_reads_singular_attribute():
    html = '<p><mark data-annotation-id="a1">文字</mark></p>'
    root = report_render._parse_document(html)
    marks = report_render.collect_annotation_marks(root)
    assert list(marks.keys()) == ["a1"]


def test_collect_annotation_marks_rejects_overlapping_annotation():
    html = '<p><mark data-annotation-ids="a1 a2">重叠</mark></p>'
    root = report_render._parse_document(html)
    with pytest.raises(CustomError) as excinfo:
        report_render.collect_annotation_marks(root)
    assert excinfo.value.enum == ErrorCode.REPORT_ANNOTATION_OVERLAP


def test_active_annotations_filters_by_status_and_comment():
    annotations = [
        {"id": "a1", "comment": "补充趋势", "status": "open"},
        {"id": "a2", "comment": "", "status": "open"},
        {"id": "a3", "comment": "已处理", "status": "applied"},
        {"id": "a4", "comment": "已完成", "status": "resolved"},
        {"id": "a5", "comment": "缺省状态"},
    ]
    active = report_render.active_annotations(annotations)
    assert [item["id"] for item in active] == ["a1"]


def test_annotation_mode_defaults_to_overwrite():
    assert report_render.annotation_mode({}) == report_render.ANNOTATION_MODE_OVERWRITE
    assert (
        report_render.annotation_mode({"mode": "append"})
        == report_render.ANNOTATION_MODE_APPEND
    )
    assert (
        report_render.annotation_mode({"mode": "invalid"})
        == report_render.ANNOTATION_MODE_OVERWRITE
    )


def test_annotation_region_text_spans_blocks():
    html = (
        '<p><mark data-annotation-ids="a1">第一段</mark></p>'
        '<p><mark data-annotation-ids="a1">第二段</mark></p>'
    )
    root = report_render._parse_document(html)
    marks = report_render.collect_annotation_marks(root)
    assert report_render.annotation_region_text(marks["a1"]) == "第一段第二段"
