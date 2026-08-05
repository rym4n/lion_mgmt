# coding=utf-8
import inspect

import pytest

from services.agent import views as agent_views


def test_export_report_document_branches_on_template():
    source = inspect.getsource(agent_views.export_report_document)
    assert "_resolve_export_template" in source, "导出必须反查关联模板"
    assert "render_report_docx" in source, "有模板时必须走批注渲染流水线"
    assert "_report_template_base_docx_path" in source, (
        "关联模板导出必须解析原始 DOCX 外壳"
    )
    assert "_resolve_fetch_placeholders" in source, "无模板时必须回落旧取数路径"


def test_resolve_export_template_prefers_latest_by_utime():
    source = inspect.getsource(agent_views._resolve_export_template)
    assert "source_templates" in source or "ReportTemplate.filter" in source
    assert "deleted=False" in source, "反查必须过滤软删除"
    assert "tenant_id" in source, "反查必须做租户隔离"
    assert "-utime" in source, "多命中时取最新模板"


def test_schedule_runner_reaches_render_pipeline_through_export():
    """定时汇报经 send_report_notify_for_document 复用 export_report_document。"""
    from services.agent import schedule_runner

    source = inspect.getsource(schedule_runner.execute_report_schedule)
    assert "send_report_notify_for_document" in source
    notify_source = inspect.getsource(agent_views.send_report_notify_for_document)
    assert "export_report_document" in notify_source


@pytest.mark.asyncio
async def test_export_uses_pipeline_when_template_exists(monkeypatch, tmp_path):
    captured = {}

    class FakeTemplate:
        content_html = '<p><mark data-annotation-ids="a1">原始</mark></p>'
        fetch_config = {"alarm": {"action": "count"}}
        annotations = [{"id": "a1", "comment": "改写", "status": "open"}]
        preserve_source_layout = True
        source_docx_path = "source.docx"

    class FakeDoc:
        name = "月度报告.docx"
        file_path = "月度报告.docx"
        fetch_config = {}

    async def fake_resolve_template(doc, tenant_id):
        return FakeTemplate()

    async def fake_render(**kwargs):
        captured.update(kwargs)
        return b"PK-fake-docx"

    async def fake_upload(file_bytes, bucket, name, prefix=None, content_type=None):
        captured["uploaded"] = file_bytes
        return "/files/user/reports/x.docx"

    async def fake_visible_agent(tenant_id, agent_key=None, agent_id=None):
        class FakeAgent:
            agent_key = "report_assistant"

        return FakeAgent()

    report_file = tmp_path / "月度报告.docx"
    report_file.write_bytes(b"PK-canonical")

    monkeypatch.setattr(agent_views, "_resolve_export_template", fake_resolve_template)
    monkeypatch.setattr(agent_views, "render_report_docx", fake_render)
    monkeypatch.setattr(agent_views, "upload_to_s3", fake_upload)
    monkeypatch.setattr(agent_views, "_get_visible_agent", fake_visible_agent)
    monkeypatch.setattr(agent_views, "_user_report_dir", lambda t, o: tmp_path)
    monkeypatch.setattr(
        agent_views, "_owned_report_path", lambda directory, rel: directory / rel
    )

    result = await agent_views.export_report_document(
        FakeDoc(), tenant_id="t1", owner_id="u1", operator_id="u1", fmt="docx"
    )

    assert captured["annotations"] == FakeTemplate.annotations
    assert captured["content_html"] == FakeTemplate.content_html
    assert captured["fetch_config"] == FakeTemplate.fetch_config
    assert captured["base_docx_path"] == tmp_path / "source.docx"
    assert captured["uploaded"] == b"PK-fake-docx"
    assert result["format"] == "docx"
    assert result["name"] == "月度报告.docx"


@pytest.mark.asyncio
async def test_export_falls_back_without_template(monkeypatch, tmp_path):
    captured = {}

    class FakeDoc:
        name = "历史报告.docx"
        file_path = "历史报告.docx"
        fetch_config = {"alarm": {"action": "count"}}

    async def fake_resolve_template(doc, tenant_id):
        return None

    async def fake_resolve_fetch(docx_bytes, fetch_config, tenant_id, operator_id):
        captured["legacy_called"] = True
        return b"PK-legacy"

    async def fake_render(**kwargs):
        raise AssertionError("无关联模板时不应调用流水线")

    async def fake_upload(file_bytes, bucket, name, prefix=None, content_type=None):
        captured["uploaded"] = file_bytes
        return "/files/user/reports/y.docx"

    report_file = tmp_path / "历史报告.docx"
    report_file.write_bytes(b"PK-canonical")

    monkeypatch.setattr(agent_views, "_resolve_export_template", fake_resolve_template)
    monkeypatch.setattr(agent_views, "_resolve_fetch_placeholders", fake_resolve_fetch)
    monkeypatch.setattr(agent_views, "render_report_docx", fake_render)
    monkeypatch.setattr(agent_views, "upload_to_s3", fake_upload)
    monkeypatch.setattr(agent_views, "_user_report_dir", lambda t, o: tmp_path)
    monkeypatch.setattr(agent_views, "_owned_report_path", lambda d, p: report_file)

    result = await agent_views.export_report_document(
        FakeDoc(), tenant_id="t1", owner_id="u1", operator_id="u1", fmt="docx"
    )

    assert captured["legacy_called"] is True
    assert captured["uploaded"] == b"PK-legacy"
    assert result["format"] == "docx"


@pytest.mark.asyncio
async def test_export_pdf_converts_pipeline_output(monkeypatch, tmp_path):
    captured = {}

    class FakeTemplate:
        content_html = "<p>正文</p>"
        fetch_config = {}
        annotations = []

    class FakeDoc:
        name = "月度报告.docx"
        file_path = "月度报告.docx"
        fetch_config = {}

    async def fake_render(**kwargs):
        return b"PK-fake-docx"

    async def fake_pdf(docx_bytes):
        captured["pdf_input"] = docx_bytes
        return b"%PDF-fake"

    async def fake_upload(file_bytes, bucket, name, prefix=None, content_type=None):
        captured["uploaded"] = file_bytes
        captured["content_type"] = content_type
        return "/files/user/reports/z.pdf"

    async def fake_visible_agent(tenant_id, agent_key=None, agent_id=None):
        class FakeAgent:
            agent_key = "report_assistant"

        return FakeAgent()

    report_file = tmp_path / "月度报告.docx"
    report_file.write_bytes(b"PK-canonical")

    monkeypatch.setattr(
        agent_views,
        "_resolve_export_template",
        lambda doc, tenant_id: _async(FakeTemplate()),
    )
    monkeypatch.setattr(agent_views, "render_report_docx", fake_render)
    monkeypatch.setattr(agent_views, "_docx_to_pdf_bytes", fake_pdf)
    monkeypatch.setattr(agent_views, "upload_to_s3", fake_upload)
    monkeypatch.setattr(agent_views, "_get_visible_agent", fake_visible_agent)
    monkeypatch.setattr(agent_views, "_user_report_dir", lambda t, o: tmp_path)
    monkeypatch.setattr(agent_views, "_owned_report_path", lambda d, p: report_file)

    result = await agent_views.export_report_document(
        FakeDoc(), tenant_id="t1", owner_id="u1", operator_id="u1", fmt="pdf"
    )

    assert captured["pdf_input"] == b"PK-fake-docx"
    assert captured["uploaded"] == b"%PDF-fake"
    assert captured["content_type"] == "application/pdf"
    assert result["name"] == "月度报告.pdf"


async def _async(value):
    return value
