# coding=utf-8

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.workflow.views import _rewrite_imported_action_versions  # noqa: E402


def test_rewrite_imported_action_versions_updates_action_nodes_only():
    flow_json = {
        "nodes": [
            {
                "id": "action-1",
                "type": "action-node",
                "properties": {
                    "app_name": "cef_show",
                    "version": "source-version",
                    "action": "show",
                },
            },
            {
                "id": "action-2",
                "type": "action-node",
                "properties": {
                    "app_name": "cef_show",
                    "version": "unmapped-version",
                    "action": "show",
                },
            },
            {
                "id": "start-1",
                "type": "start-node",
                "properties": {
                    "app_name": "cef_show",
                    "version": "source-version",
                },
            },
        ]
    }

    rewritten = _rewrite_imported_action_versions(
        flow_json,
        {("cef_show", "source-version"): "tenant-version"},
    )

    assert rewritten["nodes"][0]["properties"]["version"] == "tenant-version"
    assert rewritten["nodes"][1]["properties"]["version"] == "unmapped-version"
    assert rewritten["nodes"][2]["properties"]["version"] == "source-version"


def test_rewrite_imported_action_versions_tolerates_empty_payloads():
    assert _rewrite_imported_action_versions({}, {("tool", "old"): "new"}) == {}
    assert _rewrite_imported_action_versions({"nodes": None}, {("tool", "old"): "new"}) == {"nodes": None}
