# coding=utf-8
import io
import zipfile
from typing import Any

import yaml


class PackageFormatError(ValueError):
    pass


class UnsupportedPackageTypeError(PackageFormatError):
    pass


def inspect_package(file_bytes: bytes) -> tuple[str, dict[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            with archive.open("package.yaml") as package_file:
                package = yaml.safe_load(package_file.read().decode("utf-8")) or {}
    except (KeyError, zipfile.BadZipFile, yaml.YAMLError) as exc:
        raise PackageFormatError("invalid package") from exc
    package_type = package.get("type")
    if package_type not in {"role", "skill", "bundle"}:
        raise UnsupportedPackageTypeError("unsupported package type")
    return package_type, package
