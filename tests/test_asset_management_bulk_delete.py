import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.asset_management import manager, views


class _Query:
    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls

    def using_db(self, connection):
        self.calls.append(("using_db", connection))
        return self

    def order_by(self, *fields):
        self.calls.append(("order_by", fields))
        return self

    def select_for_update(self):
        self.calls.append("select_for_update")
        return self

    def exclude(self, **kwargs):
        self.calls.append(("exclude", kwargs))
        excluded_ids = set(kwargs.get("source_asset_id__in", []))
        self.rows = [
            row for row in self.rows if row.source_asset_id not in excluded_ids
        ]
        return self

    async def all(self):
        self.calls.append("all")
        return self.rows

    async def values_list(self, field, flat=False):
        self.calls.append(("values_list", field, flat))
        return [getattr(row, field) for row in self.rows]

    async def delete(self):
        self.calls.append("delete")


def _install_bulk_delete_fakes(
    monkeypatch, assets, incoming_refs=(), failing_asset_id=None
):
    calls = []
    transaction_events = []
    single_deletes = []
    connection = object()

    @asynccontextmanager
    async def transaction(tenant, supplied_connection=None):
        assert tenant == "tenant-a"
        assert supplied_connection is None
        transaction_events.append("enter")
        try:
            yield connection
        except Exception as exc:
            transaction_events.append(("rollback", type(exc)))
            raise
        else:
            transaction_events.append("commit")

    async def delete_one(tenant, asset_id, *, connection=None):
        single_deletes.append((tenant, asset_id, connection))
        if asset_id == failing_asset_id:
            raise CustomError(ErrorCode.ASSET_INSTANCE_HAS_REFERENCES)
        return {"id": asset_id}

    monkeypatch.setattr(manager, "structure_write_transaction", transaction)
    monkeypatch.setattr(
        manager.AssetInstance,
        "filter",
        staticmethod(lambda **_kwargs: _Query(assets, calls)),
    )

    def reference_filter(**kwargs):
        if "target_asset_id__in" in kwargs:
            return _Query(list(incoming_refs), calls)
        return _Query([], calls)

    monkeypatch.setattr(
        manager.AssetReference,
        "filter",
        staticmethod(reference_filter),
    )
    monkeypatch.setattr(manager, "delete_asset_instance", delete_one)
    return calls, transaction_events, single_deletes, connection


def test_bulk_delete_uses_one_tenant_scoped_transaction_for_one_model(monkeypatch):
    calls, transaction_events, single_deletes, connection = _install_bulk_delete_fakes(
        monkeypatch,
        [
            SimpleNamespace(id="asset-1", name="Asset 1", asset_model_id="model-1"),
            SimpleNamespace(id="asset-2", name="Asset 2", asset_model_id="model-1"),
        ],
    )

    result = asyncio.run(
        manager.delete_asset_instances("tenant-a", ["asset-1", "asset-2"])
    )

    assert result == {
        "deleted_ids": ["asset-1", "asset-2"],
        "blocked": [],
    }
    assert single_deletes == [
        ("tenant-a", "asset-1", connection),
        ("tenant-a", "asset-2", connection),
    ]
    assert transaction_events == ["enter", "commit"]
    assert ("exclude", {"source_asset_id__in": ["asset-1", "asset-2"]}) in calls
    assert calls.count("delete") == 1


def test_bulk_delete_returns_blocked_assets_without_deleting_anything(monkeypatch):
    calls, transaction_events, single_deletes, _ = _install_bulk_delete_fakes(
        monkeypatch,
        [
            SimpleNamespace(id="asset-1", name="Asset 1", asset_model_id="model-1"),
            SimpleNamespace(id="asset-2", name="Asset 2", asset_model_id="model-1"),
        ],
        incoming_refs=[
            SimpleNamespace(target_asset_id="asset-2", source_asset_id="outside-asset")
        ],
    )

    result = asyncio.run(
        manager.delete_asset_instances("tenant-a", ["asset-1", "asset-2"])
    )

    assert result == {
        "deleted_ids": [],
        "blocked": [{"id": "asset-2", "name": "Asset 2"}],
    }
    assert single_deletes == []
    assert "delete" not in calls
    assert transaction_events == ["enter", "commit"]


def test_bulk_delete_allows_references_between_selected_assets(monkeypatch):
    _, transaction_events, single_deletes, connection = _install_bulk_delete_fakes(
        monkeypatch,
        [
            SimpleNamespace(id="asset-1", name="Asset 1", asset_model_id="model-1"),
            SimpleNamespace(id="asset-2", name="Asset 2", asset_model_id="model-1"),
        ],
        incoming_refs=[
            SimpleNamespace(target_asset_id="asset-2", source_asset_id="asset-1")
        ],
    )

    result = asyncio.run(
        manager.delete_asset_instances("tenant-a", ["asset-1", "asset-2"])
    )

    assert result == {"deleted_ids": ["asset-1", "asset-2"], "blocked": []}
    assert single_deletes == [
        ("tenant-a", "asset-1", connection),
        ("tenant-a", "asset-2", connection),
    ]
    assert transaction_events == ["enter", "commit"]


def test_bulk_delete_rolls_back_when_a_delete_fails_after_prior_items(monkeypatch):
    _, transaction_events, single_deletes, connection = _install_bulk_delete_fakes(
        monkeypatch,
        [
            SimpleNamespace(id="asset-1", name="Asset 1", asset_model_id="model-1"),
            SimpleNamespace(id="asset-2", name="Asset 2", asset_model_id="model-1"),
        ],
        failing_asset_id="asset-2",
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_asset_instances("tenant-a", ["asset-1", "asset-2"]))

    assert exc_info.value.enum is ErrorCode.ASSET_INSTANCE_HAS_REFERENCES
    assert single_deletes == [
        ("tenant-a", "asset-1", connection),
        ("tenant-a", "asset-2", connection),
    ]
    assert transaction_events == ["enter", ("rollback", CustomError)]


def test_bulk_delete_rejects_assets_from_multiple_models_before_writing(monkeypatch):
    calls, transaction_events, single_deletes, _ = _install_bulk_delete_fakes(
        monkeypatch,
        [
            SimpleNamespace(id="asset-1", name="Asset 1", asset_model_id="model-1"),
            SimpleNamespace(id="asset-2", name="Asset 2", asset_model_id="model-2"),
        ],
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_asset_instances("tenant-a", ["asset-1", "asset-2"]))

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert single_deletes == []
    assert "delete" not in calls
    assert transaction_events == ["enter", ("rollback", CustomError)]


@pytest.mark.parametrize(
    "asset_ids",
    [[], ["asset-1", "asset-1"], [" "], ["asset-1"] * 101],
)
def test_bulk_delete_rejects_empty_duplicate_blank_or_oversized_ids(
    monkeypatch, asset_ids
):
    calls, transaction_events, single_deletes, _ = _install_bulk_delete_fakes(
        monkeypatch, []
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.delete_asset_instances("tenant-a", asset_ids))

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR
    assert single_deletes == []
    assert calls == []
    assert transaction_events == []


def test_bulk_delete_endpoint_uses_session_tenant_and_ids(monkeypatch):
    calls = []

    async def delete_many(tenant, asset_ids):
        calls.append((tenant, asset_ids))
        return {"deleted_ids": asset_ids, "blocked": []}

    monkeypatch.setattr(views, "delete_asset_instances", delete_many)
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={"ids": ["asset-1", "asset-2"]},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    result = asyncio.run(inspect.unwrap(views.asset_delete_batch)(request))

    assert calls == [("tenant-a", ["asset-1", "asset-2"])]
    assert result == {"deleted_ids": ["asset-1", "asset-2"], "blocked": []}
