# coding=utf-8
"""技能文件存储根与路径安全。文件成为权威载体后，这里是唯一的写入闸门：所有来自 zip 与页面的路径
都必须经 resolve_skill_file 校验，双层确认（技能目录内 + 租户根内）。
"""
from __future__ import annotations

import os
from pathlib import Path

import frontmatter

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import DIGITAL_EMPLOYEE_RESOURCES

BUILTIN_SCOPE = "__builtin__"


def skill_storage_root() -> Path:
    override = os.environ.get("RY_SKILL_ROOT")
    if override:
        return Path(override).resolve()
    return (Path(DIGITAL_EMPLOYEE_RESOURCES) / "skills").resolve()


def tenant_skill_root(tenant_id: str) -> Path:
    scope = (tenant_id or "").strip()
    if not scope or "/" in scope or "\\" in scope or scope.startswith("."):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return (skill_storage_root() / scope).resolve()


def relative_skill_dir(root: Path) -> str:
    try:
        return root.resolve().relative_to(skill_storage_root()).as_posix()
    except ValueError as exc:
        raise CustomError(ErrorCode.PARAM_ERROR) from exc


def parse_skill_md(root: Path) -> tuple[str, str]:
    """解析技能目录下的 SKILL.md，返回 (name, description)。规范要求 frontmatter 必须有 name 与 description，缺一即非法技能
    （AgentScope 的 LocalSkillLoader 也会静默跳过这类目录）。
    """
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    try:
        post = frontmatter.loads(skill_md.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CustomError(ErrorCode.PACKAGE_INVALID) from exc
    name = str(post.get("name") or "").strip()
    description = str(post.get("description") or "").strip()
    if not name or not description:
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    return name, description


def resolve_skill_file(skill_dir: str, relative_path: str, tenant_id: str, *, for_write: bool = False) -> Path:
    """把 (技能目录, 目录内相对路径) 解析为绝对路径，越界即拒。双层校验：先确认落在该技能目录内，再确认该技能目录落在允许的根内
    （租户自己的根，或内置根——内置只读，由调用方另行拦写）。
    `for_write=True` 时收紧到仅租户根，防止写操作误落到共享的内置目录。
    """
    if not skill_dir or Path(skill_dir).is_absolute() or not relative_path:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if Path(relative_path).is_absolute():
        raise CustomError(ErrorCode.PARAM_ERROR)

    storage_root = skill_storage_root()
    skill_root = (storage_root / skill_dir).resolve()
    target = (skill_root / relative_path).resolve()

    if skill_root != target and skill_root not in target.parents:
        raise CustomError(ErrorCode.PARAM_ERROR)

    allowed_roots = [tenant_skill_root(tenant_id)]
    if not for_write:
        allowed_roots.append((storage_root / BUILTIN_SCOPE).resolve())
    if not any(root == skill_root or root in skill_root.parents for root in allowed_roots):
        raise CustomError(ErrorCode.FORBIDDEN)
    return target
