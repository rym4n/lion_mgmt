import ast
from pathlib import Path

from services.timed.cron_expression import is_valid_cron_expression


def test_accepts_legacy_five_field_cron_with_extra_spaces():
    assert is_valid_cron_expression("*/5  * * * *")
    assert is_valid_cron_expression("0 9 * * 1")


def test_accepts_seconds_first_six_field_cron():
    assert is_valid_cron_expression("*/10 * * * * *")
    assert is_valid_cron_expression("0 30 14 * * 1")


def test_accepts_croniter_macros_supported_by_the_previous_validator():
    for expression in (
        "@hourly",
        "@daily",
        "@midnight",
        "@weekly",
        "@monthly",
        "@yearly",
        "@annually",
    ):
        assert is_valid_cron_expression(expression)


def test_rejects_invalid_cron_macro():
    assert not is_valid_cron_expression("@not-a-cron-macro")


def test_rejects_invalid_field_counts_and_seconds():
    assert not is_valid_cron_expression("* * * *")
    assert not is_valid_cron_expression("61 * * * * *")


def test_timed_views_use_the_shared_validator():
    source = Path("services/timed/views.py").read_text()
    assert source.count("is_valid_cron_expression(tactics)") == 3
    assert "croniter.is_valid(tactics)" not in source


def test_event_views_import_runlog_list_from_its_owner():
    tree = ast.parse(Path("services/event/views.py").read_text())
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "libs.utils"
        for alias in node.names
    }
    assert "runlog_list" in imported_names
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "services.timed.views"
        for node in ast.walk(tree)
    )
