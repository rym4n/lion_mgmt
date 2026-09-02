from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


def _rename_copy(expression: str) -> str:
    """Rename the product term in a text or JSONB expression."""
    for old, new in (
        ("Digital Employees", "Digital Partners"),
        ("Digital Employee", "Digital Partner"),
        ("digital employees", "digital partners"),
        ("digital employee", "digital partner"),
        ("数字员工", "数字伙伴"),
        ("數字員工", "數字夥伴"),
    ):
        expression = f"replace({expression}, '{old}', '{new}')"
    return expression


async def upgrade(db: BaseDBAsyncClient) -> str:
    description = _rename_copy('"description"')
    permissions = _rename_copy('"Permissions"::text')
    url = _rename_copy('"url"::text')
    return f"""
        UPDATE "permissions_menu"
        SET
            "description" = {description},
            "Permissions" = ({permissions})::jsonb,
            "url" = ({url})::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE concat_ws(' ', "description", "Permissions"::text, "url"::text)
            ~ '(数字员工|數字員工|Digital Employee|digital employee)';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "description" = replace(replace(replace(replace(replace(replace("description",
                'Digital Partners', 'Digital Employees'),
                'Digital Partner', 'Digital Employee'),
                'digital partners', 'digital employees'),
                'digital partner', 'digital employee'),
                '数字伙伴', '数字员工'),
                '數字夥伴', '數字員工'),
            "Permissions" = replace(replace(replace(replace(replace(replace("Permissions"::text,
                'Digital Partners', 'Digital Employees'),
                'Digital Partner', 'Digital Employee'),
                'digital partners', 'digital employees'),
                'digital partner', 'digital employee'),
                '数字伙伴', '数字员工'),
                '數字夥伴', '數字員工')::jsonb,
            "url" = replace(replace(replace(replace(replace(replace("url"::text,
                'Digital Partners', 'Digital Employees'),
                'Digital Partner', 'Digital Employee'),
                'digital partners', 'digital employees'),
                'digital partner', 'digital employee'),
                '数字伙伴', '数字员工'),
                '數字夥伴', '數字員工')::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE concat_ws(' ', "description", "Permissions"::text, "url"::text)
            ~ '(数字伙伴|數字夥伴|Digital Partner|digital partner)';
    """
