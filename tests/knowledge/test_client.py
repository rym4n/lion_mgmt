# coding=utf-8
"""KnowledgeClient 的请求构造契约（SPEC §8、§9）。

用假 httpx transport 断言实际发出的请求，不连真实 OpenViking。
"""

import httpx
import pytest


def _client_with(handler):
    """构造一个把请求交给 handler 处理的 KnowledgeClient。"""
    from services.knowledge.client import KnowledgeClient

    client = KnowledgeClient(base_url="http://ov:8000", api_key="sk-secret-abc")
    client._transport = httpx.MockTransport(handler)
    return client


@pytest.mark.asyncio
async def test_find_sends_target_uri_as_list():
    """target_uri 必须以列表原样传出——这是空间隔离的落点，
    被压成字符串会让多空间检索退化成单空间或全库。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import orjson

        captured["url"] = str(request.url)
        captured["body"] = orjson.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "result": {"contexts": []}})

    client = _client_with(handler)
    await client.find(
        query="磁盘告警怎么处理",
        target_uri=[
            "viking://resources/spaces/monitoring",
            "viking://resources/spaces/runbook",
        ],
        limit=5,
    )

    assert captured["url"] == "http://ov:8000/api/v1/search/find"
    assert captured["body"]["target_uri"] == [
        "viking://resources/spaces/monitoring",
        "viking://resources/spaces/runbook",
    ]
    assert captured["body"]["query"] == "磁盘告警怎么处理"
    assert captured["body"]["limit"] == 5


@pytest.mark.asyncio
async def test_find_fixes_context_type_to_resource():
    """不固定 context_type 会把 OpenViking 自身的 memory 内容也搜出来，
    那部分不在空间体系内，等于绕过隔离。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import orjson

        captured["body"] = orjson.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "result": {"contexts": []}})

    client = _client_with(handler)
    await client.find(query="x", target_uri=["viking://resources/spaces/a"])

    assert captured["body"]["context_type"] == "resource"


@pytest.mark.asyncio
async def test_authorization_header_present():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": "ok", "result": {}})

    client = _client_with(handler)
    await client.stat("viking://resources/spaces/a/x.md")

    assert captured["auth"] == "Bearer sk-secret-abc"


@pytest.mark.asyncio
async def test_upload_file_is_two_phase():
    """先 temp_upload 拿 temp_file_id，再 resources 带 to 入库。
    顺序或参数错了会得到「上传成功但搜不到」——最难排查的一类故障。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/api/v1/resources/temp_upload":
            return httpx.Response(
                200, json={"status": "ok", "result": {"temp_file_id": "tmp-123"}}
            )
        import orjson

        body = orjson.loads(request.content)
        assert body["temp_file_id"] == "tmp-123"
        assert body["to"] == "viking://resources/spaces/ops/run.md"
        assert body["create_parent"] is True
        assert body["watch_interval"] == 0
        return httpx.Response(200, json={"status": "ok", "result": {"task_id": "task-9"}})

    client = _client_with(handler)
    result = await client.upload_file(
        to="viking://resources/spaces/ops/run.md",
        filename="run.md",
        content=b"# runbook",
    )

    assert calls == ["/api/v1/resources/temp_upload", "/api/v1/resources"]
    assert result["task_id"] == "task-9"


@pytest.mark.asyncio
async def test_watch_interval_always_zero():
    """正值会创建持续刷新任务，而 watch 管理端点本设计不开放，
    会留下没人能取消的长期开销（SPEC §8.2）。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import orjson

        if request.url.path == "/api/v1/resources":
            captured["body"] = orjson.loads(request.content)
        return httpx.Response(200, json={"status": "ok", "result": {"task_id": "t"}})

    client = _client_with(handler)
    await client.add_url(to="viking://resources/spaces/ops/a.md", url="https://x/a.md")

    assert captured["body"]["watch_interval"] == 0


@pytest.mark.asyncio
async def test_api_key_not_in_exception_message():
    """异常信息会进日志、可能进响应，绝不能带 key。"""
    from libs.error_code.exception import CustomError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client_with(handler)
    with pytest.raises(CustomError) as excinfo:
        await client.stat("viking://resources/spaces/a/x.md")

    assert "sk-secret-abc" not in str(excinfo.value)
    assert "sk-secret-abc" not in repr(excinfo.value)


@pytest.mark.asyncio
async def test_rm_never_recursive():
    """目录级删除只允许在 manager 层显式发起，client 的 rm 恒为单文件。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"status": "ok", "result": {}})

    client = _client_with(handler)
    await client.rm("viking://resources/spaces/ops/a.md")

    assert "recursive=false" in captured["url"].lower()
