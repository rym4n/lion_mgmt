# coding=utf-8
"""112 迁移的契约测试（补偿迁移：收敛策略从 UPDATE 修正为 DELETE）。

这是对 105 迁移的补偿——105 采用了 UPDATE 策略，但正确的策略应该是
DELETE（SPEC §5.6）。本测试验证补偿迁移的完整性。
"""
from pathlib import Path

from models.command_center import CommandAgent

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_112 = ROOT / "migrations" / "models" / "112_20260819_llm_config_convergence_correction.py"
COMMAND_AGENT_TABLE = CommandAgent._meta.db_table


def test_migration_112_exists():
    """补偿迁移文件必须存在。"""
    assert MIGRATION_112.exists(), "112 迁移文件不存在"


def test_creates_backup_schema():
    """必须创建 migration_backup schema。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    upgrade = sql.split("async def upgrade")[1].split("async def downgrade")[0]
    assert "CREATE SCHEMA IF NOT EXISTS migration_backup" in upgrade


def test_backs_up_configs_before_delete():
    """DELETE 前必须备份所有非 default 租户的配置。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    upgrade = sql.split("async def upgrade")[1].split("async def downgrade")[0]

    assert "llm_model_config_backup_" in upgrade
    assert "SELECT * FROM llm_model_config" in upgrade
    assert "WHERE tenant_id !=" in upgrade

    # 备份必须在 DELETE 之前
    backup_idx = upgrade.index("llm_model_config_backup_")
    delete_idx = upgrade.index("DELETE FROM llm_model_config")
    assert backup_idx < delete_idx, "备份必须在 DELETE 之前"


def test_backs_up_employee_bindings():
    """必须从 CommandAgent 模型对应的表备份员工绑定关系。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    upgrade = sql.split("async def upgrade")[1].split("async def downgrade")[0]

    assert "employee_llm_binding_backup_" in upgrade
    assert f'FROM "{COMMAND_AGENT_TABLE}" ca' in upgrade
    assert "llm_config_id" in upgrade


def test_deletes_non_default_configs():
    """必须 DELETE 所有非 default 租户的配置。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    upgrade = sql.split("async def upgrade")[1].split("async def downgrade")[0]

    assert "DELETE FROM llm_model_config" in upgrade
    assert "WHERE tenant_id !=" in upgrade
    # 不能是 UPDATE，必须是 DELETE
    assert "UPDATE llm_model_config" not in upgrade or \
           upgrade.index("DELETE FROM llm_model_config") < upgrade.find("UPDATE llm_model_config")


def test_downgrade_is_fail_closed():
    """回滚必须是 fail-closed：校验失败则拒绝执行。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]

    # 必须包含校验逻辑
    assert "RAISE EXCEPTION" in downgrade, "回滚缺少 fail-closed 校验"
    assert "备份表" in downgrade or "backup" in downgrade.lower()


def test_downgrade_verifies_backup_existence():
    """回滚必须校验备份表存在。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]

    assert "information_schema.tables" in downgrade
    assert "llm_model_config_backup_" in downgrade
    assert "employee_llm_binding_backup_" in downgrade


def test_downgrade_restores_configs():
    """回滚必须恢复非 default 租户的配置。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]

    assert "INSERT INTO llm_model_config" in downgrade
    assert "FROM migration_backup.llm_model_config_backup_" in downgrade


def test_downgrade_restores_employee_bindings():
    """回滚必须通过 CommandAgent 模型对应的表恢复员工绑定。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]

    assert f'UPDATE "{COMMAND_AGENT_TABLE}" ca' in downgrade
    assert "llm_config_id" in downgrade
    assert "employee_llm_binding_backup_" in downgrade


def test_downgrade_verifies_restore_completeness():
    """回滚必须验证恢复的完整性（备份数 == 恢复数）。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]

    # 必须有计数校验
    assert "COUNT(*)" in downgrade
    assert "backup_count" in downgrade or "restored_count" in downgrade


def test_upgrade_runs_in_transaction():
    """迁移必须在事务中运行。"""
    src = MIGRATION_112.read_text(encoding="utf-8")
    assert "RUN_IN_TRANSACTION = True" in src


def test_uses_correct_default_tenant_id():
    """必须使用正确的 default 租户 ID。"""
    src = MIGRATION_112.read_text(encoding="utf-8")
    assert "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18" in src


def test_backup_table_names_are_timestamped():
    """备份表名必须带时间戳后缀（防止重复运行冲突）。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    upgrade = sql.split("async def upgrade")[1].split("async def downgrade")[0]

    # 检查是否使用了日期后缀变量
    assert "backup_suffix" in upgrade or "datetime" in upgrade or "strftime" in upgrade


def test_preserves_deleted_flag_semantic():
    """备份和恢复时必须保留 deleted 标志语义。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    upgrade = sql.split("async def upgrade")[1].split("async def downgrade")[0]

    # 备份时应该只备份 deleted=FALSE 的（已删除的不需要备份）
    assert "deleted = FALSE" in upgrade or "deleted = false" in upgrade.lower()


def test_handles_uuid_conflicts_gracefully():
    """恢复时必须处理 ID 冲突（ON CONFLICT）。"""
    sql = MIGRATION_112.read_text(encoding="utf-8")
    downgrade = sql.split("async def downgrade")[1]

    assert "ON CONFLICT" in downgrade or "on conflict" in downgrade.lower()


def test_migration_is_documented():
    """迁移必须有清晰的文档字符串说明其目的。"""
    src = MIGRATION_112.read_text(encoding="utf-8")

    # 必须说明这是补偿迁移
    assert "补偿" in src or "修正" in src or "correction" in src.lower()
    assert "105" in src  # 必须提到它修正的是 105
