from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

ASSET_MODEL_PERMISSIONS = '["supply_chain_review:model:c","supply_chain_review:model:r","supply_chain_review:model:u","supply_chain_review:model:d","supply_chain_review:field:c","supply_chain_review:field:r","supply_chain_review:field:u","supply_chain_review:field:d","supply_chain_review:relation:c","supply_chain_review:relation:r","supply_chain_review:relation:u","supply_chain_review:relation:d"]'
ASSET_CATALOG_PERMISSIONS = '["supply_chain_review:ci:c","supply_chain_review:ci:r","supply_chain_review:ci:u","supply_chain_review:ci:d"]'
NETWORK_TOPOLOGY_PERMISSIONS = '["network_topology:r","network_topology:w"]'
ACCESS_CONTROL_PERMISSIONS = '["supply_chain_review:policy:c","supply_chain_review:policy:r","supply_chain_review:policy:u","supply_chain_review:policy:d"]'

ASSET_MODEL_URL = '{"CN":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"资产管理"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:node-tree","title":"资产模型"},"name":"AssetManagementModels","path":"/asset-management/models","component":"views/securityManagement/index.vue"}]},"TW":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"資產管理"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:node-tree","title":"資產模型"},"name":"AssetManagementModels","path":"/asset-management/models","component":"views/securityManagement/index.vue"}]},"HK":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"資產管理"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:node-tree","title":"資產模型"},"name":"AssetManagementModels","path":"/asset-management/models","component":"views/securityManagement/index.vue"}]},"EN":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"Asset Management"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:node-tree","title":"Asset Models"},"name":"AssetManagementModels","path":"/asset-management/models","component":"views/securityManagement/index.vue"}]}}'
ASSET_CATALOG_URL = '{"CN":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"资产管理"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:archive-drawer-line","title":"资产目录"},"name":"AssetManagementCatalog","path":"/asset-management/catalog","component":"views/securityManagement/index.vue"}]},"TW":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"資產管理"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:archive-drawer-line","title":"資產目錄"},"name":"AssetManagementCatalog","path":"/asset-management/catalog","component":"views/securityManagement/index.vue"}]},"HK":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"資產管理"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:archive-drawer-line","title":"資產目錄"},"name":"AssetManagementCatalog","path":"/asset-management/catalog","component":"views/securityManagement/index.vue"}]},"EN":{"meta":{"icon":"ri:database-2-line","rank":16,"title":"Asset Management"},"path":"/asset-management","redirect":"/asset-management/models","component":"layout/index.vue","children":[{"meta":{"icon":"ri:archive-drawer-line","title":"Asset Catalog"},"name":"AssetManagementCatalog","path":"/asset-management/catalog","component":"views/securityManagement/index.vue"}]}}'
NETWORK_TOPOLOGY_URL = '{"CN":{"meta":{"icon":"ri:router-line","rank":17,"title":"网络管理"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:mind-map","title":"网络拓扑"},"name":"NetworkManagementTopology","path":"/network-management/topology","component":"views/topology/index.vue"}]},"TW":{"meta":{"icon":"ri:router-line","rank":17,"title":"網絡管理"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:mind-map","title":"網絡拓撲"},"name":"NetworkManagementTopology","path":"/network-management/topology","component":"views/topology/index.vue"}]},"HK":{"meta":{"icon":"ri:router-line","rank":17,"title":"網絡管理"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:mind-map","title":"網絡拓撲"},"name":"NetworkManagementTopology","path":"/network-management/topology","component":"views/topology/index.vue"}]},"EN":{"meta":{"icon":"ri:router-line","rank":17,"title":"Network Management"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:mind-map","title":"Network Topology"},"name":"NetworkManagementTopology","path":"/network-management/topology","component":"views/topology/index.vue"}]}}'
ACCESS_CONTROL_URL = '{"CN":{"meta":{"icon":"ri:router-line","rank":17,"title":"网络管理"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:shield-keyhole-line","title":"访问控制"},"name":"NetworkManagementAccessControl","path":"/network-management/access-control","component":"views/securityManagement/network.vue"}]},"TW":{"meta":{"icon":"ri:router-line","rank":17,"title":"網絡管理"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:shield-keyhole-line","title":"訪問控制"},"name":"NetworkManagementAccessControl","path":"/network-management/access-control","component":"views/securityManagement/network.vue"}]},"HK":{"meta":{"icon":"ri:router-line","rank":17,"title":"網絡管理"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:shield-keyhole-line","title":"訪問控制"},"name":"NetworkManagementAccessControl","path":"/network-management/access-control","component":"views/securityManagement/network.vue"}]},"EN":{"meta":{"icon":"ri:router-line","rank":17,"title":"Network Management"},"path":"/network-management","redirect":"/network-management/topology","component":"layout/index.vue","children":[{"meta":{"icon":"ri:shield-keyhole-line","title":"Access Control"},"name":"NetworkManagementAccessControl","path":"/network-management/access-control","component":"views/securityManagement/network.vue"}]}}'


def _insert_menu_sql(
    *,
    item_id: str,
    model: str,
    description: str,
    permissions: str,
    url: str,
    bp: str,
) -> str:
    return f"""
        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            '{item_id}',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            '{model}',
            '{description}',
            '{permissions}',
            '{url}',
            '{bp}'
        )
        ON CONFLICT ("model") DO UPDATE SET
            "description" = EXCLUDED."description",
            "Permissions" = EXCLUDED."Permissions",
            "url" = EXCLUDED."url",
            "bp" = EXCLUDED."bp",
            "deleted" = False,
            "utime" = CURRENT_TIMESTAMP;
    """


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        DELETE FROM "permissions_menu"
        WHERE "model" = 'supply-chain-review'
           OR "description" = '安全管理';

        {_insert_menu_sql(
            item_id="asset-management-models-permissions",
            model="asset-management-models",
            description="资产模型",
            permissions=ASSET_MODEL_PERMISSIONS,
            url=ASSET_MODEL_URL,
            bp="asset_management_bp",
        )}

        {_insert_menu_sql(
            item_id="asset-management-catalog-permissions",
            model="asset-management-catalog",
            description="资产目录",
            permissions=ASSET_CATALOG_PERMISSIONS,
            url=ASSET_CATALOG_URL,
            bp="asset_management_bp",
        )}

        {_insert_menu_sql(
            item_id="network-management-topology-permissions",
            model="network-management-topology",
            description="网络拓扑",
            permissions=NETWORK_TOPOLOGY_PERMISSIONS,
            url=NETWORK_TOPOLOGY_URL,
            bp="topology_bp",
        )}

        {_insert_menu_sql(
            item_id="network-management-access-control-permissions",
            model="network-management-access-control",
            description="访问控制",
            permissions=ACCESS_CONTROL_PERMISSIONS,
            url=ACCESS_CONTROL_URL,
            bp="network_access_control_bp",
        )}

        UPDATE "role"
        SET "permissions" = (
            SELECT COALESCE(jsonb_agg(DISTINCT permission_value), '[]'::jsonb)
            FROM (
                SELECT permission_value
                FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS permission_values(permission_value)
                UNION
                SELECT permission_value
                FROM jsonb_array_elements_text('{NETWORK_TOPOLOGY_PERMISSIONS}'::jsonb) AS migrated_topology(permission_value)
                WHERE EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS existing_permissions(existing_permission)
                    WHERE existing_permission LIKE 'supply_chain_review:topology:%'
                )
            ) AS merged_permissions
        )
        WHERE "permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS existing_permissions(existing_permission)
              WHERE existing_permission LIKE 'supply_chain_review:topology:%'
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu"
        WHERE "model" IN (
            'asset-management-models',
            'asset-management-catalog',
            'network-management-topology',
            'network-management-access-control'
        );
    """
