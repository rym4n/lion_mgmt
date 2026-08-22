# coding=utf-8
"""113 迁移契约：从当前模型配置结构中物理删除 remark 列。"""

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "models"
    / "113_20260822_drop_llm_config_remark.py"
)


def _parts() -> tuple[str, str]:
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("async def upgrade")[1].split("async def downgrade")[0]
    downgrade = source.split("async def downgrade")[1]
    return upgrade, downgrade


def test_upgrade_drops_llm_config_remark_column():
    assert MIGRATION.exists()
    upgrade, _ = _parts()

    assert 'ALTER TABLE "llm_model_config" DROP COLUMN "remark"' in upgrade
    assert "RUN_IN_TRANSACTION = True" in MIGRATION.read_text(encoding="utf-8")


def test_downgrade_restores_nullable_remark_column_and_comment():
    _, downgrade = _parts()

    assert 'ADD COLUMN "remark" VARCHAR(512)' in downgrade
    assert "NOT NULL" not in downgrade
    assert 'COMMENT ON COLUMN "llm_model_config"."remark"' in downgrade
