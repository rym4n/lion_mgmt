from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "inspection_result"
            ADD COLUMN IF NOT EXISTS "target_content" TEXT;
        ALTER TABLE "inspection_result"
            ADD COLUMN IF NOT EXISTS "target_standard" TEXT;

        UPDATE "inspection_result" AS result
        SET
            "target_content" = COALESCE(result."target_content", target."content"),
            "target_standard" = COALESCE(result."target_standard", target."standard")
        FROM "inspection_target" AS target
        WHERE target."id" = result."target_id"
          AND target."tenant_id" = result."tenant_id"
          AND (
              result."target_content" IS NULL
              OR result."target_standard" IS NULL
          );

        COMMENT ON COLUMN "inspection_result"."target_content"
            IS '巡检执行时的巡检内容快照';
        COMMENT ON COLUMN "inspection_result"."target_standard"
            IS '巡检执行时的巡检标准快照';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "inspection_result" DROP COLUMN IF EXISTS "target_standard";
        ALTER TABLE "inspection_result" DROP COLUMN IF EXISTS "target_content";
    """
