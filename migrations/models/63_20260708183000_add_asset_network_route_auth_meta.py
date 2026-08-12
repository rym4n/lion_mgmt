from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


def _patch_child_meta_sql(model: str, permissions: str) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "url" = (
                SELECT jsonb_object_agg(
                    lang.key,
                    jsonb_set(
                        lang.value,
                        '{{children,0,meta}}',
                        COALESCE(lang.value #> '{{children,0,meta}}', '{{}}'::jsonb)
                            || jsonb_build_object(
                                'permissions', '{permissions}'::jsonb,
                                'auths', '{permissions}'::jsonb
                            ),
                        True
                    )
                )
                FROM jsonb_each("permissions_menu"."url") AS lang(key, value)
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = '{model}'
          AND jsonb_typeof("url") = 'object';
    """


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        {_patch_child_meta_sql(
            "asset-management-models",
            '["supply_chain_review:model:r","supply_chain_review:field:r","supply_chain_review:relation:r"]',
        )}

        {_patch_child_meta_sql(
            "asset-management-catalog",
            '["supply_chain_review:ci:r"]',
        )}

        {_patch_child_meta_sql(
            "network-management-topology",
            '["network_topology:r"]',
        )}

        {_patch_child_meta_sql(
            "network-management-access-control",
            '["supply_chain_review:policy:r"]',
        )}
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
