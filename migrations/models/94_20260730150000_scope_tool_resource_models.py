from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources_model"
        ADD COLUMN IF NOT EXISTS "internal" BOOL NOT NULL DEFAULT False;

        UPDATE "tool_resources_model" AS resource_model
        SET "internal" = COALESCE(
            (
                SELECT tool."maintain"
                FROM "tool" AS tool
                WHERE tool."tenant_id" IS NOT DISTINCT FROM resource_model."tenant_id"
                  AND tool."name" = resource_model."tool_name"
                  AND tool."deleted" = False
                ORDER BY tool."ctime" DESC, tool."id" DESC
                LIMIT 1
            ),
            False
        );

        WITH base_models AS (
            SELECT DISTINCT ON (resource_model."tenant_id", resource_model."tool_name")
                resource_model.*
            FROM "tool_resources_model" AS resource_model
            WHERE resource_model."deleted" = False
            ORDER BY
                resource_model."tenant_id",
                resource_model."tool_name",
                resource_model."utime" DESC,
                resource_model."id" DESC
        ), desired_scopes AS (
            SELECT DISTINCT
                tool."tenant_id",
                tool."name" AS "tool_name",
                tool."maintain" AS "internal"
            FROM "tool" AS tool
            WHERE tool."deleted" = False
        )
        INSERT INTO "tool_resources_model" (
            "id",
            "ctime",
            "utime",
            "deleted",
            "tenant_id",
            "tool_name",
            "version",
            "resources",
            "status",
            "health_check",
            "internal"
        )
        SELECT
            'm94-' || md5(base."id" || ':' || scope."internal"::text),
            base."ctime",
            base."utime",
            False,
            base."tenant_id",
            base."tool_name",
            base."version",
            base."resources",
            base."status",
            base."health_check",
            scope."internal"
        FROM base_models AS base
        JOIN desired_scopes AS scope
          ON scope."tenant_id" IS NOT DISTINCT FROM base."tenant_id"
         AND scope."tool_name" = base."tool_name"
        WHERE NOT EXISTS (
            SELECT 1
            FROM "tool_resources_model" AS existing
            WHERE existing."tenant_id" IS NOT DISTINCT FROM base."tenant_id"
              AND existing."tool_name" = base."tool_name"
              AND existing."internal" = scope."internal"
              AND existing."deleted" = False
        );

        CREATE INDEX IF NOT EXISTS "idx_tool_resource_model_mode_scope"
        ON "tool_resources_model" (
            "tenant_id",
            "tool_name",
            "internal",
            "deleted"
        );

        COMMENT ON COLUMN "tool_resources_model"."internal"
        IS '是否为维护模式资源模板';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_tool_resource_model_mode_scope";
        DELETE FROM "tool_resources_model" WHERE "id" LIKE 'm94-%';
        ALTER TABLE "tool_resources_model" DROP COLUMN IF EXISTS "internal";
    """
