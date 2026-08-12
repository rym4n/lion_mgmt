from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return 'ALTER TABLE "command_center_agent" ADD "enabled" BOOL NOT NULL DEFAULT True;'


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'ALTER TABLE "command_center_agent" DROP COLUMN "enabled";'
