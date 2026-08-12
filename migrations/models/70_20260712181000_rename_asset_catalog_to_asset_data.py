from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


def _rename_asset_catalog_sql(
    *,
    description: str,
    cn_title: str,
    traditional_title: str,
    en_title: str,
) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "description" = '{description}',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'asset-management-catalog';

        UPDATE "permissions_menu" AS menu
        SET
            "url" = (
                SELECT jsonb_object_agg(
                    language.key,
                    jsonb_set(
                        language.value,
                        '{{children}}',
                        COALESCE(
                            (
                                SELECT jsonb_agg(
                                    CASE
                                        WHEN child.value ->> 'name' = 'AssetManagementCatalog'
                                          OR child.value ->> 'path' = '/asset-management/catalog'
                                        THEN jsonb_set(
                                            child.value,
                                            '{{meta,title}}',
                                            to_jsonb(
                                                CASE language.key
                                                    WHEN 'CN' THEN '{cn_title}'
                                                    WHEN 'TW' THEN '{traditional_title}'
                                                    WHEN 'HK' THEN '{traditional_title}'
                                                    WHEN 'EN' THEN '{en_title}'
                                                    ELSE COALESCE(
                                                        child.value #>> '{{meta,title}}',
                                                        '{en_title}'
                                                    )
                                                END
                                            ),
                                            True
                                        )
                                        ELSE child.value
                                    END
                                    ORDER BY child.ordinality
                                )
                                FROM jsonb_array_elements(
                                    COALESCE(language.value -> 'children', '[]'::jsonb)
                                ) WITH ORDINALITY AS child(value, ordinality)
                            ),
                            '[]'::jsonb
                        ),
                        True
                    )
                )
                FROM jsonb_each(menu."url") AS language(key, value)
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE menu."model" = 'asset-management-catalog'
          AND jsonb_typeof(menu."url") = 'object';
    """


async def upgrade(db: BaseDBAsyncClient) -> str:
    return _rename_asset_catalog_sql(
        description="资产数据",
        cn_title="资产数据",
        traditional_title="資產數據",
        en_title="Asset Data",
    )


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
