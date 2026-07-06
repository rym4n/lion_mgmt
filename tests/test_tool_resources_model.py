# coding=utf-8
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orjson

import libs.utils as utils
from models.tool_management import ToolResources


def test_tool_resources_encrypts_large_payload_without_truncation(monkeypatch):
    monkeypatch.setattr(utils, "RY_RES_KEY", "test-resource-key-for-aioe-123456")
    payload = {
        "addresses": {
            "data_type": "string",
            "description": "large resource field",
            "required": True,
            "value": ",".join(f"192.168.{idx // 256}.{idx % 256}" for idx in range(700)),
        }
    }
    raw = orjson.dumps(payload).decode()
    assert len(raw) > 5000

    field = ToolResources._meta.fields_map["resources"]
    encrypted = field.to_db_value(raw, None)
    restored = field.to_python_value(encrypted)

    assert restored == raw
    assert orjson.loads(restored) == payload
