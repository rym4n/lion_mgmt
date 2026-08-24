# coding=utf-8
"""OpenViking embedding 配置校验单测。

配置格式以 middleware/lion_viking 的实际实现为准（JSON），
其 entrypoint.sh 用 ``python3 -m json.tool`` 校验、``sha256sum`` 比对哈希、
``"dimension": <int>`` 提取维度。
"""

import hashlib
import json

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.digital_employee.openviking import (
    compute_content_hash,
    parse_config,
    parse_dimension,
    validate_embedding_config,
)

# 与 middleware/lion_viking/ov.conf 同构的真实配置
REAL_CONFIG = json.dumps(
    {
        "server": {"host": "0.0.0.0", "port": 8000, "auth_mode": "api_key"},
        "vlm": {"provider": "openai", "model": "gpt-4o"},
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "dimension": 1536,
            }
        },
        "storage": {"workspace": "/app/data"},
    },
    indent=2,
)


class TestParseDimension:
    def test_parses_nested_dense_dimension(self):
        assert parse_dimension(REAL_CONFIG) == 1536

    def test_parses_top_level_embedding_dimension(self):
        content = json.dumps({"embedding": {"dimension": 3072}})
        assert parse_dimension(content) == 3072

    def test_parses_numeric_string_dimension(self):
        content = json.dumps({"embedding": {"dense": {"dimension": "1024"}}})
        assert parse_dimension(content) == 1024

    def test_returns_none_when_dimension_absent(self):
        content = json.dumps({"embedding": {"dense": {"model": "text-embedding-3"}}})
        assert parse_dimension(content) is None

    def test_returns_none_for_empty_content(self):
        assert parse_dimension("") is None
        assert parse_dimension("   \n  ") is None

    def test_falls_back_to_flat_key_form(self):
        # 兼容 `embedding.dimension = 1536` 扁平写法
        assert parse_dimension("embedding.dimension = 1536") == 1536
        assert parse_dimension("[embedding]\ndimension = 2048\n") == 2048

    def test_ignores_boolean_dimension(self):
        content = json.dumps({"embedding": {"dimension": True}})
        assert parse_dimension(content) is None

    def test_matches_entrypoint_first_dimension_semantics(self):
        """entrypoint.sh 取首个 dimension，解析结果需一致。"""
        content = json.dumps({"embedding": {"dense": {"dimension": 1536}}})
        assert parse_dimension(content) == 1536


class TestParseConfig:
    def test_parses_valid_json(self):
        assert parse_config(REAL_CONFIG)["embedding"]["dense"]["dimension"] == 1536

    def test_rejects_malformed_json(self):
        # entrypoint.sh 会拒绝非法 JSON 并拒绝启动，此处必须提前拦截
        with pytest.raises(CustomError) as exc:
            parse_config("{ not json ")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON

    def test_rejects_non_object_json(self):
        with pytest.raises(CustomError) as exc:
            parse_config("[1, 2, 3]")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON


class TestComputeContentHash:
    def test_matches_sha256_of_utf8_bytes(self):
        expected = hashlib.sha256(REAL_CONFIG.encode("utf-8")).hexdigest()
        assert compute_content_hash(REAL_CONFIG) == expected
        assert len(compute_content_hash(REAL_CONFIG)) == 64

    def test_hash_changes_with_content(self):
        other = REAL_CONFIG.replace("1536", "3072")
        assert compute_content_hash(REAL_CONFIG) != compute_content_hash(other)

    def test_hash_is_stable(self):
        assert compute_content_hash(REAL_CONFIG) == compute_content_hash(REAL_CONFIG)


class TestValidateEmbeddingConfig:
    def test_returns_dimension_and_hash(self):
        dimension, content_hash = validate_embedding_config(REAL_CONFIG)
        assert dimension == 1536
        assert content_hash == hashlib.sha256(REAL_CONFIG.encode("utf-8")).hexdigest()

    def test_rejects_empty_content(self):
        with pytest.raises(CustomError) as exc:
            validate_embedding_config("")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON

    def test_rejects_whitespace_only_content(self):
        with pytest.raises(CustomError):
            validate_embedding_config("   \n\t ")

    def test_rejects_invalid_json(self):
        with pytest.raises(CustomError) as exc:
            validate_embedding_config("embedding.dimension = 1536")
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_JSON

    def test_rejects_missing_dimension(self):
        content = json.dumps({"embedding": {"dense": {"model": "text-embedding-3"}}})
        with pytest.raises(CustomError) as exc:
            validate_embedding_config(content)
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_MISSING_DIMENSION

    @pytest.mark.parametrize("dimension", [0, -1, 4097, 999999])
    def test_rejects_out_of_range_dimension(self, dimension):
        content = json.dumps({"embedding": {"dense": {"dimension": dimension}}})
        with pytest.raises(CustomError) as exc:
            validate_embedding_config(content)
        assert exc.value.enum == ErrorCode.EMBEDDING_CONFIG_INVALID_DIMENSION

    @pytest.mark.parametrize("dimension", [1, 768, 1536, 3072, 4096])
    def test_accepts_boundary_dimensions(self, dimension):
        content = json.dumps({"embedding": {"dense": {"dimension": dimension}}})
        assert validate_embedding_config(content)[0] == dimension
