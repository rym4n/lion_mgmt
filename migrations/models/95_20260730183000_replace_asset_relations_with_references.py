from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        CREATE TABLE "supply_chain_ci_reference" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "field_key" VARCHAR(64) NOT NULL,
            "source_ci_id" VARCHAR(36) NOT NULL,
            "target_ci_id" VARCHAR(36) NOT NULL,
            "position" INT NOT NULL DEFAULT 0,
            CONSTRAINT "fk_ci_reference_source" FOREIGN KEY ("source_ci_id")
                REFERENCES "supply_chain_ci" ("id") ON DELETE CASCADE,
            CONSTRAINT "fk_ci_reference_target" FOREIGN KEY ("target_ci_id")
                REFERENCES "supply_chain_ci" ("id") ON DELETE CASCADE,
            CONSTRAINT "uid_ci_reference_tenant_identity"
                UNIQUE ("tenant_id", "source_ci_id", "field_key", "target_ci_id")
        );
        CREATE INDEX "idx_ci_reference_source"
            ON "supply_chain_ci_reference" ("tenant_id", "source_ci_id");
        CREATE INDEX "idx_ci_reference_target"
            ON "supply_chain_ci_reference" ("tenant_id", "target_ci_id");
        CREATE INDEX "idx_ci_reference_source_field"
            ON "supply_chain_ci_reference" ("tenant_id", "source_ci_id", "field_key");

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM "supply_chain_ci_relation_type"
                WHERE "deleted" = FALSE
                  AND COALESCE("relation_fields", '[]'::jsonb) <> '[]'::jsonb
            ) THEN
                RAISE EXCEPTION
                    'Asset relations with attributes must be migrated to association asset models before migration 95';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM "supply_chain_ci_relation"
                WHERE "deleted" = FALSE
                  AND COALESCE("attributes", '{}'::jsonb) <> '{}'::jsonb
            ) THEN
                RAISE EXCEPTION
                    'Asset relation instances with attributes must be migrated to association asset models before migration 95';
            END IF;
        END $$;

        CREATE TEMP TABLE "tmp_m95_reference_fields" ON COMMIT DROP AS
        WITH expanded AS (
            SELECT
                relation_type."tenant_id",
                relation_type."key" AS relation_key,
                relation_type."name" AS relation_name,
                relation_type."semantic_key",
                relation_type."source_to_target",
                relation_type."target_to_source",
                relation_type."is_builtin",
                relation_type."builtin_version",
                relation_type."owner",
                source_model."id" AS source_type_id,
                source_model."key" AS source_type_key,
                target_model."id" AS target_type_id,
                target_model."key" AS target_type_key,
                target_model."name" AS target_type_name
            FROM "supply_chain_ci_relation_type" AS relation_type
            CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS_TEXT(
                COALESCE(relation_type."source_ci_type_ids", '[]'::jsonb)
            ) AS source_ref(value)
            CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS_TEXT(
                COALESCE(relation_type."target_ci_type_ids", '[]'::jsonb)
            ) AS target_ref(value)
            JOIN "supply_chain_ci_type" AS source_model
              ON source_model."tenant_id" IS NOT DISTINCT FROM relation_type."tenant_id"
             AND source_model."id" = source_ref.value
             AND source_model."deleted" = FALSE
            JOIN "supply_chain_ci_type" AS target_model
              ON target_model."tenant_id" IS NOT DISTINCT FROM relation_type."tenant_id"
             AND target_model."id" = target_ref.value
             AND target_model."deleted" = FALSE
            WHERE relation_type."deleted" = FALSE
        ), numbered AS (
            SELECT
                expanded.*,
                COUNT(*) OVER (
                    PARTITION BY "tenant_id", relation_key, source_type_id
                ) AS endpoint_count,
                ROW_NUMBER() OVER (
                    PARTITION BY "tenant_id", source_type_id
                    ORDER BY relation_key, target_type_key
                ) AS field_position
            FROM expanded
        )
        SELECT
            numbered.*,
            CASE
                WHEN endpoint_count = 1
                 AND NOT EXISTS (
                    SELECT 1
                    FROM "supply_chain_ci_field_def" AS existing_field
                    WHERE existing_field."tenant_id" IS NOT DISTINCT FROM numbered."tenant_id"
                      AND existing_field."ci_type_id" = numbered.source_type_id
                      AND existing_field."field_key" = numbered.relation_key
                 )
                THEN numbered.relation_key
                ELSE LEFT(numbered.relation_key, 55)
                    || '_' || LEFT(MD5(numbered.target_type_id), 8)
            END AS field_key
        FROM numbered;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM "supply_chain_ci_relation_type" AS relation_type
                WHERE relation_type."deleted" = FALSE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM "tmp_m95_reference_fields" AS mapping
                      WHERE mapping."tenant_id" IS NOT DISTINCT FROM relation_type."tenant_id"
                        AND mapping.relation_key = relation_type."key"
                  )
            ) THEN
                RAISE EXCEPTION
                    'Asset relation type endpoints could not be mapped to reference fields in migration 95';
            END IF;
        END $$;

        INSERT INTO "supply_chain_ci_field_def" (
            "id", "ctime", "utime", "deleted", "tenant_id",
            "ci_type_id", "ci_type_key", "field_key", "source_field_key",
            "label", "data_type", "enum_options", "options",
            "ref_ci_type_id", "ref_ci_type", "required", "unique", "editable",
            "is_derived", "field_group", "field_group_sort", "user_prompt",
            "default_value", "sort", "is_locked", "is_builtin", "builtin_key",
            "builtin_version", "builtin_hash", "owner", "creator", "updater"
        )
        SELECT
            'm95-' || MD5(
                COALESCE(mapping."tenant_id", '') || ':'
                || mapping.source_type_id || ':' || mapping.field_key
            ),
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            FALSE,
            mapping."tenant_id",
            mapping.source_type_id,
            mapping.source_type_key,
            mapping.field_key,
            mapping.field_key,
            LEFT(mapping.relation_name || ' -> ' || mapping.target_type_name, 180)
                || ' [' || mapping.field_key || ']',
            'ref',
            '[]'::jsonb,
            JSONB_BUILD_OBJECT(
                'multiple', mapping.source_to_target = 'many',
                'reverse_unique', mapping.target_to_source = 'one',
                'semantic_key', mapping.semantic_key,
                'derived_from_association', mapping.relation_key
            ),
            mapping.target_type_id,
            mapping.target_type_key,
            FALSE,
            FALSE,
            TRUE,
            FALSE,
            '关联信息',
            1000,
            '',
            NULL,
            COALESCE((
                SELECT MAX(existing_field."sort")
                FROM "supply_chain_ci_field_def" AS existing_field
                WHERE existing_field."tenant_id" IS NOT DISTINCT FROM mapping."tenant_id"
                  AND existing_field."ci_type_id" = mapping.source_type_id
            ), 0) + mapping.field_position,
            FALSE,
            mapping.is_builtin,
            CASE WHEN mapping.is_builtin
                THEN mapping.source_type_key || '.' || mapping.field_key
                ELSE NULL
            END,
            mapping.builtin_version,
            NULL,
            mapping.owner,
            'migration-95',
            'migration-95'
        FROM "tmp_m95_reference_fields" AS mapping
        ON CONFLICT ("tenant_id", "ci_type_id", "field_key") DO NOTHING;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM "tmp_m95_reference_fields" AS mapping
                LEFT JOIN "supply_chain_ci_field_def" AS field
                  ON field."tenant_id" IS NOT DISTINCT FROM mapping."tenant_id"
                 AND field."ci_type_id" = mapping.source_type_id
                 AND field."field_key" = mapping.field_key
                 AND field."data_type" = 'ref'
                 AND field."ref_ci_type_id" = mapping.target_type_id
                 AND field."deleted" = FALSE
                WHERE field."id" IS NULL
            ) THEN
                RAISE EXCEPTION
                    'Asset relation reference fields could not be created in migration 95';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM "supply_chain_ci_relation" AS relation
                WHERE relation."deleted" = FALSE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM "supply_chain_ci" AS source_ci
                      JOIN "supply_chain_ci" AS target_ci
                        ON target_ci."id" = relation."target_ci_id"
                       AND target_ci."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
                       AND target_ci."deleted" = FALSE
                      JOIN "tmp_m95_reference_fields" AS mapping
                        ON mapping."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
                       AND mapping.relation_key = relation."relation_type_key"
                       AND mapping.source_type_id = source_ci."ci_type_id"
                       AND mapping.target_type_id = target_ci."ci_type_id"
                      WHERE source_ci."id" = relation."source_ci_id"
                        AND source_ci."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
                        AND source_ci."deleted" = FALSE
                  )
            ) THEN
                RAISE EXCEPTION
                    'Asset relation instances could not be mapped to reference fields in migration 95';
            END IF;
        END $$;

        WITH relation_targets AS (
            SELECT
                relation."source_ci_id",
                mapping.field_key,
                mapping.source_to_target = 'many' AS multiple,
                JSONB_AGG(relation."target_ci_id" ORDER BY relation."ctime", relation."id")
                    AS target_ids
            FROM "supply_chain_ci_relation" AS relation
            JOIN "supply_chain_ci" AS source_ci
              ON source_ci."id" = relation."source_ci_id"
             AND source_ci."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
            JOIN "supply_chain_ci" AS target_ci
              ON target_ci."id" = relation."target_ci_id"
             AND target_ci."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
            JOIN "tmp_m95_reference_fields" AS mapping
              ON mapping."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
             AND mapping.relation_key = relation."relation_type_key"
             AND mapping.source_type_id = source_ci."ci_type_id"
             AND mapping.target_type_id = target_ci."ci_type_id"
            WHERE relation."deleted" = FALSE
              AND source_ci."deleted" = FALSE
              AND target_ci."deleted" = FALSE
            GROUP BY relation."source_ci_id", mapping.field_key, mapping.source_to_target
        ), relation_patches AS (
            SELECT
                source_ci_id,
                JSONB_OBJECT_AGG(
                    field_key,
                    CASE WHEN multiple THEN target_ids ELSE target_ids -> 0 END
                ) AS patch
            FROM relation_targets
            GROUP BY source_ci_id
        )
        UPDATE "supply_chain_ci" AS source_ci
        SET
            "attributes" = COALESCE(source_ci."attributes", '{}'::jsonb) || patch.patch,
            "utime" = CURRENT_TIMESTAMP
        FROM relation_patches AS patch
        WHERE source_ci."id" = patch.source_ci_id;

        UPDATE "supply_chain_ci_field_def" AS manager_field
        SET
            "data_type" = 'ref',
            "options" = '{"multiple":false,"reverse_unique":false}'::jsonb,
            "ref_ci_type_id" = employee_model."id",
            "ref_ci_type" = employee_model."key",
            "builtin_hash" =
                '45dfd9a579ed03a559cd3c32780fd53312a7ca08238da1ef1e48c78e0888df82',
            "utime" = CURRENT_TIMESTAMP
        FROM "supply_chain_ci_type" AS employee_model
        WHERE manager_field."tenant_id" IS NOT DISTINCT FROM employee_model."tenant_id"
          AND manager_field."ci_type_key" = 'project'
          AND manager_field."field_key" = 'manager'
          AND manager_field."deleted" = FALSE
          AND employee_model."key" = 'user'
          AND employee_model."deleted" = FALSE;

        WITH project_managers AS (
            SELECT
                project_ci."id" AS project_ci_id,
                project_ci."tenant_id",
                project_ci."attributes" ->> 'manager' AS manager_value,
                employee_model."id" AS employee_type_id
            FROM "supply_chain_ci" AS project_ci
            JOIN "supply_chain_ci_type" AS project_model
              ON project_model."id" = project_ci."ci_type_id"
             AND project_model."tenant_id" IS NOT DISTINCT FROM project_ci."tenant_id"
             AND project_model."key" = 'project'
            JOIN "supply_chain_ci_type" AS employee_model
              ON employee_model."tenant_id" IS NOT DISTINCT FROM project_ci."tenant_id"
             AND employee_model."key" = 'user'
             AND employee_model."deleted" = FALSE
            WHERE project_ci."deleted" = FALSE
              AND JSONB_TYPEOF(project_ci."attributes" -> 'manager') = 'string'
              AND NULLIF(project_ci."attributes" ->> 'manager', '') IS NOT NULL
        ), resolved AS (
            SELECT
                project_managers.*,
                COALESCE(
                    (
                        SELECT employee."id"
                        FROM "supply_chain_ci" AS employee
                        WHERE employee."tenant_id" IS NOT DISTINCT FROM project_managers."tenant_id"
                          AND employee."ci_type_id" = project_managers.employee_type_id
                          AND employee."id" = project_managers.manager_value
                          AND employee."deleted" = FALSE
                        LIMIT 1
                    ),
                    (
                        SELECT CASE WHEN COUNT(*) = 1 THEN MIN(employee."id") END
                        FROM "supply_chain_ci" AS employee
                        WHERE employee."tenant_id" IS NOT DISTINCT FROM project_managers."tenant_id"
                          AND employee."ci_type_id" = project_managers.employee_type_id
                          AND employee."attributes" ->> 'employee_no' = project_managers.manager_value
                          AND employee."deleted" = FALSE
                    ),
                    (
                        SELECT CASE WHEN COUNT(*) = 1 THEN MIN(employee."id") END
                        FROM "supply_chain_ci" AS employee
                        WHERE employee."tenant_id" IS NOT DISTINCT FROM project_managers."tenant_id"
                          AND employee."ci_type_id" = project_managers.employee_type_id
                          AND employee."attributes" ->> 'person_name' = project_managers.manager_value
                          AND employee."deleted" = FALSE
                    )
                ) AS target_ci_id
            FROM project_managers
        )
        UPDATE "supply_chain_ci" AS project_ci
        SET
            "attributes" = CASE
                WHEN resolved.target_ci_id IS NULL
                    THEN COALESCE(project_ci."attributes", '{}'::jsonb)
                ELSE JSONB_SET(
                    COALESCE(project_ci."attributes", '{}'::jsonb),
                    '{manager}',
                    TO_JSONB(resolved.target_ci_id),
                    TRUE
                )
            END,
            "utime" = CURRENT_TIMESTAMP
        FROM resolved
        WHERE project_ci."id" = resolved.project_ci_id;

        INSERT INTO "supply_chain_ci_reference" (
            "id", "ctime", "utime", "deleted", "tenant_id",
            "field_key", "source_ci_id", "target_ci_id", "position"
        )
        SELECT
            'm95-' || MD5(
                COALESCE(source_ci."tenant_id", '') || ':' || source_ci."id" || ':'
                || field."field_key" || ':' || target_ci."id"
            ),
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            FALSE,
            source_ci."tenant_id",
            field."field_key",
            source_ci."id",
            target_ci."id",
            value.ordinality - 1
        FROM "supply_chain_ci" AS source_ci
        JOIN "supply_chain_ci_field_def" AS field
          ON field."tenant_id" IS NOT DISTINCT FROM source_ci."tenant_id"
         AND field."ci_type_id" = source_ci."ci_type_id"
         AND field."data_type" = 'ref'
         AND field."deleted" = FALSE
        CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS_TEXT(
            CASE JSONB_TYPEOF(source_ci."attributes" -> field."field_key")
                WHEN 'array' THEN source_ci."attributes" -> field."field_key"
                WHEN 'string' THEN JSONB_BUILD_ARRAY(
                    source_ci."attributes" -> field."field_key"
                )
                ELSE '[]'::jsonb
            END
        ) WITH ORDINALITY AS value(target_ci_id, ordinality)
        JOIN "supply_chain_ci" AS target_ci
          ON target_ci."id" = value.target_ci_id
         AND target_ci."tenant_id" IS NOT DISTINCT FROM source_ci."tenant_id"
         AND target_ci."ci_type_id" = field."ref_ci_type_id"
         AND target_ci."deleted" = FALSE
        WHERE source_ci."deleted" = FALSE
        ON CONFLICT ("tenant_id", "source_ci_id", "field_key", "target_ci_id")
        DO NOTHING;

        UPDATE "permissions_menu" AS menu
        SET
            "Permissions" = (
                SELECT COALESCE(JSONB_AGG(permission), '[]'::jsonb)
                FROM JSONB_ARRAY_ELEMENTS(
                    COALESCE(menu."Permissions"::jsonb, '[]'::jsonb)
                ) AS permission
                WHERE NOT (
                    JSONB_TYPEOF(permission) = 'string'
                    AND permission #>> '{}' LIKE 'supply_chain_review:relation:%'
                )
                  AND NOT (
                    JSONB_TYPEOF(permission) = 'object'
                    AND COALESCE(
                        permission ->> 'permission',
                        permission ->> 'value',
                        ''
                    ) LIKE 'supply_chain_review:relation:%'
                )
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE COALESCE(menu."Permissions"::jsonb, '[]'::jsonb)::text
            LIKE '%supply_chain_review:relation:%';

        UPDATE "permissions_menu" AS menu
        SET
            "url" = (
                SELECT JSONB_OBJECT_AGG(
                    language.key,
                    JSONB_SET(
                        language.value,
                        '{children,0,meta}',
                        COALESCE(language.value #> '{children,0,meta}', '{}'::jsonb)
                            || JSONB_BUILD_OBJECT(
                                'permissions', '["supply_chain_review:model:r","supply_chain_review:field:r"]'::jsonb,
                                'auths', '["supply_chain_review:model:r","supply_chain_review:field:r"]'::jsonb
                            ),
                        TRUE
                    )
                )
                FROM JSONB_EACH(menu."url"::jsonb) AS language(key, value)
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE menu."model" = 'asset-management-models'
          AND JSONB_TYPEOF(menu."url"::jsonb) = 'object';

        UPDATE "role" AS role_row
        SET
            "permissions" = (
                SELECT COALESCE(JSONB_AGG(permission), '[]'::jsonb)
                FROM JSONB_ARRAY_ELEMENTS(
                    COALESCE(role_row."permissions"::jsonb, '[]'::jsonb)
                ) AS permission
                WHERE NOT (
                    JSONB_TYPEOF(permission) = 'string'
                    AND permission #>> '{}' LIKE 'supply_chain_review:relation:%'
                )
                  AND NOT (
                    JSONB_TYPEOF(permission) = 'object'
                    AND COALESCE(
                        permission ->> 'permission',
                        permission ->> 'value',
                        ''
                    ) LIKE 'supply_chain_review:relation:%'
                )
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE COALESCE(role_row."permissions"::jsonb, '[]'::jsonb)::text
            LIKE '%supply_chain_review:relation:%';

        DROP TABLE "supply_chain_ci_relation";
        DROP TABLE "supply_chain_ci_relation_type";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""
