# coding=utf-8
import asyncio
import traceback
from asyncio.base_events import BaseEventLoop

from sanic import Sanic
from sanic.log import logger

from .base import BaseListener


class AssetCatalogListener(BaseListener):
    """Provision the formal builtin asset catalog for every active tenant."""

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        async def reconcile_catalogs() -> None:
            try:
                from services.asset_management.builtin_catalog import (
                    reconcile_all_tenant_builtin_catalogs,
                )

                result = await reconcile_all_tenant_builtin_catalogs()
                if result["failed"]:
                    logger.warning(
                        "内置资产目录初始化部分失败: "
                        f"total={result['total']} changed={result['changed']} "
                        f"unchanged={result['unchanged']} failed={len(result['failed'])}"
                    )
                else:
                    logger.info(
                        "内置资产目录初始化完成: "
                        f"total={result['total']} changed={result['changed']} "
                        f"unchanged={result['unchanged']}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"内置资产目录初始化失败: {exc} | {traceback.format_exc()}"
                )

        app.add_task(
            reconcile_catalogs(),
            name="asset_builtin_catalog_reconcile",
        )

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        try:
            await app.cancel_task("asset_builtin_catalog_reconcile")
        except Exception:
            pass
