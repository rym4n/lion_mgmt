# coding=utf-8
from __future__ import annotations

import asyncio
import os

from sanic.log import logger

from .package_importer import import_builtin_packages


def builtin_package_bootstrap_enabled() -> bool:
    value = os.environ.get("RY_DIGITAL_EMPLOYEE_BOOTSTRAP_PACKAGES", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def bootstrap_builtin_packages() -> dict:
    result = await import_builtin_packages(operator_id="system")
    return result.json


def main() -> int:
    if not builtin_package_bootstrap_enabled():
        logger.info("digital employee builtin package bootstrap skipped")
        return 0
    result = asyncio.run(bootstrap_builtin_packages())
    logger.info("digital employee builtin package bootstrap completed: %s", result)
    return 0
