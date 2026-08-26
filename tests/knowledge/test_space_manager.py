# coding=utf-8
"""空间管理业务逻辑（SPEC §3.1）。"""

import pytest

from libs.error_code.exception import CustomError


class _FakeSpace:
    def __init__(self, space_key="ops", tenant_id="t1", name="运维", **kw):
        self.id = kw.get("id", "space-1")
        self.space_key = space_key
        self.tenant_id = tenant_id
        self.name = name
        self.description = kw.get("description")
        self.source_type = kw.get("source_type", "manual")
        self.source_ref = kw.get("source_ref")
        self.deleted = False
        self.saved = False

    @property
    def json(self):
        return {"id": self.id, "space_key": self.space_key, "name": self.name}

    async def save(self):
        self.saved = True


class _FakeQS:
    def __init__(self, n):
        self._n = n

    async def count(self):
        return self._n

    async def update(self, **kwargs):
        return self._n


@pytest.mark.asyncio
async def test_create_space_rejects_generated_key_conflict(monkeypatch):
    from services.knowledge import manager

    async def fake_get_or_none(**kwargs):
        return _FakeSpace()

    monkeypatch.setattr(manager.KnowledgeSpace, "get_or_none", fake_get_or_none)
    monkeypatch.setattr(
        manager.uuid,
        "uuid4",
        lambda: type("FakeUUID", (), {"hex": "1" * 32})(),
    )
    with pytest.raises(CustomError):
        await manager.create_space("t1", "运维")


@pytest.mark.asyncio
async def test_create_space_writes_only_local_metadata(monkeypatch):
    from services.knowledge import manager

    created = _FakeSpace()

    async def fake_get_or_none(**kwargs):
        return None

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return created

    monkeypatch.setattr(manager.KnowledgeSpace, "get_or_none", fake_get_or_none)
    monkeypatch.setattr(manager.KnowledgeSpace, "create", fake_create)
    monkeypatch.setattr(
        manager.uuid,
        "uuid4",
        lambda: type("FakeUUID", (), {"hex": "2" * 32})(),
    )
    result = await manager.create_space("t1", "运维", description="运维资料")
    assert result is created
    assert captured == {
        "tenant_id": "t1",
        "space_key": f"space-{'2' * 32}",
        "name": "运维",
        "description": "运维资料",
        "source_type": "manual",
        "source_ref": None,
    }


@pytest.mark.asyncio
async def test_ensure_space_validates_internal_key(monkeypatch):
    from services.knowledge import manager

    with pytest.raises(CustomError):
        await manager.ensure_space("t1", "Bad_Key", "x")


def test_update_space_cannot_change_key():
    import inspect

    from services.knowledge import manager

    sig = inspect.signature(manager.update_space)
    assert "name" in sig.parameters
    assert "description" in sig.parameters


@pytest.mark.asyncio
async def test_delete_space_dry_run_does_not_mutate(monkeypatch):
    from services.knowledge import manager

    space = _FakeSpace()

    async def fake_get(tenant_id, space_key):
        return space

    async def fake_affected(tenant_id, space_key):
        return ["emp-1", "emp-2"]

    monkeypatch.setattr(manager, "get_space_or_404", fake_get)
    monkeypatch.setattr(manager, "_employees_bound_to", fake_affected)
    monkeypatch.setattr(manager.KnowledgeDocument, "filter", lambda **kw: _FakeQS(7))
    result = await manager.delete_space("t1", "ops", dry_run=True)
    assert result["document_count"] == 7
    assert result["affected_employees"] == ["emp-1", "emp-2"]
    assert space.deleted is False
    assert space.saved is False


@pytest.mark.asyncio
async def test_delete_space_soft_deletes_local_records(monkeypatch):
    from services.knowledge import manager

    space = _FakeSpace()

    async def fake_get(tenant_id, space_key):
        return space

    async def fake_affected(tenant_id, space_key):
        return []

    monkeypatch.setattr(manager, "get_space_or_404", fake_get)
    monkeypatch.setattr(manager, "_employees_bound_to", fake_affected)
    monkeypatch.setattr(manager.KnowledgeDocument, "filter", lambda **kw: _FakeQS(3))
    await manager.delete_space("t1", "ops", dry_run=False)
    assert space.deleted is True
    assert space.saved is True


@pytest.mark.asyncio
async def test_ensure_space_is_idempotent(monkeypatch):
    from services.knowledge import manager

    existing = _FakeSpace(space_key="skill-vuln", source_type="skill_package")

    async def fake_get_or_none(**kwargs):
        return existing

    monkeypatch.setattr(manager.KnowledgeSpace, "get_or_none", fake_get_or_none)
    result = await manager.ensure_space("t1", "skill-vuln", "漏洞扫描", "skill_package", "vuln-scan")
    assert result is existing


def test_scope_has_no_uri_helpers():
    from services.knowledge import scope

    for gone in ("build_uri", "uri_in_space", "validate_rel_path", "visible_space_roots"):
        assert not hasattr(scope, gone), f"{gone} 仍存在"


def test_scope_still_validates_space_key():
    from services.knowledge.scope import validate_space_key

    validate_space_key("ops")
    for bad in ("../x", "OPS", "a/b", ""):
        with pytest.raises(CustomError):
            validate_space_key(bad)


def test_openviking_package_is_gone():
    import importlib

    for module in ("services.openviking", "models.openviking",
                   "services.digital_employee.openviking"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)


def test_settings_has_no_openviking_config():
    import settings

    for gone in ("OPENVIKING_ADMIN_BASE_URL", "OPENVIKING_ROOT_API_KEY"):
        assert not hasattr(settings, gone), f"{gone} 仍存在"


def test_space_sync_route_removed():
    from services.knowledge.views import knowledge_bp

    paths = {getattr(r, "path", "") for r in knowledge_bp.routes}
    assert not any("sync" in p for p in paths)
