# coding=utf-8
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from services.log_view import views as log_views


ROOT = Path(__file__).resolve().parents[1]


def _function_node(path: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def test_event_update_treats_an_explicit_empty_summary_as_an_update():
    function = _function_node("services/event/views.py", "update_event")

    summary_guard = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and node.left.value == "event_summary"
        and any(isinstance(operator, ast.In) for operator in node.ops)
    )

    assert isinstance(summary_guard.comparators[0], ast.Name)
    assert summary_guard.comparators[0].id == "params"


def test_log_index_list_keeps_soft_deleted_sources_available_for_cleanup(monkeypatch):
    filters = {}

    class _ProjectsQuery:
        async def values(self, *fields):
            assert fields == ("project_id", "event_ingestion_name", "deleted")
            return [
                {
                    "project_id": 7,
                    "event_ingestion_name": "retained source",
                    "deleted": True,
                }
            ]

    def fake_filter(**kwargs):
        filters.update(kwargs)
        return _ProjectsQuery()

    monkeypatch.setattr(log_views.EventsIngestion, "filter", fake_filter)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})
    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}))
    handler = log_views.get_index_list
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__

    result = asyncio.run(handler(request))

    assert filters == {"project_id__isnull": False, "tenant_id": "tenant-a"}
    assert result == {
        "code": 0,
        "data": [{"id": 7, "name": "retained source", "deleted": True}],
    }
