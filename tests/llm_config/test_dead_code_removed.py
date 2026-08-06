# coding=utf-8
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_old_ai_config_routes_removed():
    src = (ROOT / "services" / "workflow" / "views.py").read_text(encoding="utf-8")
    for handler in ("create_ai_config", "update_ai_config", "delete_ai_config", "list_ai_config"):
        assert f"async def {handler}" not in src, f"死代码 {handler} 未删除"
    assert '@workflow_bp.post("/ai-config/' not in src
    assert 'module="ai_service"' not in src, "ai_service Config 读写应一并清除"


def test_license_gate_helper_kept():
    """check_ai_permission 被新模块复用，不能一起删掉。"""
    src = (ROOT / "services" / "workflow" / "views.py").read_text(encoding="utf-8")
    assert "def check_ai_permission" in src
