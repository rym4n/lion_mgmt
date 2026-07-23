# coding=utf-8
import asyncio
from types import SimpleNamespace

from services.event_ingestion import views as event_ingestion_views
from services.system_config import views as system_views


def _run_handler(handler, request):
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return asyncio.run(handler(request))


def test_audit_query_is_scoped_to_current_tenant(monkeypatch):
    queries = []

    async def fake_query_vlog(query, **_kwargs):
        queries.append(query)
        return []

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: data)
    request = SimpleNamespace(
        json={"page": 1, "size": 15},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    result = _run_handler(system_views.search_audit, request)

    assert result["data"] == []
    assert len(queries) == 2
    assert all('tenant_id:"tenant-a"' in query for query in queries)


def test_outgoing_vector_sinks_are_filtered_per_tenant():
    base_config = {
        "sources": {"aioe_kafka_audit": {"type": "kafka"}},
        "transforms": {
            "keep_me": {"type": "remap", "inputs": ["source"]},
            "aioe_tenant_audit_stale": {
                "type": "filter",
                "inputs": ["aioe_kafka_audit"],
            },
        },
        "sinks": {
            "aioe_default_audit": {
                "type": "elasticsearch",
                "inputs": ["aioe_kafka_audit", "health_logs"],
            },
            "legacy_global_sink": {
                "type": "socket",
                "inputs": ["aioe_kafka_audit"],
            },
        },
    }
    configs = {
        "tenant-a": [
            {
                "sink_type": "syslog",
                "sink_address": "192.0.2.10:514",
                "sink_protocol": "tcp",
            }
        ],
        "tenant-b": [
            {
                "sink_type": "kafka",
                "sink_address": "broker.example:9092",
                "kafka_topic": "tenant-b-audit",
            }
        ],
    }

    result = system_views._build_outgoing_vector_config(base_config, configs)

    assert set(result["sinks"]) == {
        "aioe_default_audit",
        *{
            name
            for name in result["sinks"]
            if name.startswith(system_views._TENANT_AUDIT_SINK_PREFIX)
        },
    }
    assert "legacy_global_sink" not in result["sinks"]
    assert "aioe_tenant_audit_stale" not in result["transforms"]

    filters = {
        transform["condition"]: name
        for name, transform in result["transforms"].items()
        if name.startswith(system_views._TENANT_AUDIT_TRANSFORM_PREFIX)
    }
    assert set(filters) == {
        '.tenant_id == "tenant-a"',
        '.tenant_id == "tenant-b"',
    }
    tenant_a_filter = filters['.tenant_id == "tenant-a"']
    tenant_b_filter = filters['.tenant_id == "tenant-b"']
    custom_sinks = [
        sink
        for name, sink in result["sinks"].items()
        if name != "aioe_default_audit"
    ]
    assert {tuple(sink["inputs"]) for sink in custom_sinks} == {
        (tenant_a_filter,),
        (tenant_b_filter,),
    }

    without_tenant_a = system_views._build_outgoing_vector_config(
        base_config,
        {"tenant-a": [], "tenant-b": configs["tenant-b"]},
    )
    assert not any(
        transform.get("condition") == '.tenant_id == "tenant-a"'
        for transform in without_tenant_a["transforms"].values()
    )
    assert any(
        transform.get("condition") == '.tenant_id == "tenant-b"'
        for transform in without_tenant_a["transforms"].values()
    )


def test_vector_validation_can_skip_cross_tenant_healthchecks(monkeypatch):
    commands = []

    monkeypatch.setattr(
        event_ingestion_views,
        "run_linux_command",
        lambda command: commands.append(command) or {"code": 0},
    )

    assert event_ingestion_views.check_vector_config(
        {"sources": {}, "transforms": {}, "sinks": {}},
        skip_healthchecks=True,
    )
    assert "vector validate --skip-healthchecks --config-yaml" in commands[0]


def test_legacy_outgoing_config_is_only_loaded_for_its_tenant(monkeypatch):
    rows = [
        SimpleNamespace(
            key="outgoing_audit_log",
            tenant_id="tenant-a",
            value="[{'sink_type': 'syslog', 'sink_address': '192.0.2.10:514'}]",
        ),
        SimpleNamespace(
            key="outgoing_audit_log:tenant-a",
            tenant_id="tenant-a",
            value='[{"sink_type":"syslog","sink_address":"192.0.2.11:514"}]',
        ),
        SimpleNamespace(
            key="outgoing_audit_log:tenant-b",
            tenant_id="tenant-b",
            value='[{"sink_type":"syslog","sink_address":"192.0.2.20:514"}]',
        ),
    ]

    class _Query:
        async def all(self):
            return rows

    monkeypatch.setattr(system_views.Config, "filter", lambda **_kwargs: _Query())

    configs = asyncio.run(system_views._load_outgoing_configs())

    assert configs["tenant-a"][0]["sink_address"] == "192.0.2.11:514"
    assert configs["tenant-b"][0]["sink_address"] == "192.0.2.20:514"
    assert system_views._outgoing_config_key("tenant-b") == "outgoing_audit_log:tenant-b"
