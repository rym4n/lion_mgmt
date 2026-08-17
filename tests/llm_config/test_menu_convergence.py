# coding=utf-8
"""菜单入口收敛（契约 D8 / SPEC §5.6）。

「模型配置」菜单**不由 105 迁移创建**——它已存在于 permissions_menu
（model='ai_config'，bp='ai_bp'），由既有 seed 建立。本系统没有 menu_config 表。

「仅 default 租户可见」由 services/license/views.py 的 hidden_menus 在
下发菜单时过滤承担，不靠迁移改数据。

这组测试锁死上述两点，防止再次出现「迁移里 INSERT 一张不存在的表」——
那次的代价是 aerich upgrade 失败、容器起不来、进崩溃重启循环。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_105 = (
    ROOT / "migrations" / "models" / "105_20260817_llm_config_dimensions_and_convergence.py"
)
LICENSE_VIEWS = ROOT / "services" / "license" / "views.py"


def test_migration_does_not_touch_any_menu_table():
    """105 迁移不得碰菜单表。

    真实表是 permissions_menu，列结构是 model/description/Permissions/url/bp，
    与「name/path/icon/sort/parent_id」完全不同。迁移里任何菜单写操作都是
    在猜 schema，猜错整个服务就起不来。
    """
    sql = MIGRATION_105.read_text(encoding="utf-8")
    # 只看非注释行：文件里有一条注释说明「本系统没有 menu_config 表」，
    # 那是要保留的解释，不是引用。
    statements = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    assert "menu_config" not in statements, "menu_config 表不存在，不得引用"
    for forbidden in ("INSERT INTO permissions_menu", "DELETE FROM permissions_menu"):
        assert forbidden not in statements, f"迁移不得对菜单表做写操作：{forbidden}"


def test_migration_only_does_column_and_tenant_convergence():
    """105 迁移的职责边界：加 dimensions 列 + 租户收敛，仅此两项。"""
    sql = MIGRATION_105.read_text(encoding="utf-8")
    assert 'ALTER TABLE "llm_model_config" ADD COLUMN "dimensions" INT' in sql
    assert "SET tenant_id = " in sql
    assert "llm_model_config" in sql


def test_downgrade_only_drops_the_column():
    """回滚只摘列。租户收敛不可逆——原租户归属没留快照，不能假装能还原。"""
    sql = MIGRATION_105.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]
    assert 'DROP COLUMN "dimensions"' in downgrade
    assert "DELETE" not in downgrade.upper(), "回滚不得删除任何数据行"


def test_tenant_visibility_is_enforced_by_license_menu_filter():
    """租户可见性由 hidden_menus 过滤承担（契约 D8），不由迁移承担。"""
    src = LICENSE_VIEWS.read_text(encoding="utf-8")
    assert "hidden_menus" in src, "菜单下发过滤机制缺失"
    assert "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18" in src, "缺少 default 租户判定"
