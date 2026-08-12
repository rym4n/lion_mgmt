from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


NETWORK_TOPOLOGY_PERMISSIONS = """[
    "network_topology:c",
    "network_topology:r",
    "network_topology:u",
    "network_topology:d",
    {"label": "同步拓扑", "value": "network_topology:sync"},
    {"label": "重新分析拓扑", "value": "network_topology:reanalyze"},
    {"label": "管理拓扑 Profile", "value": "network_topology:profile_admin"}
]"""


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "Permissions" = '{NETWORK_TOPOLOGY_PERMISSIONS}'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'network-management-topology';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "Permissions" = '[
                "network_topology:c",
                "network_topology:r",
                "network_topology:u",
                "network_topology:d"
            ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'network-management-topology';
    """
