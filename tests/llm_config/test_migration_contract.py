# coding=utf-8
"""迁移契约测试：确保建表 SQL 与模型定义、菜单注入保持一致。"""
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "models" / "100_20260805180000_llm_model_config.py"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists():
    assert MIGRATION.exists(), f"迁移文件缺失: {MIGRATION}"


def test_creates_table_with_all_model_fields():
    sql = _sql()
    assert 'CREATE TABLE IF NOT EXISTS "llm_model_config"' in sql
    for column in ("name", "base_url", "api_key", "model_name", "purpose", "enabled", "remark"):
        assert f'"{column}"' in sql, f"建表 SQL 缺少字段 {column}"
    # BaseModel 继承字段
    for column in ("id", "ctime", "utime", "deleted", "tenant_id"):
        assert f'"{column}"' in sql, f"建表 SQL 缺少基类字段 {column}"


def test_name_unique_per_tenant():
    assert 'UNIQUE ("tenant_id", "name")' in _sql()


def test_api_key_column_is_wide_enough_for_ciphertext():
    # 密文比明文长，SPEC 定为 2048
    assert '"api_key" VARCHAR(2048)' in _sql()


def test_adds_llm_config_fk_to_command_agent():
    sql = _sql()
    assert 'ALTER TABLE "command_center_agent" ADD COLUMN "llm_config_id"' in sql
    assert "ON DELETE SET NULL" in sql


def test_inserts_model_config_menu_row():
    sql = _sql()
    assert 'INSERT INTO "public"."permissions_menu"' in sql
    assert "'ai_config'" in sql, "菜单行的 model 字段应为 ai_config（与权限码前缀一致）"
    assert "'ai_bp'" in sql, "bp 字段必须是 ai_bp，否则菜单在 License 过滤时永久不显示"
    # 三语言标题
    assert "模型配置" in sql
    assert "模型配置" in sql  # HK 与 CN 同字形
    assert "Model Config" in sql
    # 路由与组件路径
    assert "/System/ModelConfig/index" in sql
    assert "views/System/ModelConfig/index.vue" in sql
    # 幂等
    assert "ON CONFLICT" in sql


def test_downgrade_reverses_everything():
    sql = _sql()
    downgrade = sql.split("async def downgrade")[1]
    assert 'DROP TABLE IF EXISTS "llm_model_config"' in downgrade
    assert 'DROP COLUMN IF EXISTS "llm_config_id"' in downgrade
    assert "DELETE FROM" in downgrade and "permissions_menu" in downgrade
