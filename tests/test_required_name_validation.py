# coding=utf-8
import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.utils import normalize_required_text
from models.timed import Timed
from services.asset_management import manager
from services.timed import views as timed_views


ROOT = Path(__file__).resolve().parents[1]


def _function(path: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _calls(function: ast.AsyncFunctionDef, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        for node in ast.walk(function)
    )


def test_normalize_required_text_trims_edges_and_preserves_inner_spaces():
    assert normalize_required_text("  安全 运维  ") == "安全 运维"


@pytest.mark.parametrize("value", [None, 1, "", " \t\n", "\u200b", "名称\u2060"])
def test_normalize_required_text_rejects_missing_or_whitespace_only(value):
    with pytest.raises(CustomError) as exc_info:
        normalize_required_text(value)

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_model_write_boundary_rejects_noncanonical_or_invisible_names():
    valid = Timed(name="安全 运维", type=1, status=0, tactics="now", end_time="")
    valid._validate_persisted_name()

    for name in ("  安全运维  ", "\u200b"):
        timed = Timed(name=name, type=1, status=0, tactics="now", end_time="")
        with pytest.raises(CustomError) as exc_info:
            timed._validate_persisted_name()
        assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_delete_legacy_whitespace_timed_updates_only_soft_delete_fields(monkeypatch):
    class LegacyTimedRecord:
        _meta = Timed._meta
        _saved_in_db = True
        id = "timed-1"
        name = "   "
        type = timed_views.RUN_TYPE_ACTION
        action_input = {"action": "health_check", "action_input": {}}
        playbook_input = {}
        internal = False
        deleted = False
        updator = None
        saved_fields = None

        async def save(self, update_fields=None):
            Timed._validate_persisted_name(self, update_fields)
            self.saved_fields = update_fields

    record = LegacyTimedRecord()
    produced = []
    account = SimpleNamespace(id="account-1")

    class TimedQuery:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs == {
                "id": "timed-1",
                "deleted": False,
                "internal": False,
                "tenant_id": "tenant-1",
            }
            return record

    async def fake_scope(request):
        return False

    async def fake_aproduce(topic, message):
        produced.append((topic, message))

    monkeypatch.setattr(timed_views, "Timed", TimedQuery)
    monkeypatch.setattr(timed_views, "get_current_timed_internal_scope", fake_scope)
    monkeypatch.setattr(timed_views, "aproduce", fake_aproduce)

    request = SimpleNamespace(
        json={"id": "timed-1"},
        ctx=SimpleNamespace(
            session={"tenant_id": "tenant-1", "account_info": account}
        ),
    )
    response = asyncio.run(inspect.unwrap(timed_views.delete_timed)(request))

    assert response.status == 200
    assert record.deleted is True
    assert record.updator is account
    assert record.saved_fields == ["deleted", "updator_id", "utime"]
    assert produced == [
        (
            timed_views.TASK_TOPIC,
            {
                "run_type": "action",
                "run_args": record.action_input,
                "timer": "remove",
                "timer_id": "timed-1",
                "timer_rule": {},
                "tenant_id": "tenant-1",
            },
        )
    ]


def test_required_dynamic_text_field_rejects_whitespace_only():
    field = SimpleNamespace(
        data_type="text",
        required=True,
        options={},
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.validate_field_value("tenant-a", field, "   "))

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.parametrize(
    ("path", "function_name"),
    [
        ("services/event/views.py", "artificial_create_event"),
        ("services/event/views.py", "artificial_create_event_wuzhi"),
        ("services/event/views.py", "create_event"),
        ("services/event/views.py", "update_event"),
        ("services/workflow/views.py", "create_workflow"),
        ("services/workflow/views.py", "edit_workflow"),
        ("services/timed/views.py", "add_timed"),
    ],
)
def test_name_write_paths_use_required_trimmed_text(path, function_name):
    assert _calls(_function(path, function_name), "normalize_required_text")
