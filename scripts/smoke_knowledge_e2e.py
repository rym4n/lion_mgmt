#!/usr/bin/env python3
"""Optional deployed-environment smoke test for the knowledge RAG chain.

This script is intentionally not run as part of local unit tests. It requires
an authenticated management API and a deployed lion_agent/RustFS stack.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("KNOWLEDGE_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("KNOWLEDGE_AUTH_TOKEN", ""))
    parser.add_argument("--space-key", default=os.environ.get("KNOWLEDGE_SPACE_KEY", "rag-smoke"))
    parser.add_argument("--embedding-base-url", default=os.environ.get("KNOWLEDGE_EMBEDDING_BASE_URL", ""))
    parser.add_argument("--embedding-model", default=os.environ.get("KNOWLEDGE_EMBEDDING_MODEL", ""))
    parser.add_argument("--embedding-dimensions", type=int, default=int(os.environ.get("KNOWLEDGE_EMBEDDING_DIMENSIONS", "0")))
    parser.add_argument("--embedding-api-key", default=os.environ.get("KNOWLEDGE_EMBEDDING_API_KEY", ""))
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not args.token or not args.embedding_base_url or not args.embedding_model or args.embedding_dimensions <= 0:
        print("Set KNOWLEDGE_AUTH_TOKEN and embedding settings before running this deployed smoke test.", file=sys.stderr)
        return 2

    headers = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), headers=headers, timeout=30) as client:
        async def post(path: str, payload: dict) -> dict:
            response = await client.post(path, json=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("code") not in (None, 0, "0"):
                raise RuntimeError(f"{path} returned code={body.get('code')}")
            return body.get("data") or {}

        await post("/knowledge/embedding-config", {
            "base_url": args.embedding_base_url,
            "model": args.embedding_model,
            "dimensions": args.embedding_dimensions,
            "api_key": args.embedding_api_key,
        })
        try:
            await post("/knowledge/space/create", {
                "space_key": args.space_key,
                "name": "RAG smoke space",
            })
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (409, 422):
                raise

        document = await post("/knowledge/document/upsert", {
            "space_key": args.space_key,
            "filename": "smoke.md",
            "content": "Knowledge RAG smoke document.",
        })
        document_id = str(document["id"])
        result = await post("/knowledge/search", {"query": "RAG smoke", "space_key": args.space_key})
        if not any(str(hit.get("document_id")) == document_id for hit in result if isinstance(hit, dict)):
            raise RuntimeError("search did not return the smoke document")
        await post("/knowledge/document/delete", {"document_id": document_id})
        print(f"knowledge smoke passed: document_id={document_id}")
    return 0


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
