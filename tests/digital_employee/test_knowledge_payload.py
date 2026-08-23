# coding=utf-8
"""执行 payload 的 knowledge 段（SPEC §11.1）。"""

import pytest


class _FakeEmployee:
    def __init__(self, **kw):
        self.id = kw.get("id", "emp-1")
        self.tenant_id = kw.get("tenant_id", "t1")
        self.knowledge_space_keys = kw.get("space_keys", ["ops"])
        self.knowledge_write_space = kw.get("write_space")


class _FakeSpace:
    def __init__(self, space_key, name="空间", description="说明"):
        self.space_key = space_key
        self.name = name
        self.description = description


def _patch_spaces(monkeypatch, spaces):
    from services.digital_employee import knowledge_payload as mod

    class _QS:
        async def all(self):
            return spaces

    monkeypatch.setattr(mod.KnowledgeSpace, "filter", lambda **kw: _QS())
    return mod


@pytest.mark.asyncio
async def test_payload_contains_no_credentials(monkeypatch):
    mod = _patch_spaces(monkeypatch, [_FakeSpace("ops")])

    payload = await mod.build_knowledge_payload(_FakeEmployee())

    assert payload is not None
    forbidden = {"api_key", "endpoint", "account_id", "credential_version", "root"}
    assert forbidden.isdisjoint(payload.keys())
    for space in payload["spaces"]:
        assert forbidden.isdisjoint(space.keys())


@pytest.mark.asyncio
async def test_payload_shape(monkeypatch):
    mod = _patch_spaces(
        monkeypatch,
        [_FakeSpace("ops", "运维手册", "日常处置"), _FakeSpace("runbook", "预案", "应急")],
    )

    payload = await mod.build_knowledge_payload(
        _FakeEmployee(space_keys=["ops", "runbook"], write_space="runbook")
    )

    assert payload["enabled"] is True
    assert payload["spaces"] == [
        {"key": "ops", "name": "运维手册", "description": "日常处置", "writable": False},
        {"key": "runbook", "name": "预案", "description": "应急", "writable": True},
    ]


@pytest.mark.asyncio
async def test_no_binding_returns_none(monkeypatch):
    from services.digital_employee import knowledge_payload as mod

    assert await mod.build_knowledge_payload(_FakeEmployee(space_keys=[])) is None


@pytest.mark.asyncio
async def test_all_spaces_deleted_returns_none(monkeypatch):
    mod = _patch_spaces(monkeypatch, [])

    assert await mod.build_knowledge_payload(_FakeEmployee(space_keys=["gone"])) is None


@pytest.mark.asyncio
async def test_exception_degrades_to_none(monkeypatch):
    from services.digital_employee import knowledge_payload as mod

    class _Boom:
        async def all(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(mod.KnowledgeSpace, "filter", lambda **kw: _Boom())

    assert await mod.build_knowledge_payload(_FakeEmployee()) is None


def test_execute_coordinator_sends_knowledge_section():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "digital_employee"
        / "execute_coordinator.py"
    ).read_text(encoding="utf-8")

    assert "build_knowledge_payload" in src
    assert '"knowledge"' in src
