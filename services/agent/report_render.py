# coding=utf-8
"""报告渲染流水线：HTML 层取数 → 批注优化 → 剥批注痕迹 → HTML→docx。

依赖方向：report_render → report_fetch / docx_converter。
本模块不 import services.agent.views，避免循环导入。
"""

from lxml import etree, html as lxml_html
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.agent import report_fetch


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
    return "".join(value for element, attr in _iter_text_nodes(root)
                   for value in [getattr(element, attr) or ""])


_EMPTY_MERGEABLE_TAGS = frozenset({"strong", "em", "b", "i", "u", "s", "span", "mark", "code", "sub", "sup"})


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
    all_text = _collect_text(root)
    results = await report_fetch.resolve_fetch_values(
        all_text, fetch_config, tenant_id, operator_id
    )
    if not results:
        return html

    replacer = report_fetch.build_placeholder_replacer(results)

    def _substitute(value: str) -> str:
        return report_fetch.FETCH_PLACEHOLDER_RE.sub(
            lambda match: replacer(match) if replacer(match) is not None else match.group(0),
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
    """按文档顺序收集每个批注 id 对应的 mark 元素。

    携带 2+ 批注 id 的 mark 视为重叠批注，抛 REPORT_ANNOTATION_OVERLAP。
    """
    grouped: dict[str, list] = {}
    for element in root.iter("mark"):
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


def annotation_region_text(marks: list) -> str:
    """拼接批注区域内的全部文字。"""
    return "".join("".join(mark.itertext()) for mark in marks)
