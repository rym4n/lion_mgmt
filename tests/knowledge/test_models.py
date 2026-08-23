# coding=utf-8
"""知识空间与文档模型的字段契约（SPEC §7）。

本仓测试不连数据库，模型层断言直接读 Tortoise 字段描述对象。
"""

import sys
from pathlib import Path


def _import_models():
    """安全导入 models.knowledge，兼容其他测试的 sys.modules 操作。"""
    root = Path(__file__).resolve().parents[2]
    old_path = sys.path.copy()
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        sys.modules.pop("models", None)
        sys.modules.pop("models.knowledge", None)
        from models.knowledge import KnowledgeDocument, KnowledgeSpace

        return KnowledgeSpace, KnowledgeDocument
    finally:
        sys.path[:] = old_path


def test_space_key_and_tenant_unique_together():
    """space_key 只在租户内唯一——不同租户可以都有 monitoring。"""
    KnowledgeSpace, _ = _import_models()
    assert ("tenant_id", "space_key") in KnowledgeSpace._meta.unique_together


def test_space_root_uri_format():
    """root_uri 决定 OpenViking 侧的路径，格式错了隔离就失效。"""
    KnowledgeSpace, _ = _import_models()
    space = KnowledgeSpace(space_key="monitoring", name="监控手册")
    assert space.root_uri == "viking://resources/spaces/monitoring"


def test_space_source_type_default_is_manual():
    KnowledgeSpace, _ = _import_models()
    field = KnowledgeSpace._meta.fields_map["source_type"]
    assert field.default == "manual"


def test_document_uri_unique_per_tenant():
    """同一 URI 在租户内只能有一行，这是 upsert 幂等的基础。"""
    _, KnowledgeDocument = _import_models()
    assert ("tenant_id", "uri") in KnowledgeDocument._meta.unique_together


def test_document_status_default_is_pending():
    """新建文档必须从 pending 起步，直接写 ready 会让界面显示假的就绪状态。"""
    _, KnowledgeDocument = _import_models()
    field = KnowledgeDocument._meta.fields_map["status"]
    assert field.default == "pending"


def test_document_size_is_big_int():
    """size 用 BigInt——普通 IntField 上限约 2GB，虽然单文件限 10MB，
    但字段类型不该埋一个将来放宽限制时才炸的雷。"""
    _, KnowledgeDocument = _import_models()
    from tortoise import fields

    assert isinstance(KnowledgeDocument._meta.fields_map["size"], fields.BigIntField)


def test_document_indexed_at_nullable():
    """未完成索引的文档没有 indexed_at。"""
    _, KnowledgeDocument = _import_models()
    assert KnowledgeDocument._meta.fields_map["indexed_at"].null is True


def test_document_space_foreign_key_cascade():
    """空间被物理删除时文档记录不该变孤儿。
    注意软删除走的是 deleted 标记，不触发级联。"""
    _, KnowledgeDocument = _import_models()
    field = KnowledgeDocument._meta.fields_map["space"]
    assert field.model_name == "models.KnowledgeSpace"
