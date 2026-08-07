# coding=utf-8
"""报告渲染流水线：HTML 层取数 → 批注优化 → 剥批注痕迹 → HTML→docx。

依赖方向：report_render → report_fetch / docx_converter。
本模块不 import services.agent.views，避免循环导入。
"""

import asyncio
from pathlib import Path

from lxml import etree, html as lxml_html
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.agent import docx_converter, report_fetch
from services.digital_employee.lion_agent_adapter import execute_prompt
from services.llm_config.resolver import resolve_llm


def _parse_document(html: str):
    """把 HTML 片段解析为可遍历的根元素。空输入返回空 div。"""
    fragment = (html or "").strip()
    if not fragment:
        return lxml_html.fragment_fromstring("<div></div>")
    return lxml_html.fragment_fromstring(fragment, create_parent="div")


def _serialize_children(root) -> str:
    """序列化根元素的子节点，丢掉包裹用的 div 本身。"""
    parts = []
    if root.text:
        parts.append(root.text)
    for child in root:
        parts.append(etree.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


def _iter_text_nodes(root):
    """产出 (元素, 属性名) 二元组，属性名为 'text' 或 'tail'。"""
    for element in root.iter():
        if element.text:
            yield element, "text"
        if element.tail:
            yield element, "tail"


def _collect_text(root) -> str:
    return "".join(
        value
        for element, attr in _iter_text_nodes(root)
        for value in [getattr(element, attr) or ""]
    )


_EMPTY_MERGEABLE_TAGS = frozenset(
    {"strong", "em", "b", "i", "u", "s", "span", "mark", "code", "sub", "sup"}
)


def _drop_empty_inline_elements(root) -> None:
    """删除无文本、无子节点的内联空元素，使被其切断的占位符 token 重新连续。"""
    for element in list(root.iter()):
        if element is root or element.tag not in _EMPTY_MERGEABLE_TAGS:
            continue
        if element.text or len(element):
            continue
        parent = element.getparent()
        if parent is None:
            continue
        tail = element.tail or ""
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        parent.remove(element)


def _semantic_placeholder_token(element) -> str | None:
    placeholder_id = str(element.get("data-placeholder-id") or "").strip()
    if not placeholder_id:
        return None
    raw_path = element.get("data-result-path")
    result_path = "result" if raw_path is None else str(raw_path).strip()
    suffix = f".{result_path}" if result_path else ""
    token = f"{{{placeholder_id}{suffix}}}"
    if report_fetch.FETCH_PLACEHOLDER_RE.fullmatch(token) is None:
        logger.error("报告取数语义占位符格式非法 token=%s", token)
        raise CustomError(ErrorCode.SERVER_ERROR)
    return token


async def resolve_placeholders_in_html(
    html: str,
    fetch_config: dict,
    tenant_id: str,
    operator_id,
) -> str:
    """执行取数动作，把 HTML 文本节点里的 {key.path} 替换为真实结果。

    只改文本节点，不动标签与属性。无占位符时原样返回。
    """
    root = _parse_document(html)
    _drop_empty_inline_elements(root)
    semantic_placeholders = [
        (element, token)
        for element in root.iter()
        if (token := _semantic_placeholder_token(element)) is not None
    ]
    all_text = " ".join(
        [_collect_text(root), *(token for _, token in semantic_placeholders)]
    )
    results = await report_fetch.resolve_fetch_values(
        all_text, fetch_config, tenant_id, operator_id
    )
    if not results:
        return html

    replacer = report_fetch.build_placeholder_replacer(results)

    for element, token in semantic_placeholders:
        match = report_fetch.FETCH_PLACEHOLDER_RE.fullmatch(token)
        if match is None:
            raise CustomError(ErrorCode.SERVER_ERROR)
        value = report_fetch.get_fetch_value_by_path(
            results.get(match.group(1), report_fetch.MISSING_FETCH_VALUE),
            match.group(2) or "",
        )
        if value is report_fetch.MISSING_FETCH_VALUE:
            raise CustomError(ErrorCode.SERVER_ERROR)
        for child in list(element):
            element.remove(child)
        element.text = report_fetch.stringify_fetch(value)

    def _substitute(value: str) -> str:
        return report_fetch.FETCH_PLACEHOLDER_RE.sub(
            lambda match: (
                replacer(match) if replacer(match) is not None else match.group(0)
            ),
            value,
        )

    for element, attr in list(_iter_text_nodes(root)):
        current = getattr(element, attr) or ""
        replaced = _substitute(current)
        if replaced != current:
            setattr(element, attr, replaced)

    return _serialize_children(root)


ANNOTATION_MODE_OVERWRITE = "overwrite"
ANNOTATION_MODE_APPEND = "append"
_ANNOTATION_MODES = frozenset({ANNOTATION_MODE_OVERWRITE, ANNOTATION_MODE_APPEND})
_ANNOTATION_STATUS_ACTIVE = "open"


def _annotation_ids_of(element) -> list[str]:
    raw = element.get("data-annotation-ids") or element.get("data-annotation-id") or ""
    seen: list[str] = []
    for item in str(raw).split():
        value = item.strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def collect_annotation_marks(root) -> dict[str, list]:
    """按文档顺序收集每个批注 id 对应的文本或图片锚点。

    携带 2+ 批注 id 的锚点视为重叠批注，抛 REPORT_ANNOTATION_OVERLAP。
    """
    grouped: dict[str, list] = {}
    for element in root.iter():
        is_mark = element.tag == "mark"
        is_annotated_image = element.tag == "img" and (
            "data-annotation-ids" in element.attrib
            or "data-annotation-id" in element.attrib
        )
        if not (is_mark or is_annotated_image):
            continue
        ids = _annotation_ids_of(element)
        if not ids:
            continue
        if len(ids) > 1:
            logger.error("报告存在重叠批注 annotation_ids=%s", ids)
            raise CustomError(ErrorCode.REPORT_ANNOTATION_OVERLAP)
        grouped.setdefault(ids[0], []).append(element)
    return grouped


def active_annotations(annotations: list) -> list[dict]:
    """筛出参与生成的批注：status 显式为 open 且 comment 非空。"""
    if not isinstance(annotations, list):
        return []
    active = []
    for item in annotations:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != _ANNOTATION_STATUS_ACTIVE:
            continue
        if not str(item.get("comment") or "").strip():
            continue
        active.append(item)
    return active


def annotation_mode(annotation: dict) -> str:
    """读批注写入模式；缺省或非法值一律按覆盖写处理（向后兼容存量批注）。"""
    mode = str((annotation or {}).get("mode") or "").strip().lower()
    return mode if mode in _ANNOTATION_MODES else ANNOTATION_MODE_OVERWRITE


def _effective_annotation_mode(annotation: dict, marks: list) -> str:
    """图片是位置锚点，只能保留原图并在其后追加生成内容。"""
    if any(element.tag == "img" for element in marks):
        return ANNOTATION_MODE_APPEND
    return annotation_mode(annotation)


def annotation_region_text(marks: list) -> str:
    """拼接批注区域内的全部文字。"""
    return "".join("".join(mark.itertext()) for mark in marks)


AI_OUTPUT_ALLOWED_TAGS = frozenset({"p", "ul", "ol", "li", "strong", "em", "br"})
_AI_OUTPUT_BLOCK_TAGS = frozenset({"p", "ul", "ol"})
_AI_OUTPUT_DROP_ENTIRELY = frozenset(
    {"script", "style", "img", "iframe", "object", "embed", "svg"}
)


def _serialize_blocks(blocks: list) -> str:
    """把块级元素列表序列化为 HTML 字符串。"""
    return "".join(
        etree.tostring(block, encoding="unicode", method="html") for block in blocks
    )


def _merge_tail_into_previous(element) -> None:
    """删除元素前，把它的 tail 文字接到前一个兄弟或父节点上，避免丢字。"""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail or ""
    if not tail:
        return
    previous = element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail


def _strip_disallowed(element) -> None:
    """递归清洗：删危险元素、解包非白名单标签、清空全部属性。"""
    for child in list(element):
        if not isinstance(child.tag, str) or child.tag in _AI_OUTPUT_DROP_ENTIRELY:
            _merge_tail_into_previous(child)
            element.remove(child)
            continue
        _strip_disallowed(child)
        if child.tag not in AI_OUTPUT_ALLOWED_TAGS:
            child.drop_tag()

    for name in list(element.attrib):
        del element.attrib[name]


def sanitize_ai_html(raw: str) -> list:
    """按窄白名单清洗模型输出，返回块级元素列表。

    放行 p / ul / ol / li / strong / em / br；其余标签解包保留文字；
    script / style / img 等连同内容一并删除；所有属性清空。
    """
    fragment = (raw or "").strip()
    if not fragment:
        return []

    try:
        root = _parse_document(fragment)
    except Exception as exc:
        logger.warning("批注优化结果 HTML 解析失败: %s", exc)
        return []

    _strip_disallowed(root)

    blocks: list = []
    leading = (root.text or "").strip()
    if leading:
        paragraph = etree.Element("p")
        paragraph.text = leading
        blocks.append(paragraph)

    for child in list(root):
        tail = (child.tail or "").strip()
        child.tail = None
        if child.tag in _AI_OUTPUT_BLOCK_TAGS:
            blocks.append(child)
        else:
            paragraph = etree.Element("p")
            root.remove(child)
            paragraph.append(child)
            blocks.append(paragraph)
        if tail:
            extra = etree.Element("p")
            extra.text = tail
            blocks.append(extra)

    return [block for block in blocks if "".join(block.itertext()).strip()]


ANNOTATION_POLISH_TIMEOUT = 120.0
_CONTEXT_CHARS = 300

_MODE_INSTRUCTIONS = {
    ANNOTATION_MODE_APPEND: (
        "写入模式：追加写。请保留【被批注内容】原文不变，只产出需要接在其后的新增内容。"
        "不要重复原文，不要改写原文。"
    ),
    ANNOTATION_MODE_OVERWRITE: (
        "写入模式：覆盖写。请直接产出用于替换【被批注内容】的新表述，"
        "覆盖后原文将不再出现。"
    ),
}


def build_annotation_prompt(
    region_text: str,
    comment: str,
    mode: str,
    before: str,
    after: str,
) -> str:
    """构造单条批注的优化提示词。"""
    lines = [
        "你在完善一份运维汇报报告。报告中的数据已经是本期真实取数结果，请基于这些数据写作。",
        "",
        _MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS[ANNOTATION_MODE_OVERWRITE]),
        "",
        "【被批注内容】",
        region_text,
        "",
        "【批注意见】",
        comment,
    ]
    if before:
        lines += ["", "【上文（仅供参考，不要改写）】", before]
    if after:
        lines += ["", "【下文（仅供参考，不要改写）】", after]
    lines += [
        "",
        "输出要求：",
        "1. 只输出正文 HTML 片段，不要任何解释、前后缀或代码块标记。",
        "2. 只允许这些标签：<p>、<ul>、<ol>、<li>、<strong>、<em>、<br>。",
        "3. 不要输出标题、表格、图片、链接，不要带任何属性。",
        "4. 保持专业、简洁的报告语气，与上下文风格一致。",
        (
            "5. 【被批注内容】是从段落中截取的片段，其前后文字会原样保留。"
            "若原片段末尾没有标点，你的输出末尾也不要加标点，避免与原文标点重复。"
        ),
    ]
    return "\n".join(lines)


def _containing_block(element, root):
    """从元素向上找到父节点为 root 的祖先，即它所属的顶层块。"""
    current = element
    while current is not None:
        parent = current.getparent()
        if parent is None:
            return None
        if parent is root:
            return current
        current = parent
    return None


def _block_context(block, root) -> tuple[str, str]:
    """取所属块的前后兄弟块文字，作为 prompt 上下文。"""
    if block is None:
        return "", ""
    previous = block.getprevious()
    following = block.getnext()
    before = "".join(previous.itertext()).strip() if previous is not None else ""
    after = "".join(following.itertext()).strip() if following is not None else ""
    return before[-_CONTEXT_CHARS:], after[:_CONTEXT_CHARS]


def _clear_element_content(element) -> None:
    """清空元素的文字与子节点，保留元素本身与其 tail。"""
    element.text = None
    for child in list(element):
        element.remove(child)


def _move_inline_into(mark, paragraph) -> None:
    """把 <p> 的内联内容搬进 mark，替换 mark 原有内容。"""
    _clear_element_content(mark)
    mark.text = paragraph.text
    for child in list(paragraph):
        paragraph.remove(child)
        mark.append(child)


def _insert_blocks_after(block, blocks: list) -> None:
    """把块级元素依次插到 block 之后，保持给定顺序。"""
    anchor = block
    for element in blocks:
        anchor.addnext(element)
        anchor = element


async def _polish_one(
    annotation: dict,
    marks: list,
    root,
    *,
    workspace_id: str,
    session_id: str,
    llm,
    timeout: float,
) -> list:
    """调 LLM 优化单条批注，返回清洗后的块级元素列表。"""
    requested_mode = annotation_mode(annotation)
    mode = _effective_annotation_mode(annotation, marks)
    if mode != requested_mode:
        logger.warning(
            "图片批注不支持覆盖写，按追加写处理 annotation_id=%s",
            annotation.get("id"),
        )
    region_text = annotation_region_text(marks)
    block = _containing_block(marks[0], root)
    before, after = _block_context(block, root)
    prompt = build_annotation_prompt(
        region_text,
        str(annotation.get("comment") or "").strip(),
        mode,
        before,
        after,
    )
    annotation_id = str(annotation.get("id") or "")
    raw = await execute_prompt(
        workspace_id=workspace_id,
        session_id=session_id,
        request_id=f"report-annotation:{session_id}:{annotation_id}",
        message=prompt,
        llm=llm,
        timeout=timeout,
    )
    blocks = sanitize_ai_html(str(raw or ""))
    if not blocks:
        logger.error("批注优化结果为空 annotation_id=%s", annotation_id)
        raise CustomError(ErrorCode.REPORT_ANNOTATION_POLISH_FAILED)
    return blocks


def _apply_one(annotation: dict, marks: list, blocks: list, root) -> None:
    """按写入模式把优化结果写进文档树。"""
    mode = _effective_annotation_mode(annotation, marks)
    if mode == ANNOTATION_MODE_APPEND:
        block = _containing_block(marks[-1], root)
        if block is not None:
            _insert_blocks_after(block, blocks)
        return

    first_mark = marks[0]
    block = _containing_block(first_mark, root)
    head = blocks[0]
    if head.tag == "p":
        _move_inline_into(first_mark, head)
        rest = blocks[1:]
    else:
        _clear_element_content(first_mark)
        rest = blocks
    if rest and block is not None:
        _insert_blocks_after(block, rest)
    for extra in marks[1:]:
        _clear_element_content(extra)


async def apply_annotations_to_html(
    html: str,
    annotations: list,
    *,
    workspace_id: str,
    session_id: str,
    tenant_id: str,
    timeout: float = ANNOTATION_POLISH_TIMEOUT,
) -> str:
    """按批注并发调用 LLM 优化内容，结果按追加写/覆盖写写回 HTML。

    无待处理批注时原样返回。锚点缺失、重叠批注、任一条优化失败均硬失败。
    """
    active = active_annotations(annotations)
    if not active:
        return html

    root = _parse_document(html)
    grouped = collect_annotation_marks(root)

    missing = [
        str(item.get("id") or "")
        for item in active
        if not grouped.get(str(item.get("id") or ""))
    ]
    if missing:
        logger.error("报告批注锚点缺失 annotation_ids=%s", sorted(missing))
        raise CustomError(ErrorCode.REPORT_ANNOTATION_ANCHOR_MISSING)

    llm = await resolve_llm(tenant_id)

    results = await asyncio.gather(
        *(
            _polish_one(
                item,
                grouped[str(item.get("id"))],
                root,
                workspace_id=workspace_id,
                session_id=session_id,
                llm=llm,
                timeout=timeout,
            )
            for item in active
        ),
        return_exceptions=True,
    )

    for item, outcome in zip(active, results):
        if isinstance(outcome, CustomError):
            raise outcome
        if isinstance(outcome, BaseException):
            logger.error(
                "批注优化失败 annotation_id=%s error=%s", item.get("id"), outcome
            )
            raise CustomError(ErrorCode.REPORT_ANNOTATION_POLISH_FAILED)

    for item, blocks in zip(active, results):
        _apply_one(item, grouped[str(item.get("id"))], blocks, root)

    return _serialize_children(root)


def strip_annotation_marks(html: str) -> str:
    """剥掉文本和图片的批注痕迹，保留被批注的正文与图片。"""
    root = _parse_document(html)
    for element in list(root.iter("mark")):
        if not _annotation_ids_of(element):
            continue
        element.drop_tag()
    for image in root.iter("img"):
        image.attrib.pop("data-annotation-id", None)
        image.attrib.pop("data-annotation-ids", None)
    return _serialize_children(root)


async def render_report_docx(
    *,
    content_html: str,
    fetch_config: dict,
    annotations: list,
    tenant_id: str,
    operator_id,
    workspace_id: str,
    session_id: str,
    base_docx_path: Path | None = None,
) -> bytes:
    """报告渲染流水线：取数 → 批注优化 → 剥批注痕迹 → HTML→docx。

    取数严格先于批注优化，使 AI 能依据本期真实数据完善内容。
    任一环节失败即抛 CustomError，不产出部分报告。
    """
    html = await resolve_placeholders_in_html(
        content_html or "", fetch_config or {}, tenant_id, operator_id
    )
    html = await apply_annotations_to_html(
        html,
        annotations or [],
        workspace_id=workspace_id,
        session_id=session_id,
        tenant_id=tenant_id,
    )
    html = strip_annotation_marks(html)
    base_document = None
    if base_docx_path is not None:
        try:
            if base_docx_path.stat().st_size > docx_converter.MAX_DOCX_BYTES:
                raise docx_converter.DocxConversionError("invalid DOCX size")
            base_document = base_docx_path.read_bytes()
            docx_converter.validate_docx_bytes(base_document)
        except (OSError, docx_converter.DocxConversionError) as exc:
            logger.warning(
                "原始 DOCX 外壳不可用，降级为从零生成 path=%s error=%s",
                base_docx_path,
                type(exc).__name__,
            )
            base_document = None
    return docx_converter.html_to_docx(html, base_document=base_document)
