"""Builtin archive import keeps explicit execution budgets through runtime config."""
import inspect
import io
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from libs.error_code.exception import CustomError
from services.system_config import views
from services.inspection.logic import _build_playbook_config


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 300, 6600, 14400, 259200, 299, 259201, True, "14400", 3.5])
async def test_import_archive_validates_and_preserves_flow_timeout(monkeypatch, timeout):
    item = {"version": "builtin-v2", "name": "Collection", "flow_json": {"nodes": []}}
    if timeout is not None:
        item["flow_timeout"] = timeout
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("workflow.json", orjson.dumps([item]))
    creator = SimpleNamespace(username="bootstrap")
    request = SimpleNamespace(files={"file": SimpleNamespace(body=buffer.getvalue())},
                              form={"maintenance_mode": "true"},
                              ctx=SimpleNamespace(session={"tenant_id": "default", "account_info": creator}))
    @asynccontextmanager
    async def transaction():
        yield None
    monkeypatch.setattr(views, "in_transaction", transaction)
    monkeypatch.setattr(views, "get_superadmin_role", AsyncMock(return_value=object()))
    playbook = SimpleNamespace(id="pb1", name="Collection")
    monkeypatch.setattr(views.Playbook, "get_or_none", AsyncMock(return_value=playbook))
    monkeypatch.setattr(views.PlaybookVersion, "filter", lambda **kw: SimpleNamespace(exists=AsyncMock(return_value=False)))
    captured = []
    async def create(**values):
        row = SimpleNamespace(id="pv1", **values)
        captured.append(row)
        return row
    monkeypatch.setattr(views.PlaybookVersion, "create", create)
    monkeypatch.setattr(views, "statistics_data", AsyncMock(return_value=[]))
    monkeypatch.setattr(views, "tool_action_update", AsyncMock())
    valid = timeout is None or type(timeout) is int and 300 <= timeout <= 259200
    if not valid:
        with pytest.raises(CustomError):
            await inspect.unwrap(views.init_data)(request)
        assert not captured
        return
    response = await inspect.unwrap(views.init_data)(request)
    assert response.status == 200
    version = captured[0]
    expected = 300 if timeout is None else timeout
    assert version.flow_timeout == expected
    # The same persisted version is the execution contract source, and the
    # built-in importer may no longer silently truncate its budget to 300s.
    config = _build_playbook_config(SimpleNamespace(schedule_id="s1", app_name="builtin"),
        SimpleNamespace(id="target1", action_input={}, action_name="Collection"),
        playbook, version, "default")
    assert config["timeout"] == str(expected)
