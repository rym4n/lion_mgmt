from models.config import Config

QAX_SKYEYE_TEMPLATE_KEY = "qax_skyeye"
BROKEN_FIELD_DELIMITER = "field_delimiter: ,"
QUOTED_SPACE_FIELD_DELIMITER = 'field_delimiter: " "'


def repair_qax_skyeye_template_value(value: str) -> str:
    """Repair only the legacy invalid delimiter fragment in a template value."""
    if not isinstance(value, str):
        return value
    return value.replace(BROKEN_FIELD_DELIMITER, QUOTED_SPACE_FIELD_DELIMITER)


async def repair_qax_skyeye_templates() -> int:
    """Upgrade persisted encrypted templates without replacing user customizations."""
    templates = await Config.filter(
        key=QAX_SKYEYE_TEMPLATE_KEY,
        module="transforms",
        deleted=False,
    ).all()
    repaired = 0
    for template in templates:
        value = repair_qax_skyeye_template_value(template.value)
        if value == template.value:
            continue
        template.value = value
        await template.save(update_fields=["value", "utime"])
        repaired += 1
    return repaired
