from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE OR REPLACE FUNCTION pg_temp.digital_employee_role_prompt_text(source jsonb)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        AS $function$
            SELECT CASE
                WHEN source IS NULL THEN ''
                WHEN jsonb_typeof(source) = 'string' THEN source #>> '{}'
                WHEN jsonb_typeof(source) = 'array' THEN COALESCE(
                    (
                        SELECT string_agg(
                            CASE
                                WHEN jsonb_typeof(item) = 'string' THEN '- ' || (item #>> '{}')
                                WHEN jsonb_typeof(item) = 'object' AND item ? 'scene' THEN concat_ws(E'\\n',
                                    NULLIF('### ' || COALESCE(item->>'scene', ''), '### '),
                                    CASE
                                        WHEN jsonb_typeof(item->'steps') = 'array'
                                            THEN (
                                                SELECT string_agg('- ' || step, E'\\n')
                                                FROM jsonb_array_elements_text(item->'steps') AS step_values(step)
                                            )
                                        WHEN item ? 'steps' THEN item->>'steps'
                                        ELSE NULL
                                    END,
                                    NULLIF((item - 'scene' - 'steps')::text, '{}')
                                )
                                ELSE item::text
                            END,
                            E'\\n'
                        )
                        FROM jsonb_array_elements(source) AS array_items(item)
                    ),
                    ''
                )
                WHEN jsonb_typeof(source) = 'object' THEN source::text
                ELSE COALESCE(source #>> '{}', source::text, '')
            END
        $function$;

        UPDATE "digital_employee_role"
        SET "meta" = jsonb_set("meta", '{identity}', to_jsonb(pg_temp.digital_employee_role_prompt_text("meta"->'identity')), false),
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'identity'
          AND jsonb_typeof("meta"->'identity') <> 'string';

        UPDATE "digital_employee_role"
        SET "meta" = jsonb_set("meta", '{principles}', to_jsonb(pg_temp.digital_employee_role_prompt_text("meta"->'principles')), false),
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'principles'
          AND jsonb_typeof("meta"->'principles') <> 'string';

        UPDATE "digital_employee_role"
        SET "meta" = jsonb_set("meta", '{responsibilities}', to_jsonb(pg_temp.digital_employee_role_prompt_text("meta"->'responsibilities')), false),
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'responsibilities'
          AND jsonb_typeof("meta"->'responsibilities') <> 'string';

        UPDATE "digital_employee_role"
        SET "meta" = jsonb_set("meta", '{workflows}', to_jsonb(pg_temp.digital_employee_role_prompt_text("meta"->'workflows')), false),
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'workflows'
          AND jsonb_typeof("meta"->'workflows') <> 'string';

        UPDATE "digital_employee_role"
        SET "meta" = jsonb_set("meta", '{safety_rules}', to_jsonb(pg_temp.digital_employee_role_prompt_text("meta"->'safety_rules')), false),
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'safety_rules'
          AND jsonb_typeof("meta"->'safety_rules') <> 'string';

        UPDATE "digital_employee_role"
        SET "meta" = jsonb_set("meta", '{tools_notes}', to_jsonb(pg_temp.digital_employee_role_prompt_text("meta"->'tools_notes')), false),
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'tools_notes'
          AND jsonb_typeof("meta"->'tools_notes') <> 'string';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return "SELECT 1;"
