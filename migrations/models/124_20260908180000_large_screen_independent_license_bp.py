from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# 大屏管理原先与仪表盘共用 dashboard_bp 作为 license module key，导致：
#   1. license 中只要有「仪表盘」，大屏菜单就会跟着放行（_menu_is_licensed 按 bp 匹配）
#   2. 大屏路由挂在无条件注册的 dashboard_bp 上，后端完全没有 license 门禁
# 改为独立的 large_screen_bp，使其可被单独授权。


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "bp" = 'large_screen_bp',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'large_screen'
          AND "bp" = 'dashboard_bp';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "bp" = 'dashboard_bp',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'large_screen'
          AND "bp" = 'large_screen_bp';
    """
