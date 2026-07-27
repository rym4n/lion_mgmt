import orjson

from services.tools.views import _sse_json_event


def test_tool_upload_results_use_json_sse_payload():
    results = [
        {"tool": "valid.zip", "msg": "success", "tool_name": "Valid"},
        {"tool": "invalid.zip", "msg": "config文件不存在", "tool_name": ""},
    ]

    event = _sse_json_event(results)

    assert event.startswith("data: ")
    assert event.endswith("\n\n")
    assert orjson.loads(event.removeprefix("data: ").strip()) == results
