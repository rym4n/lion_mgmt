# coding=utf-8
"""报告渲染流水线：HTML 层取数 → 批注优化 → 剥批注痕迹 → HTML→docx。

依赖方向：report_render → report_fetch / docx_converter。
本模块不 import services.agent.views，避免循环导入。
"""

from lxml import etree, html as lxml_html
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
