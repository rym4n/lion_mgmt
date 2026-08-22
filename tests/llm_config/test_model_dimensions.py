# coding=utf-8
"""LlmModelConfig 的 dimensions 字段与 purpose 取值扩展（SPEC §5.6）。

本仓测试不连数据库，模型层断言直接读 Tortoise 字段描述对象。
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "models" / "llm_model_config.py"


def _import_model():
    """安全导入 models.llm_model_config，兼容其他测试的 sys.modules 操作。"""
    root = Path(__file__).resolve().parents[2]
    old_path = sys.path.copy()
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        # 清除可能被其他测试污染的 models 模块
        sys.modules.pop("models", None)
        sys.modules.pop("models.llm_model_config", None)
        from models.llm_model_config import LlmModelConfig

        return LlmModelConfig
    finally:
        # 恢复原始 sys.path,不影响后续测试
        sys.path[:] = old_path


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def test_dimensions_field_declared_nullable_int():
    """embedding 用途才填维度，chat/lite 恒为 NULL，因此必须可空。"""
    LlmModelConfig = _import_model()
    from tortoise import fields

    field = LlmModelConfig._meta.fields_map["dimensions"]
    assert isinstance(field, fields.IntField), "dimensions 必须是整型列"
    assert field.null is True, "dimensions 必须可空——chat/lite 配置不填维度"


def test_dimensions_has_no_default_value():
    """给 dimensions 设默认值会让 chat 配置也带上一个假维度，
    进而让 ReMe 的 BaseAsEmbedding.dimensions 拿到错误值（SPEC §3.5）。"""
    LlmModelConfig = _import_model()

    field = LlmModelConfig._meta.fields_map["dimensions"]
    assert field.default is None


def test_purpose_description_mentions_embedding():
    """purpose 是 CharField 取值扩展，字段注释是唯一的取值文档。"""
    LlmModelConfig = _import_model()

    description = LlmModelConfig._meta.fields_map["purpose"].description
    assert "embedding" in description, (
        f"purpose 注释未记录 embedding 取值: {description}"
    )
    for existing in ("chat", "lite"):
        assert existing in description, f"purpose 注释丢失既有取值 {existing}"


def test_purpose_description_does_not_promise_stage_three_values():
    """knowledge_chat / knowledge_embedding 属阶段三（契约 §0），
    提前写进注释会让实施者以为本次要支持。"""
    description = _src()
    assert "knowledge_chat" not in description
    assert "knowledge_embedding" not in description


def test_json_payload_exposes_dimensions():
    """前端模型配置页要能回显维度（P7 消费）。"""
    LlmModelConfig = _import_model()

    row = LlmModelConfig(
        name="向量-v4",
        base_url="https://x/v1",
        api_key="sk-1",
        model_name="text-embedding-v4",
        purpose="embedding",
        enabled=True,
        dimensions=1024,
    )
    assert row.json["dimensions"] == 1024
    assert "remark" not in row.json


def test_json_payload_still_masks_api_key():
    """回归：新增字段不得改变既有脱敏行为（契约 §4.4）。"""
    LlmModelConfig = _import_model()

    row = LlmModelConfig(
        name="向量-v4",
        base_url="https://x/v1",
        api_key="sk-super-secret-value",
        model_name="text-embedding-v4",
        purpose="embedding",
        enabled=True,
        dimensions=1024,
    )
    assert "sk-super-secret-value" not in str(row.json)


def test_model_has_no_remark_field():
    """模型配置备注已从当前数据库结构删除，不应留在 ORM 元数据中。"""
    LlmModelConfig = _import_model()

    assert "remark" not in LlmModelConfig._meta.fields_map
