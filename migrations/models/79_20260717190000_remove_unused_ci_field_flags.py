from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "supply_chain_ci_field_def"
            DROP COLUMN "searchable",
            DROP COLUMN "listable";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "supply_chain_ci_field_def"
            ADD COLUMN "searchable" BOOL NOT NULL DEFAULT FALSE,
            ADD COLUMN "listable" BOOL NOT NULL DEFAULT TRUE;
    """
