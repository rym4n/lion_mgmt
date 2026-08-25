# coding=utf-8
"""知识库执行层客户端：调 lion_agent 的 /internal/v1/knowledge/*。

取代旧的外部存储 REST 客户端。不再有 per-tenant 凭证——
租户隔离由 lion_agent 侧的 collection 命名承担（SPEC D1/D6），
本客户端只持有服务间的内部 token。
"""

from typing import Any

import httpx
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import LION_AGENT_ENDPOINT, LION_AGENT_INTERNAL_TOKEN

DEFAULT_TIMEOUT = 30.0
INGEST_TIMEOUT = 60.0


class KnowledgeAgentClient:
    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.endpoint = (endpoint or LION_AGENT_ENDPOINT).rstrip("/")
        self._token = token if token is not None else LION_AGENT_INTERNAL_TOKEN
        self.timeout = timeout
        # 测试注入点：MockTransport
        self._transport: httpx.BaseTransport | None = None

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Token": self._token}

    async def _request(
        self, method: str, path: str, *, timeout: float | None = None, **kwargs
    ) -> Any:
        """统一出口：异常一律转 CustomError，绝不带上 token。"""
        url = f"{self.endpoint}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout, transport=self._transport
            ) as client:
                response = await client.request(
                    method, url, headers=self._headers(), **kwargs
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "knowledge_agent_http_error path=%s status=%s",
                path, exc.response.status_code,
            )
            raise CustomError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE) from None
        except Exception as exc:
            # 只记异常类型名：httpx 的部分异常 repr 会带上完整 URL 与 header
            logger.error(
                "knowledge_agent_failed path=%s error_type=%s", path, type(exc).__name__
            )
            raise CustomError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE) from None

    async def capabilities(self) -> dict:
        return await self._request("GET", "/internal/v1/knowledge/capabilities")

    async def ingest(
        self,
        *,
        tenant_id: str,
        space_key: str,
        document_id: str,
        storage_key: str,
        filename: str,
        embedding: dict,
        callback_url: str | None = None,
    ) -> dict:
        return await self._request(
            "POST",
            "/internal/v1/knowledge/ingest",
            timeout=INGEST_TIMEOUT,
            json={
                "tenant_id": tenant_id,
                "space_key": space_key,
                "document_id": document_id,
                "storage_key": storage_key,
                "filename": filename,
                "embedding": embedding,
                "callback_url": callback_url,
            },
        )

    async def search(
        self,
        *,
        tenant_id: str,
        space_keys: list[str],
        query: str,
        top_k: int,
        embedding: dict,
    ) -> list[dict]:
        result = await self._request(
            "POST",
            "/internal/v1/knowledge/search",
            json={
                "tenant_id": tenant_id,
                "space_keys": space_keys,
                "query": query,
                "top_k": top_k,
                "embedding": embedding,
            },
        )
        return (result or {}).get("hits") or []

    async def delete_document(
        self, *, tenant_id: str, space_key: str, document_id: str, embedding: dict
    ) -> dict:
        return await self._request(
            "POST",
            "/internal/v1/knowledge/documents/delete",
            json={
                "tenant_id": tenant_id,
                "space_key": space_key,
                "document_id": document_id,
                "embedding": embedding,
            },
        )

    async def rebuild(self, *, tenant_id: str, embedding: dict) -> dict:
        return await self._request(
            "POST",
            "/internal/v1/knowledge/collections/rebuild",
            json={"tenant_id": tenant_id, "embedding": embedding},
        )


def get_knowledge_client() -> KnowledgeAgentClient:
    """构造客户端。

    不再按租户区分、也不再返回 None——旧凭证体系已删除
    （SPEC D6）。租户身份由每次调用的 tenant_id 参数承载。
    """
    return KnowledgeAgentClient()
