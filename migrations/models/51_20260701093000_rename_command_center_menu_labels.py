from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET "description" = '专家团队',
            "Permissions" = '[
                {"label": "新增专家团队权限", "value": "c"},
                {"label": "删除专家团队权限", "value": "d"},
                {"label": "修改专家团队权限", "value": "u"},
                {"label": "查看专家团队权限", "value": "r"}
            ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'command_center';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET "description" = '指挥中心',
            "Permissions" = '[
                {"label": "新增指挥中心权限", "value": "c"},
                {"label": "删除指挥中心权限", "value": "d"},
                {"label": "修改指挥中心权限", "value": "u"},
                {"label": "查看指挥中心权限", "value": "r"}
            ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'command_center';
    """
