# coding=utf-8
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VIEWS_PATH = ROOT / "services/digital_employee/views.py"


def _source(name: str) -> str:
    tree = ast.parse(VIEWS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_file_endpoints_use_path_validation():
    for name in ("skill_file_read", "skill_file_write", "skill_file_upload", "skill_files"):
        source = _source(name)
        assert "resolve_skill_file" in source or "tenant_skill_root" in source, name


def test_read_endpoint_allows_binary_asset_limit():
    source = _source("skill_file_read")
    assert "is_binary_skill_asset" in source
    assert "SKILL_BINARY_MAX_BYTES" in source


def test_legacy_officecli_executables_are_hidden_from_skill_files():
    assert "OFFICECLI_BINARY_NAMES" in _source("skill_files")
    assert "OFFICECLI_BINARY_NAMES" in _source("skill_file_read")
    write = _source("skill_file_write")
    assert write.index("OFFICECLI_BINARY_NAMES") < write.index("clone_builtin_skill_to_tenant")


def test_write_endpoint_requires_update_permission():
    source = _source("skill_file_write")
    assert "digital_employee_skill:u" in source or "_require_save_permission" in source


def test_write_endpoint_copies_builtin_before_write():
    source = _source("skill_file_write")
    assert "is_builtin" in source


def test_upload_endpoint_accepts_binary_assets_with_bounded_storage():
    source = _source("skill_file_upload")
    assert "digital_employee_skill:u" in source
    assert "request.files" in source
    assert "write_bytes" in source
    assert "validate_skill_asset" in source
    assert "skill_storage_usage" in source
    assert "is_binary_skill_asset" in source
    assert source.index("validate_skill_asset") < source.index("clone_builtin_skill_to_tenant")


def test_skill_save_derives_skill_dir_server_side():
    """P12: skill_dir 不再信任客户端输入，改为服务端按约定派生 <tenant_id>/<skill_key>。"""
    source = _source("skill_save")
    assert "data.get('skill_dir')" not in source
    assert "relative_skill_dir" in source
    assert "tenant_skill_root" in source
