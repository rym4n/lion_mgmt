# coding=utf-8
"""knowledge_ingest 模块单元测试（不依赖 OpenViking）。"""

import tempfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_collect_package_docs_whitelist():
    """测试文档收集的后缀白名单。"""
    from services.digital_employee.knowledge_ingest import collect_package_docs

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # 创建符合白名单的文件
        (root / "README.md").write_text("# README")
        (root / "doc.txt").write_text("text")
        (root / "manual.pdf").write_bytes(b"fake pdf")
        (root / "guide.rst").write_text("guide")
        (root / "spec.adoc").write_text("spec")

        # 创建不符合白名单的文件
        (root / "script.py").write_text("print('hello')")
        (root / "config.yaml").write_text("key: value")
        (root / "secret.env").write_text("KEY=secret")
        (root / "deploy.sh").write_text("#!/bin/bash")

        docs = await collect_package_docs(root)

        # 断言：只收集白名单文件
        collected_names = {Path(rel).name for _, rel in docs}
        assert "README.md" in collected_names
        assert "doc.txt" in collected_names
        assert "manual.pdf" in collected_names
        assert "guide.rst" in collected_names
        assert "spec.adoc" in collected_names

        # 断言：不收集非白名单文件
        assert "script.py" not in collected_names
        assert "config.yaml" not in collected_names
        assert "secret.env" not in collected_names
        assert "deploy.sh" not in collected_names


@pytest.mark.asyncio
async def test_collect_package_docs_size_limits():
    """测试文件大小限制。"""
    from services.digital_employee.knowledge_ingest import (
        collect_package_docs,
        MAX_FILE_SIZE,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # 正常大小文件
        (root / "small.md").write_text("small content")

        # 超大文件（应被跳过）
        oversized = root / "huge.md"
        oversized.write_bytes(b"x" * (MAX_FILE_SIZE + 1))

        docs = await collect_package_docs(root)

        collected_names = {Path(rel).name for _, rel in docs}
        assert "small.md" in collected_names
        assert "huge.md" not in collected_names


@pytest.mark.asyncio
async def test_make_target_uri_deterministic():
    """测试目标 URI 的确定性。"""
    from services.digital_employee.knowledge_ingest import _make_target_uri

    uri1 = _make_target_uri("tenant123", "role", "monitor", "1", "README.md")
    uri2 = _make_target_uri("tenant123", "role", "monitor", "1", "README.md")

    # 同样输入必须产生同样 URI
    assert uri1 == uri2

    # 格式验证
    assert uri1.startswith("viking://resources/")
    assert "tenant123" in uri1
    assert "role" in uri1
    assert "monitor" in uri1
    assert "/1/" in uri1
    assert "README.md" in uri1


@pytest.mark.asyncio
async def test_compute_content_hash():
    """测试内容 hash 计算。"""
    from services.digital_employee.knowledge_ingest import _compute_content_hash

    content1 = b"test content"
    content2 = b"test content"
    content3 = b"different content"

    hash1 = _compute_content_hash(content1)
    hash2 = _compute_content_hash(content2)
    hash3 = _compute_content_hash(content3)

    # 相同内容产生相同 hash
    assert hash1 == hash2

    # 不同内容产生不同 hash
    assert hash1 != hash3

    # hash 格式验证（SHA256 hex）
    assert len(hash1) == 64
    assert all(c in "0123456789abcdef" for c in hash1)


@pytest.mark.asyncio
async def test_parse_upload_url():
    """测试从 MCP 响应解析 upload URL。"""
    from services.digital_employee.knowledge_ingest import (
        _parse_upload_url_from_mcp_response,
    )

    # 成功解析
    response1 = "Please upload your file to: http://lion-viking:8000/api/v1/resources/temp_upload?token=abc123xyz"
    url1 = _parse_upload_url_from_mcp_response(response1)
    assert (
        url1 == "http://lion-viking:8000/api/v1/resources/temp_upload?token=abc123xyz"
    )

    # HTTPS
    response2 = "Upload here: https://example.com/temp_upload?token=xyz789"
    url2 = _parse_upload_url_from_mcp_response(response2)
    assert url2 == "https://example.com/temp_upload?token=xyz789"

    # 无 URL
    response3 = "Something went wrong"
    url3 = _parse_upload_url_from_mcp_response(response3)
    assert url3 is None


@pytest.mark.asyncio
async def test_knowledge_layer_disabled_skip():
    """测试知识层未启用时跳过 ingest。"""
    from services.digital_employee.knowledge_ingest import ingest_package_docs

    # 知识层未启用时，knowledge_client=None 应该跳过
    result = await ingest_package_docs(
        tenant_id="test-tenant",
        package_identity="role:test:1",
        package_type="role",
        package_key="test",
        version="1",
        doc_files=[],
        knowledge_client=None,
    )

    assert result["succeeded"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == 0  # 空列表
    assert result["errors"] == []


def test_imports():
    """测试模块可以正常导入。"""
    from models.digital_employee import PackageIngestOutbox
    from services.digital_employee import knowledge_ingest

    assert hasattr(knowledge_ingest, "collect_package_docs")
    assert hasattr(knowledge_ingest, "ingest_package_docs")
    assert hasattr(knowledge_ingest, "forget_package_docs")
    assert PackageIngestOutbox is not None
