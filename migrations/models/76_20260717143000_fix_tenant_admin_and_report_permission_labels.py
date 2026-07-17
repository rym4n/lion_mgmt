from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "description" = '汇报任务',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'agent_report_schedule'
          AND "description" = '汇报助手';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "description" = '汇报助手',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'agent_report_schedule'
          AND "description" = '汇报任务';
    """
