#!/usr/bin/env python3
# coding=utf-8
"""Build digital employee role, skill and bundle zip packages."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
AGENT_TEMPLATE_ROOT = ROOT / "agent-template"
SKILL_TEMPLATE_ROOT = ROOT / "skill-template"
DIST_ROOT = ROOT / "dist"


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def write_dir_archive(source: Path, target: Path, prefix: str = "") -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(source.rglob("*")):
            if item.is_dir():
                continue
            relative = item.relative_to(source).as_posix()
            archive_name = f"{prefix.rstrip('/')}/{relative}" if prefix else relative
            archive.write(item, archive_name)


def build_role(role_dir: Path, dist_root: Path = DIST_ROOT) -> Path:
    package = read_yaml(role_dir / "package.yaml")
    role_key = package["role_key"]
    version = package.get("version", 1)
    target = dist_root / "agents" / f"{role_key}-v{version}.role.zip"
    write_dir_archive(role_dir, target)
    return target


def build_skill(skill_dir: Path, dist_root: Path = DIST_ROOT) -> Path:
    package = read_yaml(skill_dir / "package.yaml")
    skill_key = package["skill_key"]
    version = package.get("version", 1)
    target = dist_root / "skills" / f"{skill_key}-v{version}.skill.zip"
    write_dir_archive(skill_dir, target)
    return target


def build_bundle(role_dir: Path, output_root: Path | None = None) -> Path:
    package = read_yaml(role_dir / "package.yaml")
    role_yaml = read_yaml(role_dir / "role.yaml")
    role_key = package["role_key"]
    version = package.get("version", 1)
    bundle_key = package.get("bundle_key") or f"aioe-digital-employees-builtin-{role_key}"
    default_skills = package.get("default_skill_keys") or role_yaml.get("default_skills") or []
    target_root = output_root or DIST_ROOT / "bundles"
    target = target_root / f"{bundle_key}-v{version}.bundle.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "package.yaml",
            yaml.safe_dump(
                {
                    "type": "bundle",
                    "bundle_key": bundle_key,
                    "role_key": role_key,
                    "version": version,
                    "vendor": package.get("vendor", "aioe"),
                },
                allow_unicode=True,
                sort_keys=False,
            ),
        )
        for item in sorted(role_dir.rglob("*")):
            if item.is_file():
                archive.write(item, f"roles/{role_key}/{item.relative_to(role_dir).as_posix()}")
        for skill_key in default_skills:
            skill_dir = SKILL_TEMPLATE_ROOT / str(skill_key)
            if not skill_dir.exists():
                raise FileNotFoundError(f"missing default skill template: {skill_key}")
            for item in sorted(skill_dir.rglob("*")):
                if item.is_file():
                    archive.write(item, f"skills/{skill_key}/{item.relative_to(skill_dir).as_posix()}")
    return target


def _clean_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def build_all(clean: bool = False, bundles_only: bool = False, bundle_output: Path | None = None) -> list[Path]:
    if clean:
        if bundles_only and bundle_output:
            _clean_path(bundle_output)
        elif bundles_only:
            _clean_path(DIST_ROOT / "bundles")
        else:
            _clean_path(DIST_ROOT)
    built: list[Path] = []
    role_dirs = sorted(path for path in AGENT_TEMPLATE_ROOT.iterdir() if path.is_dir())
    if not bundles_only:
        for role_dir in role_dirs:
            built.append(build_role(role_dir))
        for skill_dir in sorted(path for path in SKILL_TEMPLATE_ROOT.iterdir() if path.is_dir()):
            built.append(build_skill(skill_dir))
    for role_dir in role_dirs:
        built.append(build_bundle(role_dir, output_root=bundle_output))
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description="Build digital employee package zips.")
    parser.add_argument("--clean", action="store_true", help="Remove dist before building")
    parser.add_argument("--bundles-only", action="store_true", help="Build only role bundles")
    parser.add_argument("--bundle-output", type=Path, help="Directory for generated bundle zips")
    args = parser.parse_args()
    for package_path in build_all(clean=args.clean, bundles_only=args.bundles_only, bundle_output=args.bundle_output):
        try:
            print(package_path.relative_to(ROOT))
        except ValueError:
            print(package_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
