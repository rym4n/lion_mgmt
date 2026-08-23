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


def test_target_uri_uses_space_relative_path():
    """包内 URI 只保留空间内相对路径，不含租户或版本前缀。"""
    from services.digital_employee.knowledge_ingest import _make_target_rel_path

    assert _make_target_rel_path("guide.md") == "guide.md"
    assert _make_target_rel_path("docs\\guide.md") == "docs/guide.md"


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


def test_mcp_upload_url_parser_removed():
    """旧 MCP 自然语言上传路径必须彻底移除。"""
    import services.digital_employee.knowledge_ingest as mod

    assert not hasattr(mod, "_parse_upload_url_from_mcp_response")


@pytest.mark.asyncio
async def test_knowledge_layer_disabled_skip():
    """测试知识层未启用时跳过 ingest。"""
    from services.digital_employee.knowledge_ingest import ingest_package_docs

    result = await ingest_package_docs(
        tenant_id="test-tenant",
        package_identity="role:test:1",
        package_type="role",
        package_key="test",
        version="1",
        doc_files=[],
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


def test_skill_space_key_prefix():
    from services.digital_employee.knowledge_ingest import skill_space_key

    assert skill_space_key("vuln-scan") == "skill-vuln-scan"


@pytest.mark.asyncio
async def test_ingest_creates_skill_space_and_upserts(monkeypatch):
    import services.digital_employee.knowledge_ingest as mod

    class FakeQuery:
        async def first(self):
            return None

    class FakeOutbox:
        status = "pending"
        attempt_count = 0
        error = None
        task_id = None

        @classmethod
        def filter(cls, **kwargs):
            return FakeQuery()

        @classmethod
        async def get_or_create(cls, **kwargs):
            return cls(), True

        async def save(self):
            return None

    ensured = {}
    upserted = []

    async def fake_ensure_space(tenant_id, space_key, name, source_type=None, source_ref=None):
        ensured.update({"space_key": space_key, "source_type": source_type, "source_ref": source_ref})

    async def fake_upsert(tenant_id, **kwargs):
        upserted.append(kwargs)
        return {"id": "doc-1", "status": "processing", "skipped": False}

    monkeypatch.setattr(mod, "PackageIngestOutbox", FakeOutbox)
    monkeypatch.setattr(mod, "ensure_space", fake_ensure_space)
    monkeypatch.setattr(mod, "upsert_document", fake_upsert)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "guide.md").write_text("# guide")
        docs = await mod.collect_package_docs(root)
        result = await mod.ingest_package_docs(
            tenant_id="t1",
            package_identity="skill:vuln-scan:1",
            package_type="skill",
            package_key="vuln-scan",
            version="1",
            doc_files=docs,
        )

    assert ensured == {
        "space_key": "skill-vuln-scan",
        "source_type": "skill_package",
        "source_ref": "vuln-scan",
    }
    assert len(upserted) == 1
    assert upserted[0]["space_key"] == "skill-vuln-scan"
    assert upserted[0]["source"] == "skill_package"
    assert result["succeeded"] == 1
