from croniter import croniter


def is_valid_cron_expression(expression: str) -> bool:
    fields = str(expression or "").strip().split()
    if len(fields) == 1:
        return croniter.is_valid(fields[0])
    if len(fields) == 5:
        return croniter.is_valid(" ".join(fields))
    if len(fields) == 6:
        return croniter.is_valid(" ".join(fields), second_at_beginning=True)
    return False
