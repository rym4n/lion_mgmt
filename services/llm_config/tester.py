# coding=utf-8
"""LLM 连通性测试。

评审明确不做 SSRF 防护（见 SPEC §7.3）：调用方已受 root 权限 + ai_config 权限
+ License 三重限制，且自建端点常在内网，限制地址段反而挡住正常用法。
"""
import aiohttp

TEST_TIMEOUT_SECONDS = 10


async def _post_chat(base_url: str, api_key: str, model_name: str):
    """发一个最小 chat 请求。禁止跟随重定向，短超时。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    timeout = aiohttp.ClientTimeout(total=TEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            url,
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            allow_redirects=False,
        ) as response:
            await response.read()
            return response


async def test_connectivity(base_url: str, api_key: str, model_name: str) -> dict:
    """返回 {"ok": bool, "message": str}。message 中绝不含 api_key。"""
    try:
        response = await _post_chat(base_url, api_key, model_name)
    except aiohttp.ClientError as exc:
        return {"ok": False, "message": f"连接失败：{type(exc).__name__}"}
    except TimeoutError:
        return {"ok": False, "message": f"连接超时（{TEST_TIMEOUT_SECONDS} 秒）"}

    status = getattr(response, "status", 0)
    if 200 <= status < 300:
        return {"ok": True, "message": "连接成功"}
    if status == 401 or status == 403:
        return {"ok": False, "message": f"认证失败（HTTP {status}），请检查 API Key"}
    if status == 404:
        return {"ok": False, "message": f"端点不存在（HTTP {status}），请检查 Base URL"}

    detail = ""
    try:
        body = await response.json()
        if isinstance(body, dict):
            detail = str(body.get("error", {}).get("message", ""))[:200]
    except Exception:
        detail = ""
    return {"ok": False, "message": f"调用失败（HTTP {status}）{('：' + detail) if detail else ''}"}
