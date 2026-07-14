# coding=utf-8
import asyncio
import base64
import io
from pathlib import Path
import threading
import time

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Inches, Pt, RGBColor
from docx.text.run import Run
from lxml import html as lxml_html
from PIL import Image

from services.agent import docx_converter
from services.agent.docx_converter import (
    DocxConversionError,
    decode_docx_base64,
    docx_to_html,
    html_to_docx,
    replace_paragraph_text_matches,
)


@pytest.fixture
def image_data_uri() -> str:
    image = io.BytesIO()
    Image.new("RGB", (3, 2), (220, 20, 60)).save(image, format="PNG")
    return "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")


@pytest.fixture
def rich_html(image_data_uri: str) -> str:
    return f"""
    <h1 style="text-align: center; line-height: 1.5">
      <span style="font-family: Noto Sans CJK SC; font-size: 20pt; color: #123456"><strong>运行报告</strong></span>
    </h1>
    <p style="text-align: justify; line-height: 1.8">
      正文 <strong>粗体</strong><em>斜体</em><u>下划线</u><s>删除线</s>
      <sup>上标</sup><sub>下标</sub><mark style="background-color: #00FF00">高亮</mark>
      <a href="https://example.com/report?id=1">详情链接</a><br>
      <img src="{image_data_uri}" alt="告警趋势" style="width: 24pt; height: 16pt">
    </p>
    <ul><li>节点一</li><li>节点二<ul><li>子节点</li></ul></li></ul>
    <ol><li>第一步</li><li>第二步</li></ol>
    <table><tbody>
      <tr><th>指标</th><th>结果</th></tr>
      <tr><td>可用率</td><td><strong>99.99%</strong></td></tr>
    </tbody></table>
    """


@pytest.fixture
def rich_docx_bytes(image_data_uri: str) -> bytes:
    document = Document()
    document.add_heading("既有 Word 报告", level=2)
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.paragraph_format.line_spacing = 1.25
    formatted = paragraph.add_run("组合格式")
    formatted.bold = True
    formatted.italic = True
    formatted.underline = True
    formatted.font.strike = True
    formatted.font.name = "Microsoft YaHei"
    formatted.font.size = Pt(15)
    formatted.font.color.rgb = RGBColor(0x65, 0x43, 0x21)
    formatted.font.highlight_color = WD_COLOR_INDEX.YELLOW
    superscript = paragraph.add_run("2")
    superscript.font.superscript = True

    relationship_id = paragraph.part.relate_to(
        "https://example.com/original", RT.HYPERLINK, is_external=True
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    hyperlink_run_element = OxmlElement("w:r")
    hyperlink.append(hyperlink_run_element)
    hyperlink_run = Run(hyperlink_run_element, paragraph)
    hyperlink_run.text = "原始链接"
    paragraph._p.append(hyperlink)

    document.add_paragraph("原始项目", style="List Bullet")
    document.add_paragraph("原始步骤", style="List Number")
    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"

    raw_image = base64.b64decode(image_data_uri.split(",", 1)[1])
    image_run = document.add_paragraph().add_run()
    shape = image_run.add_picture(io.BytesIO(raw_image), width=Inches(0.5))
    shape._inline.docPr.set("descr", "原始图片")

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _paragraph_with_text(document: Document, text: str):
    return next(
        paragraph for paragraph in document.paragraphs if text in paragraph.text
    )


def _run_with_text(paragraph, text: str):
    return next(run for run in paragraph.runs if text in run.text)


def test_rich_html_docx_round_trip_preserves_editor_contract(rich_html: str):
    docx_bytes = html_to_docx(rich_html)
    document = Document(io.BytesIO(docx_bytes))

    heading = _paragraph_with_text(document, "运行报告")
    assert heading.style.name == "Heading 1"
    assert heading.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert heading.paragraph_format.line_spacing == pytest.approx(1.5)
    heading_run = _run_with_text(heading, "运行报告")
    assert heading_run.bold is True
    assert heading_run.font.name == "Noto Sans CJK SC"
    assert heading_run.font.size.pt == pytest.approx(20)
    assert str(heading_run.font.color.rgb) == "123456"

    paragraph = _paragraph_with_text(document, "正文")
    assert paragraph.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert paragraph.paragraph_format.line_spacing == pytest.approx(1.8)
    assert _run_with_text(paragraph, "粗体").bold is True
    assert _run_with_text(paragraph, "斜体").italic is True
    assert _run_with_text(paragraph, "下划线").underline is True
    assert _run_with_text(paragraph, "删除线").font.strike is True
    assert _run_with_text(paragraph, "上标").font.superscript is True
    assert _run_with_text(paragraph, "下标").font.subscript is True
    highlight_run = _run_with_text(paragraph, "高亮")
    shading = highlight_run._r.get_or_add_rPr().find(qn("w:shd"))
    assert shading is not None
    assert shading.get(qn("w:fill")) == "00FF00"

    external_links = [
        relationship.target_ref
        for relationship in document.part.rels.values()
        if relationship.reltype == RT.HYPERLINK and relationship.is_external
    ]
    assert external_links == ["https://example.com/report?id=1"]
    assert len(document.inline_shapes) == 1
    assert document.inline_shapes[0]._inline.docPr.get("descr") == "告警趋势"
    assert [
        paragraph.style.name
        for paragraph in document.paragraphs
        if "节点" in paragraph.text
    ] == [
        "List Bullet",
        "List Bullet",
        "List Bullet 2",
    ]
    assert [
        paragraph.style.name
        for paragraph in document.paragraphs
        if "步" in paragraph.text
    ] == [
        "List Number",
        "List Number",
    ]
    assert len(document.tables) == 1
    assert document.tables[0].cell(1, 0).text == "可用率"
    assert document.tables[0].cell(1, 1).paragraphs[0].runs[0].bold is True

    converted_html = docx_to_html(docx_bytes)
    root = lxml_html.fragment_fromstring(converted_html, create_parent="div")
    assert root.xpath("./h1[contains(@style, 'text-align: center')]")
    assert root.xpath(".//strong[normalize-space()='运行报告']")
    assert root.xpath(".//em[normalize-space()='斜体']")
    assert root.xpath(".//u[normalize-space()='下划线']")
    assert root.xpath(".//s[normalize-space()='删除线']")
    assert root.xpath(".//sup[normalize-space()='上标']")
    assert root.xpath(".//sub[normalize-space()='下标']")
    assert root.xpath(".//mark[contains(@style, '#00FF00')]")
    assert root.xpath(".//a[@href='https://example.com/report?id=1']")
    assert root.xpath(".//ul/li[normalize-space()='节点一']")
    assert root.xpath(".//ul/li/ul/li[normalize-space()='子节点']")
    assert root.xpath(".//ol/li[normalize-space()='第一步']")
    assert len(root.xpath(".//table/tbody/tr")) == 2
    images = root.xpath(".//img")
    assert len(images) == 1
    assert images[0].get("src", "").startswith("data:image/png;base64,")
    assert images[0].get("alt") == "告警趋势"

    second_docx = html_to_docx(converted_html)
    second_document = Document(io.BytesIO(second_docx))
    assert _paragraph_with_text(second_document, "运行报告").style.name == "Heading 1"
    assert len(second_document.inline_shapes) == 1
    assert len(second_document.tables) == 1


def test_existing_rich_docx_import_and_reexport_preserves_supported_content(
    rich_docx_bytes: bytes,
):
    converted_html = docx_to_html(rich_docx_bytes)
    root = lxml_html.fragment_fromstring(converted_html, create_parent="div")

    assert root.xpath("./h2[normalize-space()='既有 Word 报告']")
    assert root.xpath(".//p[contains(@style, 'text-align: right')]")
    assert root.xpath(".//strong//em//u//s[normalize-space()='组合格式']")
    formatted_span = root.xpath(
        ".//span[contains(@style, 'font-family: Microsoft YaHei')]"
    )[0]
    assert "font-size: 15pt" in formatted_span.get("style")
    assert "color: #654321" in formatted_span.get("style")
    assert root.xpath(".//mark[contains(@style, '#FFFF00')]")
    assert root.xpath(".//sup[normalize-space()='2']")
    assert root.xpath(
        ".//a[@href='https://example.com/original'][normalize-space()='原始链接']"
    )
    assert root.xpath(".//ul/li[normalize-space()='原始项目']")
    assert root.xpath(".//ol/li[normalize-space()='原始步骤']")
    assert root.xpath(".//table/tbody/tr/td[normalize-space()='A']")
    assert root.xpath(".//img[@alt='原始图片']")

    reexported = Document(io.BytesIO(html_to_docx(converted_html)))
    assert _paragraph_with_text(reexported, "既有 Word 报告").style.name == "Heading 2"
    assert len(reexported.inline_shapes) == 1
    assert len(reexported.tables) == 1


@pytest.mark.parametrize(
    "content",
    ["", "not-base64", "YWJj\n", base64.b64encode(b"not a docx").decode("ascii")],
)
def test_docx_base64_validation_rejects_invalid_content(content: str):
    with pytest.raises(DocxConversionError):
        decode_docx_base64(content)


@pytest.mark.parametrize(
    "html_content",
    [
        "",
        "<script>alert(1)</script>",
        '<p onclick="alert(1)">x</p>',
        '<a href="javascript:alert(1)">x</a>',
        '<img src="file:///etc/passwd">',
        '<span style="color: url(javascript:alert(1))">x</span>',
        '<img src="data:image/png;base64,YWJj">',
    ],
)
def test_html_validation_rejects_unsafe_or_invalid_content(html_content: str):
    with pytest.raises(DocxConversionError):
        html_to_docx(html_content)


def test_html_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(docx_converter, "MAX_HTML_BYTES", 16)
    with pytest.raises(DocxConversionError):
        html_to_docx("<p>12345678901234567890</p>")


def test_actual_tiptap_shapes_round_trip_without_remote_image_download():
    editor_html = """
    <p data-unknown="stripped">
      普通 <code>const answer = 42</code>
      <mark data-color="#22C55E" style="background-color: #22C55E; color: inherit">通过</mark>
      <a href="docs.example.com/report">无协议链接</a>
      <img src="//cdn.example.com/report.png" alt="外链图" style="width: 160px; height: 90px">
    </p>
    <pre><code class="language-python">if ready:\n    run()</code></pre>
    <hr>
    <ul data-type="taskList">
      <li data-type="taskItem" data-checked="true">
        <label><input type="checkbox" checked="checked"><span></span></label>
        <div><p>已完成</p></div>
      </li>
      <li data-type="taskItem" data-checked="false">
        <label><input type="checkbox"><span></span></label>
        <div><p>待处理</p></div>
      </li>
    </ul>
    <table style="min-width: 75px">
      <colgroup>
        <col style="min-width: 25px; width: 120px">
        <col style="min-width: 25px">
      </colgroup>
      <tbody><tr><th>名称</th><th>状态</th></tr><tr><td>任务</td><td>完成</td></tr></tbody>
    </table>
    """

    docx_bytes = html_to_docx(editor_html)
    document = Document(io.BytesIO(docx_bytes))
    ordinary = _paragraph_with_text(document, "普通")
    assert (
        _run_with_text(ordinary, "const answer = 42").style.name == "AIOE Inline Code"
    )
    assert (
        _run_with_text(ordinary, "通过")._r.rPr.find(qn("w:shd")).get(qn("w:fill"))
        == "22C55E"
    )
    assert _paragraph_with_text(document, "if ready:").style.name == "AIOE Code Block"
    assert _paragraph_with_text(document, "已完成").text.startswith("☑ ")
    assert _paragraph_with_text(document, "待处理").text.startswith("☐ ")
    assert document.tables[0].cell(1, 0).text == "任务"

    external_hyperlinks = [
        relationship.target_ref
        for relationship in document.part.rels.values()
        if relationship.reltype == RT.HYPERLINK and relationship.is_external
    ]
    assert external_hyperlinks == ["https://docs.example.com/report"]
    external_images = [
        relationship.target_ref
        for relationship in document.part.rels.values()
        if relationship.reltype == RT.IMAGE and relationship.is_external
    ]
    assert external_images == ["https://cdn.example.com/report.png"]

    converted_html = docx_to_html(docx_bytes)
    root = lxml_html.fragment_fromstring(converted_html, create_parent="div")
    assert root.xpath(".//code[normalize-space()='const answer = 42']")
    assert root.xpath("./pre/code[contains(., 'if ready:')]")
    assert root.xpath("./hr")
    assert root.xpath(".//mark[contains(@style, 'background-color: #22C55E')]")
    assert root.xpath(".//a[@href='https://docs.example.com/report']")
    assert root.xpath(
        ".//img[@src='https://cdn.example.com/report.png'][@alt='外链图']"
    )
    assert root.xpath(
        ".//ul[@data-type='taskList']/li[@data-checked='true']//p[normalize-space()='已完成']"
    )
    assert root.xpath(
        ".//ul[@data-type='taskList']/li[@data-checked='false']//p[normalize-space()='待处理']"
    )
    assert root.xpath(".//table/tbody/tr/td[normalize-space()='任务']")
    assert "data-unknown" not in converted_html
    assert "data-color" not in converted_html


@pytest.mark.parametrize(
    "table_html",
    [
        "<table><tr><td rowspan='2'>越界</td></tr></table>",
        "<table><tr><td>A</td><td rowspan='2'>B</td></tr><tr><td colspan='2'>重叠</td></tr></table>",
        "<table><tr><td colspan='101'>过宽</td></tr></table>",
    ],
)
def test_invalid_table_spans_raise_contract_error(table_html: str):
    with pytest.raises(DocxConversionError):
        html_to_docx(table_html)


def test_table_row_limit_raises_contract_error(monkeypatch):
    monkeypatch.setattr(docx_converter, "MAX_TABLE_ROWS", 2)
    with pytest.raises(DocxConversionError, match="row limit"):
        html_to_docx(
            "<table><tr><td>1</td></tr><tr><td>2</td></tr><tr><td>3</td></tr></table>"
        )


def test_total_table_cell_limit_raises_contract_error(monkeypatch):
    monkeypatch.setattr(docx_converter, "MAX_TOTAL_TABLE_CELLS", 3)
    with pytest.raises(DocxConversionError, match="cell limit"):
        html_to_docx(
            "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>"
        )


def test_docx_structure_limit_rejects_before_python_docx_load(monkeypatch):
    document = Document()
    document.add_paragraph("first")
    document.add_paragraph("second")
    output = io.BytesIO()
    document.save(output)

    monkeypatch.setattr(docx_converter, "MAX_DOCX_PARAGRAPHS", 1)
    monkeypatch.setattr(
        docx_converter,
        "Document",
        lambda *_args, **_kwargs: pytest.fail(
            "python-docx must not load structurally oversized input"
        ),
    )
    with pytest.raises(DocxConversionError, match="structure limit"):
        docx_converter.validate_docx_bytes(output.getvalue())


def test_docx_xml_node_limit_rejects_before_python_docx_load(monkeypatch):
    document = Document()
    document.add_paragraph("node budget")
    output = io.BytesIO()
    document.save(output)

    monkeypatch.setattr(docx_converter, "MAX_DOCX_XML_NODES", 3)
    monkeypatch.setattr(
        docx_converter,
        "Document",
        lambda *_args, **_kwargs: pytest.fail(
            "python-docx must not load node-heavy input"
        ),
    )
    with pytest.raises(DocxConversionError, match="structure limit"):
        docx_converter.validate_docx_bytes(output.getvalue())


def test_conversion_deadline_is_cooperatively_enforced(monkeypatch):
    monkeypatch.setattr(docx_converter, "MAX_CONVERSION_SECONDS", -1)
    with pytest.raises(DocxConversionError, match="timed out"):
        html_to_docx("<p>deadline</p>")


def test_medium_docx_conversion_stays_within_regression_budget():
    document = Document()
    for index in range(400):
        paragraph = document.add_paragraph()
        bold = paragraph.add_run(f"paragraph {index} ")
        bold.bold = True
        italic = paragraph.add_run("value")
        italic.italic = True
    output = io.BytesIO()
    document.save(output)

    started = time.perf_counter()
    converted = docx_to_html(output.getvalue())
    elapsed = time.perf_counter() - started

    assert "paragraph 399" in converted
    assert elapsed < 5.0


def test_docx_conversion_runner_has_bounded_capacity(monkeypatch):
    from services.agent import views

    monkeypatch.setattr(views, "_DOCX_CONVERSION_WORKERS", asyncio.BoundedSemaphore(1))
    monkeypatch.setattr(views, "_DOCX_CONVERSION_CAPACITY", asyncio.BoundedSemaphore(1))
    started = threading.Event()
    release = threading.Event()

    def blocking_converter(value):
        started.set()
        release.wait(timeout=2)
        return value

    async def scenario():
        first = asyncio.create_task(
            views._run_docx_conversion(blocking_converter, "first")
        )
        try:
            while not started.is_set():
                await asyncio.sleep(0)
            with pytest.raises(views._DocxConversionBusyError):
                await views._run_docx_conversion(lambda value: value, "second")
        finally:
            release.set()
        assert await first == "first"

    asyncio.run(scenario())


def test_replace_paragraph_text_matches_preserves_surrounding_run_styles():
    document = Document()
    paragraph = document.add_paragraph()
    prefix = paragraph.add_run("前缀 ")
    prefix.bold = True
    placeholder_start = paragraph.add_run("{fetch.")
    placeholder_start.italic = True
    placeholder_end = paragraph.add_run("value}")
    placeholder_end.underline = True
    suffix = paragraph.add_run(" / {fetch.value} 后缀")
    suffix.font.color.rgb = RGBColor(0x12, 0x34, 0x56)

    pattern = docx_converter.re.compile(r"\{([a-z]+)\.([a-z]+)\}")
    assert (
        replace_paragraph_text_matches(paragraph, pattern, lambda match: "替换值")
        is True
    )
    assert paragraph.text == "前缀 替换值 / 替换值 后缀"
    assert prefix.text == "前缀 " and prefix.bold is True
    assert placeholder_start.text == "替换值" and placeholder_start.italic is True
    assert placeholder_end.text == "" and placeholder_end.underline is True
    assert suffix.text == " / 替换值 后缀"
    assert str(suffix.font.color.rgb) == "123456"


def test_replace_paragraph_text_matches_can_skip_unconfigured_split_placeholder():
    document = Document()
    paragraph = document.add_paragraph()
    first = paragraph.add_run("保留 {missing.")
    first.bold = True
    second = paragraph.add_run("value}")
    second.italic = True
    pattern = docx_converter.re.compile(r"\{([a-z]+)\.([a-z]+)\}")

    assert (
        replace_paragraph_text_matches(paragraph, pattern, lambda _match: None) is True
    )
    assert first.text == "保留 {missing." and first.bold is True
    assert second.text == "value}" and second.italic is True


def test_resolve_fetch_placeholders_keeps_run_formatting(monkeypatch):
    from services.agent import views

    document = Document()
    paragraph = document.add_paragraph()
    prefix = paragraph.add_run("前缀 ")
    prefix.bold = True
    start = paragraph.add_run("{fetch.")
    start.italic = True
    end = paragraph.add_run("value}")
    end.underline = True
    suffix = paragraph.add_run(" 后缀")
    suffix.font.color.rgb = RGBColor(0xAB, 0xCD, 0xEF)
    source = io.BytesIO()
    document.save(source)

    async def fake_fetch(*_args, **_kwargs):
        return '{"value":"42"}'

    monkeypatch.setattr(views, "_execute_fetch_action", fake_fetch)
    resolved = asyncio.run(
        views._resolve_fetch_placeholders(
            source.getvalue(), {"fetch": {}}, "tenant-1", "user-1"
        )
    )
    result = Document(io.BytesIO(resolved)).paragraphs[0]
    assert result.text == "前缀 42 后缀"
    assert result.runs[0].bold is True and result.runs[0].text == "前缀 "
    assert result.runs[1].italic is True and result.runs[1].text == "42"
    assert result.runs[2].underline is True and result.runs[2].text == ""
    assert str(result.runs[3].font.color.rgb) == "ABCDEF"


def test_docx_conversion_routes_have_fixed_contract_and_permissions():
    source = (
        Path(__file__).resolve().parents[2] / "services/agent/views.py"
    ).read_text()
    to_html = source[source.index('@agent_bp.post("/report/docx/to-html")') :]
    to_html = to_html[: to_html.index('@agent_bp.post("/report/docx/from-html")')]
    from_html = source[source.index('@agent_bp.post("/report/docx/from-html")') :]
    from_html = from_html[: from_html.index('@agent_bp.post("/report/save")')]

    assert '@requires_permission("agent:r")' in to_html
    assert 'await _run_docx_conversion(docx_base64_to_html, data["content"])' in to_html
    assert "ErrorCode.DOCX_CONVERSION_BUSY" in to_html
    assert 'return response_ok({"html": html})' in to_html
    assert '@requires_permission("agent:u")' in from_html
    assert 'await _run_docx_conversion(html_to_docx_base64, data["html"])' in from_html
    assert "ErrorCode.DOCX_CONVERSION_BUSY" in from_html
    assert 'return response_ok({"content": content})' in from_html
