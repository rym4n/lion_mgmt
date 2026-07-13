from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "supply_chain_ci_type"
        ALTER COLUMN "primary_field" SET DEFAULT '';

        UPDATE "supply_chain_ci_type" AS ci_type
        SET
            "primary_field" = '',
            "utime" = CURRENT_TIMESTAMP
        WHERE COALESCE(ci_type."primary_field", '') <> ''
          AND NOT EXISTS (
              SELECT 1
              FROM "supply_chain_ci_field_def" AS field_def
              WHERE field_def."tenant_id" IS NOT DISTINCT FROM ci_type."tenant_id"
                AND field_def."ci_type_key" = ci_type."key"
                AND field_def."field_key" = ci_type."primary_field"
                AND field_def."deleted" = False
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "supply_chain_ci_type"
        ALTER COLUMN "primary_field" SET DEFAULT 'name';
    """
