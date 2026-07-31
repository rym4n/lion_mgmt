import asyncio

from core.listeners import LISTENER_TUPLE, EventIngestionTemplateRepairListener
from services.event_ingestion import template_repair


class _Template:
    def __init__(self, value):
        self.value = value
        self.saved_fields = []

    async def save(self, *, update_fields):
        self.saved_fields.append(update_fields)


class _Query:
    def __init__(self, rows):
        self.rows = rows

    async def all(self):
        return self.rows


def test_repair_qax_skyeye_template_value_is_targeted_and_idempotent():
    broken = "prefix parse_key_value!(.log,field_delimiter: ,key_value_delimiter: '=') suffix"
    fixed = template_repair.repair_qax_skyeye_template_value(broken)

    assert 'field_delimiter: " "' in fixed
    assert "field_delimiter: ," not in fixed
    assert template_repair.repair_qax_skyeye_template_value(fixed) == fixed


def test_repair_qax_skyeye_templates_updates_only_legacy_rows(monkeypatch):
    broken = _Template("parse_key_value!(.log,field_delimiter: ,key_value_delimiter: '=')")
    valid = _Template('parse_key_value!(.log,field_delimiter: " ",key_value_delimiter: "=")')

    def filter_templates(**kwargs):
        assert kwargs == {
            "key": "qax_skyeye",
            "module": "transforms",
            "deleted": False,
        }
        return _Query([broken, valid])

    monkeypatch.setattr(template_repair.Config, "filter", filter_templates)

    repaired = asyncio.run(template_repair.repair_qax_skyeye_templates())

    assert repaired == 1
    assert 'field_delimiter: " "' in broken.value
    assert broken.saved_fields == [["value", "utime"]]
    assert valid.saved_fields == []


def test_event_ingestion_template_repair_listener_is_registered():
    assert EventIngestionTemplateRepairListener in LISTENER_TUPLE
