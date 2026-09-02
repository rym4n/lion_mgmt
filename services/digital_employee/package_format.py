# coding=utf-8
import io
import zipfile
from typing import Any

import yaml


class PackageFormatError(ValueError):
    pass


class UnsupportedPackageTypeError(PackageFormatError):
    pass


def _parse_skill_md_frontmatter(content: str) -> dict[str, Any]:
    """从 SKILL.md 内容中解析 YAML frontmatter（``---`` 包裹的部分）。"""
    content = content.strip()
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end < 0:
        return {}
    try:
        data = yaml.safe_load(content[3:end])
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def inspect_package(file_bytes: bytes) -> tuple[str, dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise PackageFormatError("invalid package") from exc

    with archive:
        # 优先使用标准的 package.yaml
        if "package.yaml" in archive.namelist():
            try:
                with archive.open("package.yaml") as package_file:
                    package = yaml.safe_load(package_file.read().decode("utf-8")) or {}
            except yaml.YAMLError as exc:
                raise PackageFormatError("invalid package") from exc
            package_type = package.get("type")
            if package_type not in {"role", "skill", "bundle"}:
                raise UnsupportedPackageTypeError("unsupported package type")
            return package_type, package

        # 没有 package.yaml 但有 SKILL.md — 视为裸技能包（第三方常见格式）
        if "SKILL.md" in archive.namelist():
            try:
                content = archive.read("SKILL.md").decode("utf-8")
            except Exception as exc:
                raise PackageFormatError("invalid package") from exc
            meta = _parse_skill_md_frontmatter(content)
            name = str(meta.get("name") or "").strip()
            description = str(meta.get("description") or "").strip()
            if name and description:
                return "skill", {"type": "skill", "skill_key": name}

        raise PackageFormatError("invalid package")
