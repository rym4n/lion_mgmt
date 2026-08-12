# coding=utf-8
import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import LION_AGENT_ENDPOINT, LION_AGENT_INTERNAL_TOKEN


class LionAgentExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LionAgentClient:
    def __init__(self, endpoint: str | None = None, token: str | None = None, timeout: float = 30.0):
        self.endpoint = (endpoint or LION_AGENT_ENDPOINT).rstrip("/")
        self.token = token if token is not None else LION_AGENT_INTERNAL_TOKEN
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Internal-Token"] = self.token
        return headers

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        execution_id = str(payload.get("execution_id") or "")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.endpoint}/internal/v1/execute", json=payload, headers=self._headers)
                if response.status_code == 422:
                    detail = response.json().get("detail", {})
                    if isinstance(detail, dict) and detail.get("code"):
                        raise LionAgentExecutionError(
                            str(detail["code"]),
                            str(detail.get("message") or detail["code"]),
                            retryable=bool(detail.get("retryable", False)),
                        )
                response.raise_for_status()
                return response.json()
        except LionAgentExecutionError:
            raise
        except asyncio.CancelledError:
            await self._cancel_after_abort(execution_id)
            raise
        except httpx.HTTPError as exc:
            await self._cancel_after_abort(execution_id)
            raise CustomError(ErrorCode.LION_AGENT_UNAVAILABLE) from exc

    async def stream_execute(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        execution_id = str(payload.get("execution_id") or "")
        completed = False
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.endpoint}/internal/v1/execute/stream",
                    json=payload,
                    headers=self._headers,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            yield line
                    completed = True
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            raise CustomError(ErrorCode.LION_AGENT_UNAVAILABLE) from exc
        finally:
            if not completed:
                await self._cancel_after_abort(execution_id)

    async def cancel_execution(self, execution_id: str) -> bool:
        if not execution_id:
            return False
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self.endpoint}/internal/v1/executions/{execution_id}/cancel",
                headers=self._headers,
            )
            response.raise_for_status()
            return bool(response.json().get("cancelled"))

    async def _cancel_after_abort(self, execution_id: str) -> None:
        if not execution_id:
            return
        cancel_task = asyncio.create_task(self.cancel_execution(execution_id))
        with suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(cancel_task)

    async def sync_workspace(
        self,
        workspace_id: str,
        files: dict[str, Any],
        role_version: int | None = None,
        skill_versions: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        payload = {"files": files, "role_version": role_version, "skill_versions": skill_versions or {}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.endpoint}/internal/v1/workspaces/{workspace_id}/sync",
                    json=payload,
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise CustomError(ErrorCode.WORKSPACE_SYNC_FAILED) from exc

    async def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.endpoint}/internal/v1/workspaces/{workspace_id}/delete",
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise CustomError(ErrorCode.LION_AGENT_UNAVAILABLE) from exc
