# coding=utf-8
"""OpenViking Admin REST 客户端。

用于管理 OpenViking 的 account、user 和 API key。
使用 root_api_key 进行管理操作。
"""

import httpx


class OpenVikingAdminClient:
    """OpenViking 管理客户端。

    通过 REST API 管理租户对应的 account/user/key。
    """

    def __init__(self, base_url: str, root_api_key: str, timeout: float = 30.0):
        """初始化客户端。

        Args:
            base_url: OpenViking 服务地址（如 http://lion-viking:8000）
            root_api_key: 管理员 root API key
            timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip("/")
        self.root_api_key = root_api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        """构造请求头（含 root API key）。"""
        return {
            "Authorization": f"Bearer {self.root_api_key}",
            "Content-Type": "application/json",
        }

    async def create_account(self, account_id: str) -> dict:
        """创建 account。

        Args:
            account_id: account 标识符（对应 AIOE tenant_id）

        Returns:
            account 信息

        Raises:
            httpx.HTTPStatusError: 请求失败
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/admin/accounts",
                headers=self._headers(),
                json={"account_id": account_id},
            )
            response.raise_for_status()
            return response.json()

    async def create_user(self, account_id: str, user_id: str = "default") -> dict:
        """在 account 下创建 user。

        Args:
            account_id: account 标识符
            user_id: user 标识符（默认 "default"）

        Returns:
            user 信息

        Raises:
            httpx.HTTPStatusError: 请求失败
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/accounts/{account_id}/users",
                headers=self._headers(),
                json={"user_id": user_id},
            )
            response.raise_for_status()
            return response.json()

    async def create_api_key(self, account_id: str, user_id: str = "default") -> str:
        """为 user 签发 API key。

        Args:
            account_id: account 标识符
            user_id: user 标识符

        Returns:
            API key 字符串

        Raises:
            httpx.HTTPStatusError: 请求失败
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/accounts/{account_id}/users/{user_id}/key",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            return data.get("api_key", "")

    async def delete_account(self, account_id: str) -> None:
        """删除 account（永久清除）。

        Args:
            account_id: account 标识符

        Raises:
            httpx.HTTPStatusError: 请求失败
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/admin/accounts/{account_id}",
                headers=self._headers(),
            )
            response.raise_for_status()

    async def provision_tenant(self, account_id: str, user_id: str = "default") -> str:
        """为租户完整创建 account + user + key（幂等）。

        Args:
            account_id: account 标识符（对应 tenant_id）
            user_id: user 标识符

        Returns:
            API key

        Raises:
            httpx.HTTPStatusError: 非幂等的失败
        """
        # 创建 account（幂等：已存在时 409，视为成功）
        try:
            await self.create_account(account_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 409:  # Conflict (already exists)
                raise

        # 创建 user（幂等）
        try:
            await self.create_user(account_id, user_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 409:
                raise

        # 签发 key（可重复调用，返回新 key）
        return await self.create_api_key(account_id, user_id)
