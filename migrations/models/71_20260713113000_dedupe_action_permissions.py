from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


def _merge_permission_json(column: str, alias: str) -> str:
    return f"""
    {alias}_ids AS (
        SELECT DISTINCT
            permission."tenant_id",
            permission."tool_name",
            permission."action_name",
            resource.key AS resource_id,
            granted_id.value AS granted_id
        FROM "action_permissions" AS permission
        INNER JOIN duplicate_groups AS duplicate
            ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
           AND permission."tool_name" = duplicate."tool_name"
           AND permission."action_name" = duplicate."action_name"
        CROSS JOIN LATERAL jsonb_array_elements(permission."{column}") AS permission_map(value)
        CROSS JOIN LATERAL jsonb_each(permission_map.value) AS resource(key, value)
        LEFT JOIN LATERAL jsonb_array_elements_text(resource.value) AS granted_id(value) ON True
        WHERE permission."deleted" = False
    ),
    {alias}_by_resource AS (
        SELECT
            "tenant_id",
            "tool_name",
            "action_name",
            resource_id,
            COALESCE(
                jsonb_agg(to_jsonb(granted_id) ORDER BY granted_id)
                    FILTER (WHERE granted_id IS NOT NULL),
                '[]'::jsonb
            ) AS granted_ids
        FROM {alias}_ids
        GROUP BY "tenant_id", "tool_name", "action_name", resource_id
    ),
    merged_{alias} AS (
        SELECT
            "tenant_id",
            "tool_name",
            "action_name",
            jsonb_build_array(
                jsonb_object_agg(resource_id, granted_ids ORDER BY resource_id)
            ) AS permissions
        FROM {alias}_by_resource
        GROUP BY "tenant_id", "tool_name", "action_name"
    )
    """


async def upgrade(db: BaseDBAsyncClient) -> str:
    users_ctes = _merge_permission_json("users", "user")
    roles_ctes = _merge_permission_json("roles", "role")
    return f"""
        LOCK TABLE "action_permissions" IN SHARE ROW EXCLUSIVE MODE;

        DO $$
        BEGIN
            IF EXISTS (
                WITH duplicate_groups AS (
                    SELECT "tenant_id", "tool_name", "action_name"
                    FROM "action_permissions"
                    WHERE "deleted" = False
                    GROUP BY "tenant_id", "tool_name", "action_name"
                    HAVING count(*) > 1
                )
                SELECT 1
                FROM "action_permissions" AS permission
                INNER JOIN duplicate_groups AS duplicate
                    ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                   AND permission."tool_name" = duplicate."tool_name"
                   AND permission."action_name" = duplicate."action_name"
                WHERE permission."deleted" = False
                  AND (
                    jsonb_typeof(permission."users") <> 'array'
                    OR jsonb_typeof(permission."roles") <> 'array'
                  )
            ) THEN
                RAISE EXCEPTION 'duplicate action permissions contain non-array users or roles';
            END IF;

            IF EXISTS (
                WITH duplicate_groups AS (
                    SELECT "tenant_id", "tool_name", "action_name"
                    FROM "action_permissions"
                    WHERE "deleted" = False
                    GROUP BY "tenant_id", "tool_name", "action_name"
                    HAVING count(*) > 1
                ),
                permission_values AS (
                    SELECT permission."users" AS value
                    FROM "action_permissions" AS permission
                    INNER JOIN duplicate_groups AS duplicate
                        ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                       AND permission."tool_name" = duplicate."tool_name"
                       AND permission."action_name" = duplicate."action_name"
                    WHERE permission."deleted" = False
                    UNION ALL
                    SELECT permission."roles" AS value
                    FROM "action_permissions" AS permission
                    INNER JOIN duplicate_groups AS duplicate
                        ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                       AND permission."tool_name" = duplicate."tool_name"
                       AND permission."action_name" = duplicate."action_name"
                    WHERE permission."deleted" = False
                )
                SELECT 1
                FROM permission_values
                CROSS JOIN LATERAL jsonb_array_elements(value) AS permission_map(value)
                WHERE jsonb_typeof(permission_map.value) <> 'object'
            ) THEN
                RAISE EXCEPTION 'duplicate action permissions contain non-object permission maps';
            END IF;

            IF EXISTS (
                WITH duplicate_groups AS (
                    SELECT "tenant_id", "tool_name", "action_name"
                    FROM "action_permissions"
                    WHERE "deleted" = False
                    GROUP BY "tenant_id", "tool_name", "action_name"
                    HAVING count(*) > 1
                ),
                permission_values AS (
                    SELECT permission."users" AS value
                    FROM "action_permissions" AS permission
                    INNER JOIN duplicate_groups AS duplicate
                        ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                       AND permission."tool_name" = duplicate."tool_name"
                       AND permission."action_name" = duplicate."action_name"
                    WHERE permission."deleted" = False
                    UNION ALL
                    SELECT permission."roles" AS value
                    FROM "action_permissions" AS permission
                    INNER JOIN duplicate_groups AS duplicate
                        ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                       AND permission."tool_name" = duplicate."tool_name"
                       AND permission."action_name" = duplicate."action_name"
                    WHERE permission."deleted" = False
                )
                SELECT 1
                FROM permission_values
                CROSS JOIN LATERAL jsonb_array_elements(value) AS permission_map(value)
                CROSS JOIN LATERAL jsonb_each(permission_map.value) AS resource(key, value)
                WHERE jsonb_typeof(resource.value) <> 'array'
            ) THEN
                RAISE EXCEPTION 'duplicate action permissions contain non-array resource grants';
            END IF;

            IF EXISTS (
                WITH duplicate_groups AS (
                    SELECT "tenant_id", "tool_name", "action_name"
                    FROM "action_permissions"
                    WHERE "deleted" = False
                    GROUP BY "tenant_id", "tool_name", "action_name"
                    HAVING count(*) > 1
                ),
                permission_values AS (
                    SELECT permission."users" AS value
                    FROM "action_permissions" AS permission
                    INNER JOIN duplicate_groups AS duplicate
                        ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                       AND permission."tool_name" = duplicate."tool_name"
                       AND permission."action_name" = duplicate."action_name"
                    WHERE permission."deleted" = False
                    UNION ALL
                    SELECT permission."roles" AS value
                    FROM "action_permissions" AS permission
                    INNER JOIN duplicate_groups AS duplicate
                        ON permission."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
                       AND permission."tool_name" = duplicate."tool_name"
                       AND permission."action_name" = duplicate."action_name"
                    WHERE permission."deleted" = False
                )
                SELECT 1
                FROM permission_values
                CROSS JOIN LATERAL jsonb_array_elements(value) AS permission_map(value)
                CROSS JOIN LATERAL jsonb_each(permission_map.value) AS resource(key, value)
                CROSS JOIN LATERAL jsonb_array_elements(resource.value) AS granted_id(value)
                WHERE jsonb_typeof(granted_id.value) <> 'string'
            ) THEN
                RAISE EXCEPTION 'duplicate action permissions contain non-string grant ids';
            END IF;
        END
        $$;

        WITH duplicate_groups AS (
            SELECT
                "tenant_id",
                "tool_name",
                "action_name",
                (array_agg("id" ORDER BY "ctime", "id"))[1] AS keeper_id
            FROM "action_permissions"
            WHERE "deleted" = False
            GROUP BY "tenant_id", "tool_name", "action_name"
            HAVING count(*) > 1
        ),
        {users_ctes},
        {roles_ctes}
        UPDATE "action_permissions" AS keeper
        SET
            "users" = COALESCE(merged_user.permissions, '[]'::jsonb),
            "roles" = COALESCE(merged_role.permissions, '[]'::jsonb),
            "utime" = CURRENT_TIMESTAMP
        FROM duplicate_groups AS duplicate
        LEFT JOIN merged_user
            ON merged_user."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
           AND merged_user."tool_name" = duplicate."tool_name"
           AND merged_user."action_name" = duplicate."action_name"
        LEFT JOIN merged_role
            ON merged_role."tenant_id" IS NOT DISTINCT FROM duplicate."tenant_id"
           AND merged_role."tool_name" = duplicate."tool_name"
           AND merged_role."action_name" = duplicate."action_name"
        WHERE keeper."id" = duplicate.keeper_id;

        WITH ranked_permissions AS (
            SELECT
                "id",
                row_number() OVER (
                    PARTITION BY "tenant_id", "tool_name", "action_name"
                    ORDER BY "ctime", "id"
                ) AS row_number
            FROM "action_permissions"
            WHERE "deleted" = False
        )
        UPDATE "action_permissions" AS duplicate
        SET
            "deleted" = True,
            "utime" = CURRENT_TIMESTAMP
        FROM ranked_permissions AS ranked
        WHERE duplicate."id" = ranked."id"
          AND ranked.row_number > 1;

        CREATE UNIQUE INDEX IF NOT EXISTS "uid_action_permissions_active_scope"
        ON "action_permissions" (
            "tenant_id",
            "tool_name",
            "action_name"
        )
        NULLS NOT DISTINCT
        WHERE "deleted" = False;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_action_permissions_active_scope";
    """
