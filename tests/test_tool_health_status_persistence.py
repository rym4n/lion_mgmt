import datetime
import inspect
from types import SimpleNamespace

import pytest

from libs import utils
from libs.error_code.exception import CustomError
from services.tools import views as tool_views


def _request(payload=None, tenant_id="tenant-a"):
    return SimpleNamespace(
        json=payload,
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


@pytest.mark.asyncio
async def test_sync_health_status_updates_each_tenant_resource_and_ignores_unknown(monkeypatch):
    saved = []
    lookups = []

    class _Resource:
        def __init__(self, resource_id):
            self.id = resource_id
            self.status = None
            self.utime = None

        async def save(self, update_fields):
            saved.append((self.id, self.status, self.utime, update_fields))

    resources = {
        "resource-a": _Resource("resource-a"),
        "resource-b": _Resource("resource-b"),
    }

    class _ToolResources:
        @staticmethod
        async def get_or_none(**kwargs):
            lookups.append(kwargs)
            return resources.get(kwargs.get("id"))

    monkeypatch.setattr(tool_views, "ToolResources", _ToolResources)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)
    payload = {
        "results": {
            "available": [{"resource_id": "resource-a"}],
            "unavailable": [{"resource_id": "resource-b"}],
            "no_check_action": [{"resource_id": "missing"}],
        }
    }

    result = await inspect.unwrap(tool_views.sync_health_status)(_request(payload))

    assert result["updated_count"] == 2
    assert result["details"] == {"available": 1, "unavailable": 1, "unknown": 0}
    assert [(item[0], item[1]) for item in saved] == [
        ("resource-a", "available"),
        ("resource-b", "unavailable"),
    ]
    assert all(item[2] is not None and item[3] == ["status", "utime"] for item in saved)
    assert all(
        lookup["tenant_id"] == "tenant-a"
        and lookup["deleted"] is False
        and lookup["internal"] is False
        for lookup in lookups
    )


@pytest.mark.asyncio
async def test_sync_health_status_supports_legacy_resource_name_lookup(monkeypatch):
    resource = SimpleNamespace(status=None, utime=None)

    async def save(update_fields):
        resource.update_fields = update_fields

    resource.save = save

    class _ToolResources:
        @staticmethod
        async def get_or_none(**kwargs):
            assert kwargs == {
                "deleted": False,
                "tenant_id": "tenant-a",
                "internal": False,
                "tool_name": "demo",
                "name": "server-1",
            }
            return resource

    monkeypatch.setattr(tool_views, "ToolResources", _ToolResources)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.sync_health_status)(
        _request(
            {
                "results": {
                    "available": [],
                    "unavailable": [],
                    "no_check_action": [
                        {"tool_name": "demo", "resource": "server-1"}
                    ],
                }
            }
        )
    )

    assert result["details"]["unknown"] == 1
    assert resource.status == "unknown"
    assert resource.update_fields == ["status", "utime"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], {}, {"results": []}])
async def test_sync_health_status_rejects_malformed_top_level_payload(payload):
    with pytest.raises(CustomError):
        await inspect.unwrap(tool_views.sync_health_status)(_request(payload))


@pytest.mark.asyncio
async def test_health_check_result_is_rebuilt_from_resource_rows(monkeypatch):
    checked_at = datetime.datetime(2026, 7, 29, 3, 30, tzinfo=datetime.UTC)
    resources = [
        SimpleNamespace(id="a", tool_name="tool-a", name="server-a", status="available", utime=checked_at),
        SimpleNamespace(id="b", tool_name="tool-b", name="server-b", status="unhealthy", utime=checked_at),
        SimpleNamespace(id="c", tool_name="tool-c", name="server-c", status="unknown", utime=checked_at),
        SimpleNamespace(id="d", tool_name="tool-d", name="server-d", status=None, utime=checked_at),
    ]

    class _Query:
        async def all(self):
            return resources

    class _ToolResources:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "deleted": False,
                "internal": False,
                "tenant_id": "tenant-a",
            }
            return _Query()

    monkeypatch.setattr(tool_views, "ToolResources", _ToolResources)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.get_health_check_result)(_request({}))
    value = result["value"]

    assert [item["resource_id"] for item in value["available"]] == ["a"]
    assert value["unavailable"][0]["state"] == "unavailable"
    assert [item["resource_id"] for item in value["no_check_action"]] == ["c"]
    assert value["available"][0]["time"] == utils.strip_timezone_to_seconds(checked_at)
