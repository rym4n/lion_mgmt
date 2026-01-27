# coding=utf-8
import httpx

from settings import (
    RY_EMBEDDING_API_BASE,
    RY_EMBEDDING_API_KEY,
    RY_EMBEDDING_MODEL,
    RY_EMBEDDING_PROVIDER,
)


async def get_embedding(text: str) -> list[float]:
    """
    生成文本 Embedding
    支持 OpenAI 和本地模型
    """
    if RY_EMBEDDING_PROVIDER == "openai":
        return await _get_openai_embedding(text)
    elif RY_EMBEDDING_PROVIDER == "local":
        return await _get_local_embedding(text)
    else:
        raise ValueError(f"Unknown embedding provider: {RY_EMBEDDING_PROVIDER}")


async def _get_openai_embedding(text: str) -> list[float]:
    """调用 OpenAI Embedding API"""
    url = f"{RY_EMBEDDING_API_BASE}/embeddings"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {RY_EMBEDDING_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"input": text, "model": RY_EMBEDDING_MODEL, "encoding_format": "float"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


async def _get_local_embedding(text: str) -> list[float]:
    """
    调用本地 Embedding 服务
    假设本地服务提供兼容 OpenAI 的接口
    """
    # 与 OpenAI 接口相同，但不需要 API Key
    url = f"{RY_EMBEDDING_API_BASE}/embeddings"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, json={"input": text, "model": RY_EMBEDDING_MODEL, "encoding_format": "float"}, timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


async def batch_get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    批量生成 Embeddings
    优化：减少 API 调用次数
    """
    if RY_EMBEDDING_PROVIDER == "openai":
        url = f"{RY_EMBEDDING_API_BASE}/embeddings"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {RY_EMBEDDING_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"input": texts, "model": RY_EMBEDDING_MODEL, "encoding_format": "float"},
                timeout=60.0,  # 批量输入
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]
    else:
        # 本地模型可能不支持批量，回退到单个调用
        return [await get_embedding(text) for text in texts]
