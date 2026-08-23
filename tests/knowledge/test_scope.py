# coding=utf-8
"""空间作用域与路径校验（SPEC §5.1、§11.2、§15）。

这是隔离的唯一实现点，每条规则都要有负向用例。
"""

import pytest

from libs.error_code.exception import CustomError


def test_valid_space_keys():
    from services.knowledge.scope import validate_space_key

    for key in ["monitoring", "skill-vuln-scan", "a", "ops2026", "a" * 63]:
        validate_space_key(key)  # 不抛异常即通过


def test_invalid_space_keys():
    """大写、下划线、斜杠、点、空格、超长、以横杠开头都要拒。
    斜杠尤其危险——它能在 URI 里造出一层目录，绕出空间边界。"""
    from services.knowledge.scope import validate_space_key

    for key in [
        "Monitoring", "my_space", "a/b", "a.b", "a b", "", "-lead",
        "a" * 64, "../etc", "空间",
    ]:
        with pytest.raises(CustomError):
            validate_space_key(key)


def test_rel_path_rejects_traversal():
    """路径穿越是本模块要挡的头号风险。"""
    from services.knowledge.scope import validate_rel_path

    for path in [
        "../secret.md", "a/../../b.md", "/etc/passwd", "a/./b/../../../c.md",
        "", "   ", "a\\..\\b.md",
    ]:
        with pytest.raises(CustomError):
            validate_rel_path(path)


def test_rel_path_normalizes():
    """允许中间目录，去掉首尾空白与重复斜杠。"""
    from services.knowledge.scope import validate_rel_path

    assert validate_rel_path("cve/2026/x.md") == "cve/2026/x.md"
    assert validate_rel_path("  a.md  ") == "a.md"
    assert validate_rel_path("a//b.md") == "a/b.md"


def test_rel_path_rejects_query_and_fragment():
    """URI 里带 ? 或 # 会让后续解析产生歧义。"""
    from services.knowledge.scope import validate_rel_path

    for path in ["a.md?x=1", "a.md#frag"]:
        with pytest.raises(CustomError):
            validate_rel_path(path)


def test_build_uri():
    from services.knowledge.scope import build_uri

    assert (
        build_uri("monitoring", "disk/alert.md")
        == "viking://resources/spaces/monitoring/disk/alert.md"
    )


def test_uri_in_space_prefix_trap():
    """空间 ops 的范围绝不能匹配到 ops-secret——
    朴素的 startswith(root) 会漏掉这个，必须比到分隔符。"""
    from services.knowledge.scope import uri_in_space

    ops_root = "viking://resources/spaces/ops"

    assert uri_in_space("viking://resources/spaces/ops/a.md", ops_root)
    assert uri_in_space("viking://resources/spaces/ops", ops_root)
    assert not uri_in_space("viking://resources/spaces/ops-secret/a.md", ops_root)
    assert not uri_in_space("viking://resources/spaces/other/a.md", ops_root)
    assert not uri_in_space("viking://agent/skills/x", ops_root)


@pytest.mark.asyncio
async def test_resolve_search_roots_no_request_uses_all_visible(monkeypatch):
    """不指定空间时搜全部可见空间。"""
    from services.knowledge import scope

    async def fake_roots(tenant_id, space_keys):
        return [
            "viking://resources/spaces/a",
            "viking://resources/spaces/b",
        ]

    monkeypatch.setattr(scope, "visible_space_roots", fake_roots)
    roots = await scope.resolve_search_roots("t1", ["a", "b"], None)
    assert roots == [
        "viking://resources/spaces/a",
        "viking://resources/spaces/b",
    ]


@pytest.mark.asyncio
async def test_resolve_search_roots_request_narrows_only(monkeypatch):
    """指定的空间在可见列表内 → 收窄到它。"""
    from services.knowledge import scope

    async def fake_roots(tenant_id, space_keys):
        return [f"viking://resources/spaces/{k}" for k in space_keys]

    monkeypatch.setattr(scope, "visible_space_roots", fake_roots)
    roots = await scope.resolve_search_roots("t1", ["a", "b"], "a")
    assert roots == ["viking://resources/spaces/a"]


@pytest.mark.asyncio
async def test_resolve_search_roots_request_cannot_widen(monkeypatch):
    """指定了不可见的空间 → 得到空集，而不是放大到该空间。
    这是越权的主要尝试路径，必须返回空而非报错（不泄露该空间是否存在）。"""
    from services.knowledge import scope

    async def fake_roots(tenant_id, space_keys):
        return [f"viking://resources/spaces/{k}" for k in space_keys]

    monkeypatch.setattr(scope, "visible_space_roots", fake_roots)
    roots = await scope.resolve_search_roots("t1", ["a"], "secret")
    assert roots == []


@pytest.mark.asyncio
async def test_resolve_search_roots_empty_binding(monkeypatch):
    """没绑任何空间的员工搜不到任何东西。"""
    from services.knowledge import scope

    async def fake_roots(tenant_id, space_keys):
        return []

    monkeypatch.setattr(scope, "visible_space_roots", fake_roots)
    assert await scope.resolve_search_roots("t1", [], None) == []
