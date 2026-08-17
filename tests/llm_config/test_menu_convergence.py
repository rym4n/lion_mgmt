# coding=utf-8
"""菜单入口收敛测试（SPEC §5.6 / Task 11）。

105 迁移已插入模型配置菜单行，本测试验证插入契约。
"""
from pathlib import Path

MIGRATION_105 = Path(__file__).resolve().parents[2] / "migrations" / "models" / "105_20260817_llm_config_dimensions_and_convergence.py"


def test_migration_inserts_menu_row():
    """105 迁移的 upgrade 必须插入菜单行（Task 11）。"""
    sql = MIGRATION_105.read_text(encoding="utf-8")
    assert "INSERT INTO menu_config" in sql, "迁移未插入菜单行"
    assert "模型配置" in sql, "菜单名称缺失"
    assert "/settings/llm-config" in sql, "菜单路径缺失"


def test_menu_row_idempotent():
    """菜单插入必须是幂等的（WHERE NOT EXISTS），避免重复执行失败。"""
    sql = MIGRATION_105.read_text(encoding="utf-8")
    assert "WHERE NOT EXISTS" in sql, "菜单插入不是幂等的"
    assert "SELECT 1 FROM menu_config" in sql, "未检查菜单行是否已存在"


def test_menu_row_bound_to_default_tenant():
    """菜单行必须属于 default 租户（SPEC §5.6）。"""
    sql = MIGRATION_105.read_text(encoding="utf-8")
    # f-string 插值后是双引号
    assert "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18" in sql, "菜单未绑定到 DEFAULT_TENANT_ID"


def test_downgrade_removes_menu_row():
    """回滚必须删除菜单行，恢复到迁移前状态。"""
    sql = MIGRATION_105.read_text(encoding="utf-8")
    assert "DELETE FROM menu_config WHERE path = '/settings/llm-config'" in sql, \
        "回滚未删除菜单行"
