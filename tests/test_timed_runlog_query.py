import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest
from tortoise.expressions import Q

from models.runlog import Runlog
from services.timed import runlog_queries
from services.timed import views as timed_views


class SQLiteRunlogDB:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE runlog (
                tenant_id TEXT, timer_id TEXT, deleted INTEGER, run_id TEXT,
                start_time TEXT, ctime TEXT, status INTEGER
            )"""
        )
        self.calls = 0

    async def execute_query_dict(self, query, params):
        self.calls += 1
        return [dict(row) for row in self.connection.execute(query, params)]


@pytest.fixture
def timed_runlogs(monkeypatch):
    db = SQLiteRunlogDB()
    db.connection.executemany(
        "INSERT INTO runlog VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("tenant-1", "timer-1", 0, "run-a", "2026-10-13 10:00:00+00:00", "2026-10-13 10:02:00+00:00", 0),
            ("tenant-1", "timer-1", 0, "run-a", "2026-10-13 10:00:00+00:00", "2026-10-13 10:03:00+00:00", 0),
            ("tenant-1", "timer-1", 0, "run-b", "2026-10-13 11:00:00+00:00", "2026-10-13 11:01:00+00:00", 1),
            ("tenant-1", "timer-1", 0, "run-c", "2026-10-13 09:00:00+00:00", "2026-10-13 09:01:00+00:00", 0),
            ("tenant-1", "timer-2", 0, "other-timer", "2026-10-13 12:00:00+00:00", "2026-10-13 12:01:00+00:00", 0),
            ("tenant-2", "timer-1", 0, "other-tenant", "2026-10-13 12:00:00+00:00", "2026-10-13 12:01:00+00:00", 0),
            ("tenant-1", "timer-1", 1, "deleted", "2026-10-13 12:00:00+00:00", "2026-10-13 12:01:00+00:00", 0),
        ],
    )

    async def summaries(_model, _condition, run_ids):
        return {run_id: {"run_id": run_id} for run_id in run_ids}

    monkeypatch.setattr(runlog_queries, "runlog_batch_summaries", summaries)
    return SimpleNamespace(model=SimpleNamespace(_meta=SimpleNamespace(db=db)), db=db)


@pytest.mark.asyncio
async def test_timed_runlog_page_and_total_share_one_grouped_query(timed_runlogs):
    model, db = timed_runlogs.model, timed_runlogs.db
    total, page = await runlog_queries.paginated_timed_runlog_summaries(
        model, Q(tenant_id="tenant-1"), tenant_id="tenant-1", timer_id="timer-1", page=1, size=2
    )

    assert total == 3
    assert [row["run_id"] for row in page] == ["run-b", "run-a"]
    assert db.calls == 1

    total, page = await runlog_queries.paginated_timed_runlog_summaries(
        model, Q(tenant_id="tenant-1"), tenant_id="tenant-1", timer_id="timer-1", page=3, size=2
    )
    assert total == 3
    assert page == []


@pytest.mark.asyncio
async def test_timed_runlog_page_applies_optional_filters(timed_runlogs):
    total, page = await runlog_queries.paginated_timed_runlog_summaries(
        timed_runlogs.model,
        Q(tenant_id="tenant-1"),
        tenant_id="tenant-1",
        timer_id="timer-1",
        page=1,
        size=20,
        start_time=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 10, 13, 10, 59, tzinfo=timezone.utc),
        status=0,
    )

    assert total == 1
    assert page == [{"run_id": "run-a"}]


@pytest.mark.asyncio
async def test_timed_runlog_page_returns_zero_for_empty_scope(timed_runlogs):
    total, page = await runlog_queries.paginated_timed_runlog_summaries(
        timed_runlogs.model,
        Q(tenant_id="tenant-1"),
        tenant_id="tenant-1",
        timer_id="missing",
        page=1,
        size=20,
    )
    assert (total, page) == (0, [])


@pytest.mark.asyncio
async def test_timed_runlog_summary_endpoint_preserves_scope_and_response(monkeypatch):
    class TimedQuery:
        async def exists(self):
            return True

    class FakeTimed:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "id": "timer-1",
                "deleted": False,
                "internal": False,
                "tenant_id": "tenant-1",
            }
            return TimedQuery()

    async def scope(_request):
        return False

    async def summaries(_model, _condition, **kwargs):
        assert kwargs == {
            "tenant_id": "tenant-1",
            "timer_id": "timer-1",
            "page": 2,
            "size": 20,
            "start_time": datetime(2026, 10, 13, 10, tzinfo=timezone.utc),
            "end_time": datetime(2026, 10, 13, 11, tzinfo=timezone.utc),
            "status": 0,
        }
        return 3, [{"run_id": "run-a"}]

    monkeypatch.setattr(timed_views, "Timed", FakeTimed)
    monkeypatch.setattr(timed_views, "get_current_timed_internal_scope", scope)
    monkeypatch.setattr(timed_views, "paginated_timed_runlog_summaries", summaries)
    request = SimpleNamespace(
        json={
            "id": "timer-1",
            "page": 2,
            "size": 20,
            "summary_only": True,
            "start_time": "2026-10-13T10:00:00Z",
            "end_time": "2026-10-13T11:00:00Z",
            "status": 0,
        },
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(
                    roles=[SimpleNamespace(tenant_id="tenant-1", permissions=["timed:r"])]
                ),
            }
        ),
    )

    response = await timed_views.get_run_log.__wrapped__(request)
    assert orjson.loads(response.body) == {
        "code": 0,
        "data": {"page": 2, "size": 20, "total": 3, "data": [{"run_id": "run-a"}]},
    }


def test_timed_runlog_index_model_matches_migration():
    fields = ("tenant_id", "timer_id", "deleted", "run_id", "start_time", "ctime")
    assert fields in Runlog._meta.indexes

    migration = (
        Path(__file__).parents[1]
        / "migrations/models/130_20261014120000_timed_runlog_index.py"
    ).read_text(encoding="utf-8")
    for field in fields[:4]:
        assert f'"{field}"' in migration
    assert '"start_time" DESC' in migration
    assert '"ctime" DESC' in migration
    assert '"idx_runlog_tenant__c05b58"' in migration
