# coding=utf-8
import pytest

from services.knowledge.object_store import raw_key, text_key


def test_key_format_matches_lion_agent():
    """键格式与 lion_agent/knowledge/object_store.py 必须逐字一致。"""
    assert raw_key("doc-1") == "knowledge/doc-1/raw"
    assert text_key("doc-1") == "knowledge/doc-1/text"


def test_keys_reject_traversal():
    for bad in ("../etc/passwd", "a/../b", "..", "a/b"):
        with pytest.raises(ValueError, match="invalid document_id"):
            raw_key(bad)
