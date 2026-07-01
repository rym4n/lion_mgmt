# coding=utf-8
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.tools.views import (
    _action_matches_search_key,
    _as_bool,
    _filter_latest_tool_pairs_by_search_key,
    _tool_matches_search_key,
)


def test_tool_search_matches_name_and_description_case_insensitively():
    tool = {"name": "AIOE_docx", "description": "AIOE Lion管理消息与用户操作"}

    assert _tool_matches_search_key(tool, "aioe")
    assert _tool_matches_search_key(tool, "AIOE")
    assert _tool_matches_search_key(tool, "lion")


def test_tool_search_can_exclude_action_only_matches():
    pairs = [
        {"name": "aioe-docx", "latest_ctime": "2026-07-01T00:00:00"},
        {"name": "unrelated-app", "latest_ctime": "2026-07-01T00:00:01"},
    ]
    latest_tools = [
        {"name": "aioe-docx", "description": "生成 AIOE docx 文件"},
        {"name": "unrelated-app", "description": "通用文件处理"},
    ]

    filtered = _filter_latest_tool_pairs_by_search_key(pairs, latest_tools, set(), "aioe")

    assert filtered == [pairs[0]]


def test_tool_search_preserves_action_matches_when_enabled():
    pairs = [
        {"name": "unrelated-app", "latest_ctime": "2026-07-01T00:00:01"},
    ]
    latest_tools = [
        {"name": "unrelated-app", "description": "通用文件处理"},
    ]

    filtered = _filter_latest_tool_pairs_by_search_key(pairs, latest_tools, {"unrelated-app"}, "aioe")

    assert filtered == pairs
    assert _action_matches_search_key({"name": "查询 AIOE 告警", "func": "query_alert"}, "aioe")


def test_as_bool_accepts_frontend_false_flag():
    assert _as_bool(False, True) is False
    assert _as_bool("false", True) is False
    assert _as_bool(None, True) is True
