# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.asset_management import views


class _AssetRecord(SimpleNamespace):
    @property
    def json(self):
        return {"id": self.id, "attributes": self.attributes}


class _CiQuery:
    def __init__(self, rows, operations):
        self.rows = rows
        self.operations = operations
        self.start = 0
        self.size = None

    def order_by(self, *fields):
        self.operations.append(("order_by", fields))
        return self

    async def count(self):
        self.operations.append("count")
        return len(self.rows)

    def offset(self, value):
        self.operations.append(("offset", value))
        self.start = value
        return self

    def limit(self, value):
        self.operations.append(("limit", value))
        self.size = value
        return self

    async def all(self):
        self.operations.append("all")
        end = None if self.size is None else self.start + self.size
        return self.rows[self.start : end]


def _field(data_type, *, field_key="field", enum_options=None):
    return SimpleNamespace(
        field_key=field_key,
        data_type=data_type,
        enum_options=enum_options or [],
    )


def _filter(data_type, payload, **field_kwargs):
    return views._normalize_asset_field_filter(
        _field(data_type, **field_kwargs),
        {"field_key": field_kwargs.get("field_key", "field"), **payload},
    )


def _assert_param_error(callback):
    with pytest.raises(CustomError) as error:
        callback()
    assert error.value.enum == ErrorCode.PARAM_ERROR


def _run_asset_list(monkeypatch, data, records, fields, reference_labels=None):
    operations = []
    query = _CiQuery(records, operations)
    asset_model = SimpleNamespace(id="type-1")

    async def get_asset_model(tenant, model_ref):
        assert tenant == "tenant-a"
        assert model_ref == "type-1"
        return asset_model

    async def get_fields(tenant, model_ref):
        assert (tenant, model_ref) == ("tenant-a", "type-1")
        return fields

    async def get_reference_labels(tenant, items, field_defs):
        assert tenant == "tenant-a"
        assert all(item in records for item in items)
        assert field_defs == fields
        return reference_labels or {}

    monkeypatch.setattr(views, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "fields_for_model", get_fields)
    monkeypatch.setattr(views, "reference_labels_for_assets", get_reference_labels)
    monkeypatch.setattr(
        views.AssetInstance,
        "filter",
        staticmethod(lambda *_args, **_kwargs: query),
    )
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json=data,
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.asset_list)(request))
    return payload, operations


def test_text_filters_are_case_insensitive_and_type_strict():
    contains = _filter("text", {"operator": "contains", "value": "DB-01"})
    exact = _filter("text", {"operator": "eq", "value": "DB-01"})

    assert views._matches_asset_field_filter("primary-db-01-node", contains)
    assert views._matches_asset_field_filter("db-01", exact)
    assert not views._matches_asset_field_filter("db-010", exact)
    assert not views._matches_asset_field_filter(101, contains)


def test_number_filter_supports_zero_inclusive_and_open_bounds():
    closed = _filter(
        "number",
        {"operator": "between", "lower": 0, "upper": 10},
    )
    lower_only = _filter("number", {"operator": "between", "lower": 10})
    upper_only = _filter("number", {"operator": "between", "upper": 0})

    assert views._matches_asset_field_filter(0, closed)
    assert views._matches_asset_field_filter(10, closed)
    assert not views._matches_asset_field_filter(-1, closed)
    assert not views._matches_asset_field_filter(11, closed)
    assert not views._matches_asset_field_filter(False, closed)
    assert views._matches_asset_field_filter(11, lower_only)
    assert views._matches_asset_field_filter(-1, upper_only)


def test_date_and_datetime_filters_are_closed_ranges_and_ignore_dirty_values():
    date_filter = _filter(
        "date",
        {
            "operator": "between",
            "lower": "2026-07-01",
            "upper": "2026-07-20",
        },
    )
    datetime_filter = _filter(
        "datetime",
        {
            "operator": "between",
            "lower": "2026-07-20 09:00:00",
            "upper": "2026-07-20 18:00:00",
        },
    )

    assert views._matches_asset_field_filter("2026-07-01", date_filter)
    assert views._matches_asset_field_filter("2026-07-20", date_filter)
    assert not views._matches_asset_field_filter("2026/07/10", date_filter)
    assert views._matches_asset_field_filter("2026-07-20 09:00:00", datetime_filter)
    assert views._matches_asset_field_filter("2026-07-20 18:00:00", datetime_filter)
    assert not views._matches_asset_field_filter("invalid", datetime_filter)


def test_enum_bool_and_ref_filters_use_exact_typed_values():
    enum_filter = _filter(
        "enum",
        {"operator": "eq", "value": "prod"},
        enum_options=[{"label": "Production", "value": "prod"}, "test"],
    )
    bool_filter = _filter("bool", {"operator": "eq", "value": False})
    ref_filter = _filter("ref", {"operator": "eq", "value": "asset-1"})

    assert views._matches_asset_field_filter("prod", enum_filter)
    assert not views._matches_asset_field_filter("Production", enum_filter)
    assert views._matches_asset_field_filter(False, bool_filter)
    assert not views._matches_asset_field_filter(0, bool_filter)
    assert not views._matches_asset_field_filter("false", bool_filter)
    assert views._matches_asset_field_filter("asset-1", ref_filter)
    assert not views._matches_asset_field_filter("ASSET-1", ref_filter)


@pytest.mark.parametrize(
    ("data_type", "payload", "field_kwargs"),
    [
        ("text", {"operator": "contains", "value": ""}, {}),
        ("text", {"operator": "between", "lower": "a"}, {}),
        ("number", {"operator": "between"}, {}),
        ("number", {"operator": "between", "lower": False}, {}),
        ("number", {"operator": "between", "lower": float("nan")}, {}),
        ("number", {"operator": "between", "lower": 2, "upper": 1}, {}),
        ("date", {"operator": "between", "lower": "2026/07/01"}, {}),
        (
            "date",
            {"operator": "between", "lower": "2026-07-02", "upper": "2026-07-01"},
            {},
        ),
        ("datetime", {"operator": "between", "lower": "2026-07-01"}, {}),
        ("enum", {"operator": "eq", "value": "missing"}, {"enum_options": ["known"]}),
        ("bool", {"operator": "eq", "value": "false"}, {}),
        ("ref", {"operator": "contains", "value": "asset"}, {}),
        ("attachment", {"operator": "eq", "value": "file-1"}, {}),
        ("unknown", {"operator": "eq", "value": "value"}, {}),
    ],
)
def test_invalid_type_specific_filters_are_rejected(data_type, payload, field_kwargs):
    _assert_param_error(lambda: _filter(data_type, payload, **field_kwargs))


@pytest.mark.parametrize(
    "data",
    [
        {"field_filter": None},
        {"field_filter": {}},
        {
            "field_filter": {
                "field_key": "field",
                "operator": "eq",
                "value": "x",
                "extra": True,
            }
        },
        {"field_key": "field"},
        {"field_keyword": "value"},
        {"field_key": "field", "field_keyword": "value", "match_mode": "invalid"},
        {
            "field_filter": {"field_key": "field", "operator": "eq", "value": "x"},
            "field_key": "field",
            "field_keyword": "x",
        },
    ],
)
def test_invalid_filter_request_shapes_are_rejected(data):
    def normalize_request():
        request = views._asset_field_filter_request(data)
        if request is not None:
            views._normalize_asset_field_filter(_field("text"), request)

    _assert_param_error(normalize_request)


def test_retired_field_filter_contract_is_rejected():
    for payload in (
        {"field_key": "field", "field_keyword": "DB-01"},
        {"field_key": "owner", "field_keyword": "asset-1", "match_mode": "exact"},
        {"match_mode": "fuzzy"},
    ):
        _assert_param_error(
            lambda payload=payload: views._asset_field_filter_request(payload)
        )


def test_asset_list_filters_before_paginating_and_preserves_filtered_total(monkeypatch):
    records = [
        _AssetRecord(id="zero", attributes={"port": 0}),
        _AssetRecord(id="five", attributes={"port": 5}),
        _AssetRecord(id="ten", attributes={"port": 10}),
        _AssetRecord(id="eleven", attributes={"port": 11}),
        _AssetRecord(id="dirty", attributes={"port": "5"}),
        _AssetRecord(id="boolean", attributes={"port": False}),
    ]

    payload, operations = _run_asset_list(
        monkeypatch,
        {
            "asset_model_id": "type-1",
            "field_filter": {
                "field_key": "port",
                "operator": "between",
                "lower": 0,
                "upper": 10,
            },
            "page": 2,
            "size": 2,
        },
        records,
        [_field("number", field_key="port")],
    )

    assert payload["total"] == 3
    assert [item["id"] for item in payload["items"]] == ["ten"]
    assert operations == [("order_by", ("-utime",)), "all"]


def test_asset_list_filters_reference_by_target_name_and_multiple_values(monkeypatch):
    source_records = [
        _AssetRecord(id="source-1", attributes={"owners": ["target-1", "target-2"]}),
        _AssetRecord(id="source-2", attributes={"owners": ["target-3"]}),
    ]
    target_records = [
        _AssetRecord(id="target-1", name="办公ip地址段", attributes={}),
        _AssetRecord(id="target-2", name="核心交换机", attributes={}),
        _AssetRecord(id="target-3", name="数据库", attributes={}),
    ]
    source_query = _CiQuery(source_records, [])
    target_query = _CiQuery(target_records, [])
    asset_model = SimpleNamespace(id="source-type")
    target_model = SimpleNamespace(id="target-type")

    async def get_asset_model(tenant, model_ref):
        assert tenant == "tenant-a"
        return asset_model if model_ref == "source-type" else target_model

    async def get_reference_labels(_tenant, items, _field_defs):
        return {item.id: {} for item in items}

    def asset_filter(*args, **kwargs):
        if kwargs.get("asset_model_id") == "target-type":
            return target_query
        return source_query

    ref_field = _field("ref", field_key="owners")
    ref_field.options = {"multiple": True}
    ref_field.referenced_asset_model_id = "target-type"

    async def get_fields_with_reference(tenant, model_ref):
        assert (tenant, model_ref) == ("tenant-a", "source-type")
        return [ref_field]

    monkeypatch.setattr(views, "get_asset_model_or_404", get_asset_model)
    monkeypatch.setattr(views, "fields_for_model", get_fields_with_reference)
    monkeypatch.setattr(views, "reference_labels_for_assets", get_reference_labels)
    monkeypatch.setattr(views.AssetInstance, "filter", staticmethod(asset_filter))
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json={
            "asset_model_id": "source-type",
            "field_filter": {
                "field_key": "owners",
                "operator": "eq",
                "value": "办公ip地址段",
            },
            "page": 1,
            "size": 20,
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    payload = asyncio.run(inspect.unwrap(views.asset_list)(request))

    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == ["source-1"]


def test_asset_list_rejects_retired_text_filter(monkeypatch):
    records = [
        _AssetRecord(id="match", attributes={"hostname": "Primary-DB"}),
        _AssetRecord(id="miss", attributes={"hostname": "web-01"}),
    ]

    _assert_param_error(
        lambda: _run_asset_list(
            monkeypatch,
            {
                "asset_model_id": "type-1",
                "field_key": "hostname",
                "field_keyword": "db",
                "match_mode": "fuzzy",
            },
            records,
            [_field("text", field_key="hostname")],
        )
    )


def test_asset_list_rejects_unknown_filter_field(monkeypatch):
    def run():
        _run_asset_list(
            monkeypatch,
            {
                "asset_model_id": "type-1",
                "field_filter": {
                    "field_key": "missing",
                    "operator": "eq",
                    "value": "value",
                },
            },
            [],
            [],
        )

    _assert_param_error(run)


def test_asset_list_rejects_filter_without_model(monkeypatch):
    monkeypatch.setattr(
        views.AssetInstance,
        "filter",
        staticmethod(lambda *_args, **_kwargs: _CiQuery([], [])),
    )
    request = SimpleNamespace(
        json={
            "field_filter": {
                "field_key": "hostname",
                "operator": "eq",
                "value": "db-01",
            }
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    _assert_param_error(lambda: asyncio.run(inspect.unwrap(views.asset_list)(request)))


def test_asset_list_without_field_filter_uses_database_pagination(monkeypatch):
    records = [_AssetRecord(id=str(index), attributes={}) for index in range(1, 6)]

    payload, operations = _run_asset_list(
        monkeypatch,
        {"asset_model_id": "type-1", "page": 2, "size": 2},
        records,
        [],
    )

    assert payload["total"] == 5
    assert [item["id"] for item in payload["items"]] == ["3", "4"]
    assert operations == [
        "count",
        ("order_by", ("-utime",)),
        ("offset", 2),
        ("limit", 2),
        "all",
    ]


def test_asset_list_includes_batch_resolved_reference_labels(monkeypatch):
    records = [
        _AssetRecord(id="project-1", attributes={"manager": "employee-1"}),
    ]

    payload, _operations = _run_asset_list(
        monkeypatch,
        {"asset_model_id": "type-1", "page": 1, "size": 15},
        records,
        [_field("ref", field_key="manager")],
        {"project-1": {"manager": "张三"}},
    )

    assert payload["items"] == [
        {
            "id": "project-1",
            "attributes": {"manager": "employee-1"},
            "reference_labels": {"manager": "张三"},
        }
    ]
