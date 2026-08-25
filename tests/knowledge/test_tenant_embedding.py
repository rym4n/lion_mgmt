# coding=utf-8
import pytest

from models.tenant_embedding import TenantEmbeddingConfig, compute_fingerprint


def test_fingerprint_changes_with_dimensions():
    a = compute_fingerprint("http://e/v1", "m", 1024)
    b = compute_fingerprint("http://e/v1", "m", 2048)
    assert a != b


def test_fingerprint_ignores_trailing_slash():
    assert compute_fingerprint("http://e/v1/", "m", 1024) == compute_fingerprint(
        "http://e/v1", "m", 1024
    )


def test_fingerprint_length_matches_lion_agent():
    """必须与 lion_agent 的 embedding_fingerprint 同算法同长度，否则对不上账。"""
    assert len(compute_fingerprint("http://e/v1", "m", 1024)) == 16


def test_dimensions_is_not_hardcoded():
    """旧 EmbeddingConfig 把维度写死 2048，新模型必须可变。"""
    field = TenantEmbeddingConfig._meta.fields_map["dimensions"]
    assert field.default != 2048 or field.default is None


def test_api_key_is_encrypted_column():
    from models.base import EncryptedCharField

    assert isinstance(
        TenantEmbeddingConfig._meta.fields_map["api_key"], EncryptedCharField
    )


def test_json_excludes_api_key():
    config = TenantEmbeddingConfig(
        tenant_id="t1", base_url="http://e/v1", model="m",
        dimensions=1024, api_key="secret", fingerprint="f" * 16,
    )
    assert "api_key" not in config.json
