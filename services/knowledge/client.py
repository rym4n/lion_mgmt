# coding=utf-8
"""OpenViking REST 客户端（内部实现，方法签名不构成对外契约）。

与 services/openviking/client.py 的分工：
  OpenVikingAdminClient —— 持 root key，管 account/user/key（身份面）
  KnowledgeClient       —— 持租户 key，管 fs/resources/search（内容面）

上传走「API key 路径」：temp_upload 拿 temp_file_id，再 resources 带 to 入库
（openviking 0.4.13 server/routers/resources.py:158-167）。不要走 MCP 的
signed-token 路径，那是给 Agent 用的。
"""

from typing import Any

import httpx
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.openviking import TenantOpenVikingCredential
from settings import OPENVIKING_ADMIN_BASE_URL

DEFAULT_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0


class KnowledgeClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        # 测试注入点：MockTransport
        self._transport: httpx.BaseTransport | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or self.timeout,
            transport=self._transport,
        )

    async def _request(
        self, method: str, path: str, *, timeout: float | None = None, **kwargs
    ) -> Any:
        """统一请求出口：异常一律转 CustomError，绝不带上 api_key。"""
        url = f"{self.base_url}{path}"
        try:
            async with self._client(timeout) as client:
                response = await client.request(
                    method, url, headers=self._headers(), **kwargs
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "knowledge_client_http_error path=%s status=%s",
                path,
                exc.response.status_code,
            )
            raise CustomError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE) from None
        except Exception as exc:
            # 只记异常类型名：httpx 的部分异常 repr 会带上完整 URL 与 header
            logger.error(
                "knowledge_client_failed path=%s error_type=%s", path, type(exc).__name__
            )
            raise CustomError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE) from None

        if isinstance(body, dict) and "result" in body:
            return body["result"]
        return body

    # ---------- 只读 ----------

    async def ls(self, uri: str, *, recursive: bool = False) -> dict:
        return await self._request(
            "GET",
            "/api/v1/fs/ls",
            params={
                "uri": uri,
                "recursive": str(recursive).lower(),
                "output": "original",
                "node_limit": 1000,
            },
        )

    async def stat(self, uri: str) -> dict:
        return await self._request("GET", "/api/v1/fs/stat", params={"uri": uri})

    async def read(self, uri: str) -> str:
        result = await self._request("GET", "/api/v1/fs/read", params={"uri": uri})
        if isinstance(result, dict):
            return str(result.get("content") or "")
        return str(result or "")

    async def find(
        self,
        query: str,
        *,
        target_uri: list[str],
        limit: int = 5,
        level: list[int] | None = None,
    ) -> dict:
        """语义检索。

        target_uri 是空间隔离的落点：传进去什么范围，就只能搜出什么范围。
        OpenViking 的 FindRequest.target_uri 支持 Union[str, List[str]]，
        所以多空间是一次调用，不需要并发合并。
        context_type 固定 resource：memory 类内容不在空间体系内。
        """
        payload: dict[str, Any] = {
            "query": query,
            "target_uri": target_uri,
            "context_type": "resource",
            "limit": limit,
        }
        if level is not None:
            payload["level"] = level
        return await self._request("POST", "/api/v1/search/find", json=payload)

    async def task_status(self, task_id: str) -> dict:
        return await self._request("GET", f"/api/v1/tasks/{task_id}")

    async def health(self) -> dict:
        return await self._request("GET", "/health")

    # ---------- 写入 ----------

    async def mkdir(self, uri: str) -> dict:
        return await self._request("POST", "/api/v1/fs/mkdir", json={"uri": uri})

    async def _temp_upload(self, filename: str, content: bytes) -> str:
        result = await self._request(
            "POST",
            "/api/v1/resources/temp_upload",
            timeout=UPLOAD_TIMEOUT,
            files={"file": (filename, content, "application/octet-stream")},
        )
        temp_file_id = (result or {}).get("temp_file_id")
        if not temp_file_id:
            logger.error("knowledge_client_temp_upload_no_id filename=%s", filename)
            raise CustomError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE)
        return str(temp_file_id)

    async def upload_file(
        self, *, to: str, filename: str, content: bytes, instruction: str | None = None
    ) -> dict:
        """两段式上传：temp_upload → resources。"""
        temp_file_id = await self._temp_upload(filename, content)
        payload: dict[str, Any] = {
            "temp_file_id": temp_file_id,
            "to": to,
            "create_parent": True,
            "watch_interval": 0,
        }
        if instruction:
            payload["instruction"] = instruction
        return await self._request(
            "POST", "/api/v1/resources", timeout=UPLOAD_TIMEOUT, json=payload
        )

    async def add_url(self, *, to: str, url: str, instruction: str | None = None) -> dict:
        payload: dict[str, Any] = {
            "path": url,
            "to": to,
            "create_parent": True,
            "watch_interval": 0,
        }
        if instruction:
            payload["instruction"] = instruction
        return await self._request(
            "POST", "/api/v1/resources", timeout=UPLOAD_TIMEOUT, json=payload
        )

    async def rm(self, uri: str) -> dict:
        """单文件删除。目录级删除不经此方法——见 manager.delete_space。"""
        return await self._request(
            "DELETE", "/api/v1/fs", params={"uri": uri, "recursive": "false"}
        )


async def get_knowledge_client(tenant_id: str) -> KnowledgeClient | None:
    """按租户构造客户端；无凭证返回 None，调用方负责降级。"""
    if not OPENVIKING_ADMIN_BASE_URL:
        return None
    credential = await TenantOpenVikingCredential.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    if not credential:
        return None
    return KnowledgeClient(
        base_url=OPENVIKING_ADMIN_BASE_URL,
        api_key=credential.api_key,  # EncryptedCharField 自动解密
    )
