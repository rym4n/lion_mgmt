# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.supply_chain_review import views


class _CiRecord(SimpleNamespace):
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
    return views._normalize_ci_field_filter(
        _field(data_type, **field_kwargs),
        {"field_key": field_kwargs.get("field_key", "field"), **payload},
    )


def _assert_param_error(callback):
    with pytest.raises(CustomError) as error:
        callback()
    assert error.value.enum == ErrorCode.PARAM_ERROR


def _run_ci_list(monkeypatch, data, records, fields):
    operations = []
    query = _CiQuery(records, operations)
    ci_type = SimpleNamespace(id="type-1")

    async def get_ci_type(tenant, model_ref):
        assert tenant == "tenant-a"
        assert model_ref == "type-1"
        return ci_type

    async def get_fields(tenant, model_ref):
        assert (tenant, model_ref) == ("tenant-a", "type-1")
        return fields

    monkeypatch.setattr(views, "get_ci_type_or_404", get_ci_type)
    monkeypatch.setattr(views, "fields_for_type", get_fields)
    monkeypatch.setattr(
        views.SupplyChainCi,
        "filter",
        staticmethod(lambda *_args, **_kwargs: query),
    )
    monkeypatch.setattr(views, "response_ok", lambda payload: payload)
    request = SimpleNamespace(
        json=data,
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    payload = asyncio.run(inspect.unwrap(views.ci_list)(request))
    return payload, operations


def test_text_filters_are_case_insensitive_and_type_strict():
    contains = _filter("text", {"operator": "contains", "value": "DB-01"})
    exact = _filter("text", {"operator": "eq", "value": "DB-01"})

    assert views._matches_ci_field_filter("primary-db-01-node", contains)
    assert views._matches_ci_field_filter("db-01", exact)
    assert not views._matches_ci_field_filter("db-010", exact)
    assert not views._matches_ci_field_filter(101, contains)


def test_number_filter_supports_zero_inclusive_and_open_bounds():
    closed = _filter(
        "number",
        {"operator": "between", "lower": 0, "upper": 10},
    )
    lower_only = _filter("number", {"operator": "between", "lower": 10})
    upper_only = _filter("number", {"operator": "between", "upper": 0})

    assert views._matches_ci_field_filter(0, closed)
    assert views._matches_ci_field_filter(10, closed)
    assert not views._matches_ci_field_filter(-1, closed)
    assert not views._matches_ci_field_filter(11, closed)
    assert not views._matches_ci_field_filter(False, closed)
    assert views._matches_ci_field_filter(11, lower_only)
    assert views._matches_ci_field_filter(-1, upper_only)


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

    assert views._matches_ci_field_filter("2026-07-01", date_filter)
    assert views._matches_ci_field_filter("2026-07-20", date_filter)
    assert not views._matches_ci_field_filter("2026/07/10", date_filter)
    assert views._matches_ci_field_filter("2026-07-20 09:00:00", datetime_filter)
    assert views._matches_ci_field_filter("2026-07-20 18:00:00", datetime_filter)
    assert not views._matches_ci_field_filter("invalid", datetime_filter)


def test_enum_bool_and_ref_filters_use_exact_typed_values():
    enum_filter = _filter(
        "enum",
        {"operator": "eq", "value": "prod"},
        enum_options=[{"label": "Production", "value": "prod"}, "test"],
    )
    bool_filter = _filter("bool", {"operator": "eq", "value": False})
    ref_filter = _filter("ref", {"operator": "eq", "value": "ci-1"})

    assert views._matches_ci_field_filter("prod", enum_filter)
    assert not views._matches_ci_field_filter("Production", enum_filter)
    assert views._matches_ci_field_filter(False, bool_filter)
    assert not views._matches_ci_field_filter(0, bool_filter)
    assert not views._matches_ci_field_filter("false", bool_filter)
    assert views._matches_ci_field_filter("ci-1", ref_filter)
    assert not views._matches_ci_field_filter("CI-1", ref_filter)


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
        ("ref", {"operator": "contains", "value": "ci"}, {}),
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
        request = views._ci_field_filter_request(data)
        if request is not None:
            payload, legacy = request
            views._normalize_ci_field_filter(_field("text"), payload, legacy=legacy)

    _assert_param_error(normalize_request)


def test_legacy_text_and_ref_filters_remain_compatible():
    text_payload, text_legacy = views._ci_field_filter_request(
        {
            "field_key": "field",
            "field_keyword": "DB-01",
            "match_mode": "fuzzy",
        }
    )
    text_filter = views._normalize_ci_field_filter(
        _field("text"), text_payload, legacy=text_legacy
    )
    ref_payload, ref_legacy = views._ci_field_filter_request(
        {
            "field_key": "field",
            "field_keyword": "ci-1",
            "match_mode": "fuzzy",
        }
    )
    ref_filter = views._normalize_ci_field_filter(
        _field("ref"), ref_payload, legacy=ref_legacy
    )

    assert views._matches_ci_field_filter("primary-db-01-node", text_filter)
    assert ref_filter["operator"] == "eq"
    assert views._matches_ci_field_filter("ci-1", ref_filter)
    _assert_param_error(
        lambda: views._normalize_ci_field_filter(
            _field("number"), text_payload, legacy=text_legacy
        )
    )


@pytest.mark.parametrize("match_mode", ["fuzzy", "exact"])
def test_legacy_match_mode_without_filter_values_is_ignored(match_mode):
    assert views._ci_field_filter_request({"match_mode": match_mode}) is None


def test_ci_list_filters_before_paginating_and_preserves_filtered_total(monkeypatch):
    records = [
        _CiRecord(id="zero", attributes={"port": 0}),
        _CiRecord(id="five", attributes={"port": 5}),
        _CiRecord(id="ten", attributes={"port": 10}),
        _CiRecord(id="eleven", attributes={"port": 11}),
        _CiRecord(id="dirty", attributes={"port": "5"}),
        _CiRecord(id="boolean", attributes={"port": False}),
    ]

    payload, operations = _run_ci_list(
        monkeypatch,
        {
            "ci_type_id": "type-1",
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


def test_ci_list_accepts_legacy_text_filter(monkeypatch):
    records = [
        _CiRecord(id="match", attributes={"hostname": "Primary-DB"}),
        _CiRecord(id="miss", attributes={"hostname": "web-01"}),
    ]

    payload, _operations = _run_ci_list(
        monkeypatch,
        {
            "ci_type_id": "type-1",
            "field_key": "hostname",
            "field_keyword": "db",
            "match_mode": "fuzzy",
        },
        records,
        [_field("text", field_key="hostname")],
    )

    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == ["match"]


def test_ci_list_rejects_unknown_filter_field(monkeypatch):
    def run():
        _run_ci_list(
            monkeypatch,
            {
                "ci_type_id": "type-1",
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


def test_ci_list_rejects_filter_without_model(monkeypatch):
    monkeypatch.setattr(
        views.SupplyChainCi,
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

    _assert_param_error(lambda: asyncio.run(inspect.unwrap(views.ci_list)(request)))


def test_ci_list_without_field_filter_uses_database_pagination(monkeypatch):
    records = [_CiRecord(id=str(index), attributes={}) for index in range(1, 6)]

    payload, operations = _run_ci_list(
        monkeypatch,
        {"ci_type_id": "type-1", "page": 2, "size": 2},
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
