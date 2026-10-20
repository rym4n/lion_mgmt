"""Builtin archive import keeps explicit execution budgets through runtime config."""
import inspect
import io
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orjson
import pytest
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.system_config import views
from services.tools import views as tool_views
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


@pytest.mark.asyncio
@pytest.mark.parametrize("upload_msg", ["success", "工具已存在"])
async def test_init_data_binds_missing_action_versions_to_imported_tool(monkeypatch, upload_msg):
    flow = {
        "nodes": [
            {
                "type": "action-node",
                "properties": {"app_name": "lion_mgmt", "action": "collect"},
            },
            {
                "type": "action-node",
                "properties": {
                    "app_name": "lion_mgmt",
                    "version": "pinned-old-version",
                    "action": "explicit",
                },
            },
            {"type": "end-node", "properties": {"label": "unchanged"}},
        ]
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lion_mgmt.zip", b"tool package")
        archive.writestr(
            "workflow.json",
            orjson.dumps(
                [
                    {
                        "version": "builtin-v3",
                        "name": "Collection",
                        "flow_json": flow,
                    }
                ]
            ),
        )
    creator = SimpleNamespace(username="bootstrap")
    request = SimpleNamespace(
        files={"file": SimpleNamespace(body=buffer.getvalue())},
        form={"maintenance_mode": "true"},
        ctx=SimpleNamespace(
            session={"tenant_id": "tenant-a", "account_info": creator}
        ),
    )

    @asynccontextmanager
    async def transaction():
        yield None

    monkeypatch.setattr(views, "in_transaction", transaction)
    monkeypatch.setattr(views, "get_superadmin_role", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        views,
        "upload_tool",
        AsyncMock(
            return_value={
                "msg": upload_msg,
                "name": "lion_mgmt",
                "version": "imported-version-a",
            }
        ),
    )
    monkeypatch.setattr(views.PlaybookVersion, "filter", lambda **kw: SimpleNamespace(exists=AsyncMock(return_value=False)))
    playbook = SimpleNamespace(id="pb1", name="Collection")
    monkeypatch.setattr(views.Playbook, "get_or_none", AsyncMock(return_value=playbook))
    captured = []

    async def create(**values):
        row = SimpleNamespace(id="pv1", **values)
        captured.append(row)
        return row

    monkeypatch.setattr(views.PlaybookVersion, "create", create)
    monkeypatch.setattr(views, "statistics_data", AsyncMock(return_value=[]))
    monkeypatch.setattr(views, "tool_action_update", AsyncMock())

    if upload_msg == "工具已存在":
        with pytest.raises(CustomError) as exc_info:
            await inspect.unwrap(views.init_data)(request)
        assert exc_info.value.enum is ErrorCode.IMPORT_FLOW_ERROR
    else:
        response = await inspect.unwrap(views.init_data)(request)
        assert response.status == 200
    imported_flow = captured[0].flow_json
    assert imported_flow["nodes"][0]["properties"]["version"] == "imported-version-a"
    assert imported_flow["nodes"][1]["properties"]["version"] == "pinned-old-version"
    assert "version" not in imported_flow["nodes"][2]["properties"]


@pytest.mark.asyncio
async def test_init_data_rejects_unresolved_missing_action_version_before_playbook(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("workflow.json", orjson.dumps([{
            "version": "builtin-v3",
            "name": "Collection",
            "flow_json": {
                "nodes": [{
                    "type": "action-node",
                    "properties": {"app_name": "missing_app", "action": "collect"},
                }]
            },
        }]))
    creator = SimpleNamespace(username="bootstrap")
    request = SimpleNamespace(
        files={"file": SimpleNamespace(body=buffer.getvalue())},
        form={"maintenance_mode": "true"},
        ctx=SimpleNamespace(
            session={"tenant_id": "tenant-a", "account_info": creator}
        ),
    )
    monkeypatch.setattr(views, "get_superadmin_role", AsyncMock(return_value=object()))
    monkeypatch.setattr(views.PlaybookVersion, "filter", lambda **kw: SimpleNamespace(exists=AsyncMock(return_value=False)))
    monkeypatch.setattr(views.Playbook, "get_or_none", AsyncMock(side_effect=AssertionError("playbook must not be read")))
    monkeypatch.setattr(views.PlaybookVersion, "create", AsyncMock(side_effect=AssertionError("version must not be created")))

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(views.init_data)(request)

    assert exc_info.value.enum is ErrorCode.IMPORT_FLOW_ERROR


@pytest.mark.asyncio
async def test_upload_tool_preserves_identity_for_existing_version(monkeypatch):
    monkeypatch.setattr(
        tool_views,
        "unzip",
        AsyncMock(
            return_value=(
                {"name": "lion_mgmt"},
                "imported-version-a",
                "lion_mgmt",
                "Topology app",
                "工具已存在",
            )
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=object())),
        ctx=SimpleNamespace(session={"id": "session-a"}),
    )

    result = await tool_views.upload_tool(
        SimpleNamespace(name="lion_mgmt.zip", body=b"archive"),
        {"moduleList": [], "grouplist": {}},
        "bootstrap",
        "tenant-a",
        request,
        maintenance_mode=True,
    )

    assert result["msg"] == "工具已存在"
    assert result["name"] == "lion_mgmt"
    assert result["version"] == "imported-version-a"
