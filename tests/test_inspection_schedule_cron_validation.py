import pytest

from libs.error_code.exception import CustomError
from services.inspection import logic
from services.inspection.logic import is_valid_schedule_cron


@pytest.mark.parametrize(
    "expression",
    [
        "@hourly",
        "*/5 * * * *",
        "0 */5 * * * *",
        "0 0 12 * * 1-5",
    ],
)
def test_inspection_schedule_accepts_engine_supported_cron(expression):
    assert is_valid_schedule_cron(expression)


@pytest.mark.parametrize(
    "expression",
    [
        None,
        "",
        "100 * * * * *",
        "* * *",
        "not-a-cron",
    ],
)
def test_inspection_schedule_rejects_invalid_cron(expression):
    assert not is_valid_schedule_cron(expression)


@pytest.mark.asyncio
async def test_inspection_schedule_rejects_invalid_cron_before_persistence():
    with pytest.raises(CustomError):
        await logic.save_schedule(
            {"name": "invalid", "schedule_cron": "100 * * * * *"},
            "tenant-a",
        )
