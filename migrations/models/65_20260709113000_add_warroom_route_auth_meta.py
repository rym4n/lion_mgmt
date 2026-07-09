from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

WARROOM_PERMISSIONS = '["warroom:c","warroom:r","warroom:u"]'


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "url" = (
                SELECT jsonb_object_agg(
                    lang.key,
                    jsonb_set(
                        jsonb_set(
                            lang.value,
                            '{{meta}}',
                            COALESCE(lang.value #> '{{meta}}', '{{}}'::jsonb)
                                || jsonb_build_object(
                                    'permissions', '{WARROOM_PERMISSIONS}'::jsonb,
                                    'auths', '{WARROOM_PERMISSIONS}'::jsonb
                                ),
                            True
                        ),
                        '{{children,0,meta}}',
                        COALESCE(lang.value #> '{{children,0,meta}}', '{{}}'::jsonb)
                            || jsonb_build_object(
                                'permissions', '{WARROOM_PERMISSIONS}'::jsonb,
                                'auths', '{WARROOM_PERMISSIONS}'::jsonb
                            ),
                        True
                    )
                )
                FROM jsonb_each("permissions_menu"."url") AS lang(key, value)
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'warroom'
          AND jsonb_typeof("url") = 'object';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
