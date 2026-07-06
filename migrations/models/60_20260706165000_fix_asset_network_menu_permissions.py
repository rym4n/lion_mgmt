import importlib

from tortoise import BaseDBAsyncClient

split_menus = importlib.import_module(
    "migrations.models.59_20260706150000_split_asset_network_management_menus"
)

RUN_IN_TRANSACTION = True


ADMIN_BACKFILL_PERMISSIONS = (
    split_menus.ASSET_MODEL_PERMISSIONS[:-1]
    + ','
    + split_menus.ASSET_CATALOG_PERMISSIONS[1:-1]
    + ','
    + split_menus.NETWORK_TOPOLOGY_PERMISSIONS[1:-1]
    + ','
    + split_menus.ACCESS_CONTROL_PERMISSIONS[1:]
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        DELETE FROM "permissions_menu"
        WHERE "model" IN ('network-topology', 'topology', 'supply-chain-review')
           OR "description" = '安全管理'
           OR ("url" -> 'CN' ->> 'path') IN ('/network-topology', '/topology', '/security-management');

        UPDATE "permissions_menu"
        SET
            "url" = CASE "model"
                WHEN 'asset-management-models' THEN '{split_menus.ASSET_MODEL_URL}'::jsonb
                WHEN 'asset-management-catalog' THEN '{split_menus.ASSET_CATALOG_URL}'::jsonb
                WHEN 'network-management-topology' THEN '{split_menus.NETWORK_TOPOLOGY_URL}'::jsonb
                WHEN 'network-management-access-control' THEN '{split_menus.ACCESS_CONTROL_URL}'::jsonb
                ELSE "url"
            END,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" IN (
            'asset-management-models',
            'asset-management-catalog',
            'network-management-topology',
            'network-management-access-control'
        );

        UPDATE "role"
        SET "permissions" = (
            SELECT COALESCE(jsonb_agg(DISTINCT permission_value), '[]'::jsonb)
            FROM (
                SELECT permission_value
                FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS existing(permission_value)
                UNION
                SELECT permission_value
                FROM jsonb_array_elements_text('{ADMIN_BACKFILL_PERMISSIONS}'::jsonb) AS added(permission_value)
            ) AS merged
        )
        WHERE "permissions" IS NOT NULL
          AND "permissions" <> '[]'::jsonb
          AND NOT ("permissions" ? '*:*')
          AND "name" IN ('租户管理员', '空间管理员', '系统管理员');
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
