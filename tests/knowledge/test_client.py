# coding=utf-8
import httpx
import pytest

from services.knowledge.client import KnowledgeAgentClient


def _client(handler) -> KnowledgeAgentClient:
    client = KnowledgeAgentClient(endpoint="http://lion-agent:8000", token="t")
    client._transport = httpx.MockTransport(handler)
    return client


@pytest.mark.asyncio
async def test_search_posts_space_keys():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"hits": [{"document_id": "d1"}]})

    hits = await _client(handler).search(
        tenant_id="t1", space_keys=["ops"], query="q", top_k=5,
        embedding={"base_url": "http://e", "model": "m", "dimensions": 8},
    )
    assert seen["url"].endswith("/internal/v1/knowledge/search")
    assert "ops" in seen["body"]
    assert hits == [{"document_id": "d1"}]


@pytest.mark.asyncio
async def test_ingest_posts_storage_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(202, json={"status": "accepted"})

    await _client(handler).ingest(
        tenant_id="t1", space_key="ops", document_id="d1",
        storage_key="knowledge/d1/raw", filename="a.md",
        embedding={"base_url": "http://e", "model": "m", "dimensions": 8},
        callback_url="http://mgmt/cb",
    )
    assert "knowledge/d1/raw" in seen["body"]


@pytest.mark.asyncio
async def test_rebuild_space_posts_selected_document_ids():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "cleared", "deleted_count": 2})

    result = await _client(handler).rebuild_space(
        tenant_id="t1",
        space_key="ops",
        document_ids=["doc-1", "doc-2"],
        embedding={"base_url": "http://e", "model": "m", "dimensions": 8},
    )

    assert seen["url"].endswith("/internal/v1/knowledge/spaces/rebuild")
    assert "doc-1" in seen["body"] and "doc-2" in seen["body"]
    assert result["deleted_count"] == 2


@pytest.mark.asyncio
async def test_sends_internal_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"supported_extensions": [".md"]})

    await _client(handler).capabilities()
    assert seen["token"] == "t"


@pytest.mark.asyncio
async def test_http_error_becomes_custom_error():
    from libs.error_code.exception import CustomError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(CustomError):
        await _client(handler).capabilities()


@pytest.mark.asyncio
async def test_error_never_leaks_token():
    """异常信息里绝不能出现内部 token。"""
    from libs.error_code.exception import CustomError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(CustomError) as exc_info:
        await _client(handler).capabilities()
    assert "t" != str(exc_info.value) and "X-Internal-Token" not in str(exc_info.value)
