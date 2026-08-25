# coding=utf-8
"""空间作用域解析（SPEC §3.1）。"""

import pytest

from libs.error_code.exception import CustomError


def test_valid_space_keys():
    from services.knowledge.scope import validate_space_key

    for key in ["monitoring", "skill-vuln-scan", "a", "ops2026", "a" * 63]:
        validate_space_key(key)


def test_invalid_space_keys():
    from services.knowledge.scope import validate_space_key

    for key in [
        "Monitoring", "my_space", "a/b", "a.b", "a b", "", "-lead",
        "a" * 64, "../etc", "空间",
    ]:
        with pytest.raises(CustomError):
            validate_space_key(key)


def test_scope_has_no_uri_helpers():
    from services.knowledge import scope

    for gone in ("build_uri", "uri_in_space", "validate_rel_path", "visible_space_roots"):
        assert not hasattr(scope, gone), f"{gone} 仍存在"


@pytest.mark.asyncio
async def test_resolve_search_scope_no_request_uses_all_visible(monkeypatch):
    from services.knowledge import scope

    class _Space:
        def __init__(self, key):
            self.space_key = key

    async def fake_visible(tenant_id, space_keys):
        return [_Space("a"), _Space("b")]

    monkeypatch.setattr(scope, "visible_spaces", fake_visible)
    assert await scope.resolve_search_scope("t1", ["a", "b"], None) == ["a", "b"]


@pytest.mark.asyncio
async def test_resolve_search_scope_request_narrows_only(monkeypatch):
    from services.knowledge import scope

    class _Space:
        def __init__(self, key):
            self.space_key = key

    async def fake_visible(tenant_id, space_keys):
        return [_Space(k) for k in space_keys]

    monkeypatch.setattr(scope, "visible_spaces", fake_visible)
    assert await scope.resolve_search_scope("t1", ["a", "b"], "a") == ["a"]
    assert await scope.resolve_search_scope("t1", ["a"], "secret") == []


@pytest.mark.asyncio
async def test_resolve_search_scope_empty_binding(monkeypatch):
    from services.knowledge import scope

    async def fake_visible(tenant_id, space_keys):
        return []

    monkeypatch.setattr(scope, "visible_spaces", fake_visible)
    assert await scope.resolve_search_scope("t1", [], None) == []
