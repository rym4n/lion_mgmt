# coding=utf-8
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.error_code import ErrorCode  # noqa: E402
from libs.error_code.exception import CustomError  # noqa: E402
from services.digital_employee.skill_storage import resolve_skill_file, relative_skill_dir, tenant_skill_root  # noqa: E402
from services.digital_employee.skill_storage import is_binary_skill_asset, validate_skill_asset  # noqa: E402

TENANT = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"


def test_resolves_file_inside_skill_dir():
    path = resolve_skill_file(f"{TENANT}/db_backup_check", "SKILL.md", TENANT)
    assert path.name == "SKILL.md"
    assert str(tenant_skill_root(TENANT)) in str(path)


def test_officecli_binary_is_accepted_as_binary_skill_asset():
    assert is_binary_skill_asset("officecli-linux-x64")
    validate_skill_asset("officecli-linux-x64", 34 * 1024 * 1024)


@pytest.mark.parametrize(
    "relative_path",
    ["../../etc/passwd", "/etc/passwd", "scripts/../../../secret", "..", "./../x"],
)
def test_rejects_path_traversal(relative_path):
    with pytest.raises(CustomError) as exc_info:
        resolve_skill_file(f"{TENANT}/db_backup_check", relative_path, TENANT)
    assert exc_info.value.enum.code == ErrorCode.PARAM_ERROR.code


def test_rejects_cross_tenant_skill_dir():
    other = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(CustomError) as exc_info:
        resolve_skill_file(f"{other}/db_backup_check", "SKILL.md", TENANT)
    assert exc_info.value.enum.code == ErrorCode.FORBIDDEN.code


def test_rejects_absolute_skill_dir():
    with pytest.raises(CustomError) as exc_info:
        resolve_skill_file("/etc", "passwd", TENANT)
    assert exc_info.value.enum.code == ErrorCode.PARAM_ERROR.code


def test_relative_skill_dir_is_not_absolute():
    target = tenant_skill_root(TENANT) / "db_backup_check"
    assert not relative_skill_dir(target).startswith("/")


def test_parse_skill_md_returns_name_and_description(tmp_path):
    from services.digital_employee.skill_storage import parse_skill_md

    (tmp_path / "SKILL.md").write_text(
        "---\nname: db-backup-check\ndescription: 检查备份有效性。当出现备份告警时使用。\n---\n\n# 正文\n",
        encoding="utf-8",
    )
    name, description = parse_skill_md(tmp_path)
    assert name == "db-backup-check"
    assert description == "检查备份有效性。当出现备份告警时使用。"


@pytest.mark.parametrize(
    "content",
    ["---\nname: only-name\n---\n\n# 正文\n", "---\ndescription: only desc\n---\n\n# 正文\n", "# 无 frontmatter\n"],
)
def test_parse_skill_md_rejects_incomplete_frontmatter(tmp_path, content):
    from services.digital_employee.skill_storage import parse_skill_md

    (tmp_path / "SKILL.md").write_text(content, encoding="utf-8")
    with pytest.raises(CustomError) as exc_info:
        parse_skill_md(tmp_path)
    assert exc_info.value.enum.code == ErrorCode.PACKAGE_INVALID.code


def test_parse_skill_md_rejects_absent_file(tmp_path):
    from services.digital_employee.skill_storage import parse_skill_md

    with pytest.raises(CustomError) as exc_info:
        parse_skill_md(tmp_path)
    assert exc_info.value.enum.code == ErrorCode.PACKAGE_INVALID.code


def test_for_write_rejects_builtin_scope():
    from services.digital_employee.skill_storage import BUILTIN_SCOPE

    with pytest.raises(CustomError) as exc_info:
        resolve_skill_file(f"{BUILTIN_SCOPE}/log_triage", "SKILL.md", TENANT, for_write=True)
    assert exc_info.value.enum.code == ErrorCode.FORBIDDEN.code


def test_default_read_allows_builtin_scope():
    from services.digital_employee.skill_storage import BUILTIN_SCOPE

    path = resolve_skill_file(f"{BUILTIN_SCOPE}/log_triage", "SKILL.md", TENANT)
    assert path.name == "SKILL.md"
