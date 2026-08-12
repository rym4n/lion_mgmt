# coding=utf-8

import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.error_code import ErrorCode  # noqa: E402
from libs.error_code.exception import CustomError  # noqa: E402
from services.workflow.views import (  # noqa: E402
    _open_workflow_archive,
    _rewrite_imported_action_versions,
)


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


def test_workflow_import_rejects_yaml_as_zip_format_error():
    with pytest.raises(CustomError) as exc_info:
        _open_workflow_archive(b'version: "1.0"\ntasks:\n  test:\n    cmd: echo test\n')

    assert exc_info.value.enum == ErrorCode.ZIP_FILE_FORMAT_ERROR


def test_workflow_import_opens_exported_zip_package():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("workflow.json", "[]")

    with _open_workflow_archive(buffer.getvalue()) as archive:
        assert archive.namelist() == ["workflow.json"]
