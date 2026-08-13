# coding=utf-8

from types import SimpleNamespace

import pytest

from services.ticket.flow_package import (
    PACKAGE_MANIFEST,
    active_snapshot,
    build_flow_archive,
    parse_flow_archive,
)


def _flow():
    return {
        "name": "变更审批",
        "remark": "变更流程",
        "ticket_type": {"name": "变更", "code": "CHG"},
        "versions": [
            {
                "version": "v20260813010101",
                "status": 1,
                "change_desc": "首版",
                "flow_json": {"nodes": [], "edges": []},
                "form_schema": {"fields": []},
            },
            {
                "version": "draft",
                "status": 0,
                "change_desc": None,
                "flow_json": {"nodes": [{"id": "start"}], "edges": []},
                "form_schema": {"fields": []},
            },
        ],
    }


def test_ticket_flow_package_round_trip():
    archive = build_flow_archive([_flow()], "2026-08-13T00:00:00+08:00")
    payload = parse_flow_archive(archive)

    assert payload["flows"][0]["name"] == "变更审批"
    assert active_snapshot(payload["flows"][0]["versions"])["status"] == 0
    assert PACKAGE_MANIFEST.encode() in archive


def test_active_snapshot_prioritizes_status_for_dicts_and_models():
    snapshots = [
        {"status": 2, "version": "old"},
        {"status": 1, "version": "published"},
        {"status": 0, "version": "draft"},
    ]
    assert active_snapshot(snapshots)["version"] == "draft"

    versions = [
        SimpleNamespace(status=2, version="old"),
        SimpleNamespace(status=1, version="published"),
    ]
    assert active_snapshot(versions).version == "published"
    assert active_snapshot([]) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda flow: flow.update(name=""),
        lambda flow: flow.pop("ticket_type"),
        lambda flow: flow.update(versions=[]),
    ],
)
def test_ticket_flow_package_rejects_invalid_manifests(mutate):
    flow = _flow()
    mutate(flow)
    archive = build_flow_archive([flow], "2026-08-13T00:00:00+08:00")

    with pytest.raises(ValueError):
        parse_flow_archive(archive)
