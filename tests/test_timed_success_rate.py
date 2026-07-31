# coding=utf-8

from types import SimpleNamespace

import pytest

from libs.security.authorization import (
    account_has_permission,
    action_permission_hits,
    check_action_execute_permission,
    is_superadmin,
)
from services.timed.views import (
    _parse_dashboard_datetime_range,
    _timed_action_permissions,
    _timed_internal_scope_from_request,
    _timed_name_filter_kwargs,
    dashboard_statistics,
    timer_success_rate,
)


def _account(*permission_lists, tenant_ids=None):
    tenant_ids = tenant_ids or ["tenant-a"] * len(permission_lists)
    roles = [
        SimpleNamespace(id=f"role-{index}", permissions=perms, tenant_id=tenant_id)
        for index, (perms, tenant_id) in enumerate(
            zip(permission_lists, tenant_ids)
        )
    ]
    return SimpleNamespace(roles=roles)


def test_account_has_permission_matches_wildcard():
    wildcard_admin = _account(["*:*"])
    assert account_has_permission(wildcard_admin, "timed:r") is True
    assert is_superadmin(wildcard_admin) is False

    tenant_admin = _account(["timed:c,d,u,r", "workflow:c,d,u,r"])
    assert account_has_permission(tenant_admin, "timed:r") is True
    assert account_has_permission(tenant_admin, "timed:u") is True
    assert is_superadmin(tenant_admin) is False

    reader = _account(["timed:r"])
    assert account_has_permission(reader, "timed:r") is True
    assert account_has_permission(reader, "timed:u") is False


def test_account_has_permission_handles_empty():
    assert account_has_permission(None, "timed:r") is False
    assert account_has_permission(SimpleNamespace(roles=[]), "timed:r") is False


def test_account_has_permission_can_scope_roles_by_tenant():
    account = _account(["warroom:r"], ["event:r"], tenant_ids=["tenant-b", "tenant-a"])

    assert account_has_permission(account, "warroom:r") is True
    assert account_has_permission(account, "warroom:r", tenant_id="tenant-a") is False
    assert account_has_permission(account, "event:r", tenant_id="tenant-a") is True


def test_is_superadmin_only_matches_fixed_platform_role_id():
    account = SimpleNamespace(
        roles=[
            SimpleNamespace(id="tenant-admin", permissions=["*:*"], tenant_id="tenant-a"),
            SimpleNamespace(id="111111", permissions=["role:r"], tenant_id="tenant-b"),
        ]
    )

    assert is_superadmin(account) is True
    assert is_superadmin(account, tenant_id="tenant-a") is True
    assert is_superadmin(account, tenant_id="tenant-b") is True


def test_timed_action_permissions_derives_from_vertical_rights():
    # 定时任务列表的操作项由 timed 垂直权限推导，与底层剧本/动作的资源权限无关
    assert _timed_action_permissions(_account(["*:*"])) == ["r", "u", "d"]
    assert _timed_action_permissions(_account(["timed:c,d,u,r"])) == ["r", "u", "d"]
    assert _timed_action_permissions(_account(["timed:r"])) == ["r"]
    assert _timed_action_permissions(_account(["timed:r,u"])) == ["r", "u"]


def test_timed_action_permissions_respects_tenant_scope():
    account = _account(["timed:r,u"], ["timed:r"], tenant_ids=["tenant-a", "tenant-b"])

    assert _timed_action_permissions(account, tenant_id="tenant-a") == ["r", "u"]
    assert _timed_action_permissions(account, tenant_id="tenant-b") == ["r"]


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def distinct(self):
        return self

    def annotate(self, **_kwargs):
        return self

    def group_by(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def values_list(self, *_args, **_kwargs):
        return self

    def values(self, *_args):
        return self

    async def all(self):
        return self._value

    def __await__(self):
        async def _result():
            return self._value

        return _result().__await__()


def test_timed_name_filter_scopes_by_visible_internal_state():
    assert _timed_name_filter_kwargs("test", "tenant-1", False) == {
        "name": "test",
        "deleted": False,
        "tenant_id": "tenant-1",
        "internal": False,
    }


@pytest.mark.parametrize(
    ("scope", "maintenance_mode", "expected"),
    [
        (0, True, True),
        ("0", True, True),
        (1, False, False),
        ("1", False, False),
        (2, True, True),
        ("2", False, False),
        (None, True, True),
        (None, False, False),
        (1, "false", False),
    ],
)
def test_timed_internal_scope_ignores_request_scope(scope, maintenance_mode, expected):
    assert _timed_internal_scope_from_request(scope, maintenance_mode) is expected


def test_parse_dashboard_datetime_range_includes_end_day_midnight():
    start_dt, end_dt = _parse_dashboard_datetime_range("2026-04-01T00:00:00Z", "2026-04-30T00:00:00Z")

    assert start_dt.isoformat() == "2026-04-01T00:00:00+00:00"
    assert end_dt.isoformat() == "2026-04-30T23:59:59.999999+00:00"


@pytest.mark.asyncio
async def test_timer_success_rate_groups_node_logs_by_run_id(monkeypatch):
    timed = SimpleNamespace(id="timer-1", success_rate=None, result=None)
    saved = {}

    async def save(**kwargs):
        saved["kwargs"] = kwargs

    timed.save = save

    logs = [
        SimpleNamespace(run_id="run-1", status=0),
        SimpleNamespace(run_id="run-1", status=0),
        SimpleNamespace(run_id="run-2", status=0),
        SimpleNamespace(run_id="run-2", status=-1),
    ]
    recent_logs = [
        {"run_id": "run-2"},
        {"run_id": "run-1"},
    ]

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            assert kwargs.get("timer_id") == timed.id
            if "run_id__in" in kwargs:
                return _FakeQuery(logs)
            return _FakeQuery(recent_logs)

    monkeypatch.setattr("services.timed.views.Runlog", FakeRunlog)

    result = await timer_success_rate(timed)

    assert saved["kwargs"] == {"update_fields": ["success_rate", "result", "utime"]}
    assert result == {
        "id": "timer-1",
        "result": "fail",
        "success_rate": "50.0%",
    }
    assert timed.result == "fail"
    assert timed.success_rate == "50.0%"


@pytest.mark.asyncio
async def test_timer_success_rate_reports_latest_running_logs(monkeypatch):
    timed = SimpleNamespace(id="timer-1", success_rate=None, result=None)
    saved = {}

    async def save(**kwargs):
        saved["kwargs"] = kwargs

    timed.save = save

    logs = [
        SimpleNamespace(run_id="run-3", status=1),
        SimpleNamespace(run_id="run-3", status=2),
        SimpleNamespace(run_id="run-2", status=0),
        SimpleNamespace(run_id="run-1", status=0),
    ]
    recent_logs = [
        {"run_id": "run-3"},
        {"run_id": "run-2"},
        {"run_id": "run-1"},
    ]

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            assert kwargs.get("timer_id") == timed.id
            if "run_id__in" in kwargs:
                return _FakeQuery(logs)
            return _FakeQuery(recent_logs)

    monkeypatch.setattr("services.timed.views.Runlog", FakeRunlog)

    result = await timer_success_rate(timed)

    assert saved["kwargs"] == {"update_fields": ["success_rate", "result", "utime"]}
    assert result == {
        "id": "timer-1",
        "result": "running",
        "success_rate": "66.67%",
    }
    assert timed.result == "running"
    assert timed.success_rate == "66.67%"


@pytest.mark.asyncio
async def test_timer_success_rate_does_not_touch_unchanged_task(monkeypatch):
    timed = SimpleNamespace(id="timer-1", success_rate="100.0%", result="success")

    async def unexpected_save(**_kwargs):
        raise AssertionError("unchanged statistics must not update task utime")

    timed.save = unexpected_save
    logs = [SimpleNamespace(run_id="run-1", status=0)]
    recent_logs = [{"run_id": "run-1"}]

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            return _FakeQuery(logs if "run_id__in" in kwargs else recent_logs)

    monkeypatch.setattr("services.timed.views.Runlog", FakeRunlog)

    result = await timer_success_rate(timed)

    assert result == {"id": "timer-1", "result": "success", "success_rate": "100.0%"}


@pytest.mark.asyncio
async def test_dashboard_statistics_counts_status_one_as_failure(monkeypatch):
    captured = {}

    class FakeTimed:
        @staticmethod
        async def get_or_none(**kwargs):
            captured["timed_filter"] = kwargs
            return SimpleNamespace(id=kwargs["id"])

    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [
                {
                    "date": "2026-04-20",
                    "total_runs": 21,
                    "success_count": 15,
                    "fail_count": 6,
                }
            ]

    class FakeTransaction:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    def fake_response_ok(data):
        return data

    async def cache_get(key):
        assert key == "session-1"
        return False

    request = SimpleNamespace(
        json={
            "id": "timer-1",
            "start": "2026-04-01T00:00:00Z",
            "end": "2026-04-30T00:00:00Z",
        },
        ctx=SimpleNamespace(
            session={"id": "session-1", "tenant_id": "tenant-1"}
        ),
        app=SimpleNamespace(
            ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))
        ),
    )

    monkeypatch.setattr("services.timed.views.Timed", FakeTimed)
    monkeypatch.setattr("services.timed.views.in_transaction", lambda: FakeTransaction())
    monkeypatch.setattr("services.timed.views.response_ok", fake_response_ok)

    result = await dashboard_statistics.__wrapped__.__wrapped__(request)

    assert "status NOT IN (0)" in captured["sql"]
    assert "status NOT IN (0, 1)" not in captured["sql"]
    assert captured["timed_filter"] == {
        "id": "timer-1",
        "deleted": False,
        "internal": False,
        "tenant_id": "tenant-1",
    }
    assert captured["params"][2].isoformat() == "2026-04-30T23:59:59.999999+00:00"
    assert result == [
        {
            "date": "2026-04-20",
            "total_runs": 21,
            "success_count": 15,
            "fail_count": 6,
            "success_rate": "71.0%",
        }
    ]


def _action_perm(users_map, roles_map, action_name="get_events_list"):
    return SimpleNamespace(
        action_name=action_name,
        users=[users_map] if users_map else [],
        roles=[roles_map] if roles_map else [],
    )


def test_action_permission_hits_by_user_and_role():
    perm = _action_perm({"res-1": ["user-a"], "res-2": ["user-b"]}, {"res-1": ["role-x"]})
    # 用户直命中
    assert action_permission_hits(perm, "res-1", "user-a", set()) is True
    # 角色命中
    assert action_permission_hits(perm, "res-1", "user-c", {"role-x"}) is True
    # 都不命中
    assert action_permission_hits(perm, "res-1", "user-c", {"role-y"}) is False
    # 资源未在授权表内
    assert action_permission_hits(perm, "res-3", "user-a", {"role-x"}) is False
    # 空 users/roles 结构
    empty = SimpleNamespace(users=[], roles=[])
    assert action_permission_hits(empty, "res-1", "user-a", {"role-x"}) is False


@pytest.mark.asyncio
async def test_check_action_execute_permission_superadmin_bypass(monkeypatch):
    called = {"filter": 0}

    class _Blocker:
        @staticmethod
        def filter(**_kwargs):
            called["filter"] += 1
            raise AssertionError("超管应豁免，不该查表")

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _Blocker)
    account = SimpleNamespace(
        id="admin-1",
        roles=[
            SimpleNamespace(
                id="111111",
                name="超管",
                permissions=["role:r"],
                tenant_id="t1",
            )
        ],
    )
    assert await check_action_execute_permission(account, "lion_mgmt", "get_events_list", "res-1", "t1") is True
    assert called["filter"] == 0


@pytest.mark.asyncio
async def test_check_action_execute_permission_uncontrolled_role_bypasses_records_after_resource_check(monkeypatch):
    calls = []

    class _ResourceFilter:
        async def values_list(self, field, flat=False):
            assert field == "id"
            assert flat is True
            return ["res-1"]

    class _ToolResources:
        @staticmethod
        def filter(**kwargs):
            calls.append(kwargs)
            return _ResourceFilter()

    class _ActionPermissions:
        @staticmethod
        def filter(**_kwargs):
            raise AssertionError("uncontrolled role must not query action grants")

    monkeypatch.setattr("libs.security.authorization.ToolResources", _ToolResources)
    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _ActionPermissions)
    account = SimpleNamespace(
        id="user-1",
        roles=[
            SimpleNamespace(
                id="role-1",
                name="应用资源不受控",
                permissions=["tool:r", "tool:resource:uncontrolled"],
                tenant_id="t1",
            )
        ],
    )

    assert await check_action_execute_permission(
        account,
        "lion_mgmt",
        "get_events_list",
        "res-1",
        "t1",
    ) is True
    assert await check_action_execute_permission(
        account,
        "lion_mgmt",
        "get_events_list",
        "res-2",
        "t1",
    ) is False
    assert calls == [
        {
            "id__in": ["res-1"],
            "tool_name": "lion_mgmt",
            "deleted": False,
            "internal": False,
            "tenant_id": "t1",
        },
        {
            "id__in": ["res-2"],
            "tool_name": "lion_mgmt",
            "deleted": False,
            "internal": False,
            "tenant_id": "t1",
        },
    ]


@pytest.mark.asyncio
async def test_check_action_execute_permission_uncontrolled_marker_requires_tool_view(monkeypatch):
    class _Filter:
        async def all(self):
            return []

    class _ActionPermissions:
        @staticmethod
        def filter(**_kwargs):
            return _Filter()

    class _ToolResources:
        @staticmethod
        def filter(**_kwargs):
            raise AssertionError("missing tool:r must keep normal grant path")

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _ActionPermissions)
    monkeypatch.setattr("libs.security.authorization.ToolResources", _ToolResources)
    account = SimpleNamespace(
        id="user-1",
        roles=[
            SimpleNamespace(
                id="role-1",
                name="缺少应用市场查看",
                permissions=["tool:resource:uncontrolled"],
                tenant_id="t1",
            )
        ],
    )

    assert await check_action_execute_permission(
        account,
        "lion_mgmt",
        "get_events_list",
        "res-1",
        "t1",
    ) is False


@pytest.mark.asyncio
async def test_check_action_execute_permission_empty_resource_follows_model(monkeypatch):
    account = SimpleNamespace(id="user-1", roles=[SimpleNamespace(id="r1", name="普通", permissions=["timed:r"], tenant_id="t1")])

    async def resource_not_required(*_args):
        return False

    monkeypatch.setattr(
        "libs.security.authorization.tool_requires_resource",
        resource_not_required,
    )
    assert await check_action_execute_permission(account, "lion_mgmt", "some_action", "", "t1") is True
    assert await check_action_execute_permission(account, "lion_mgmt", "some_action", None, "t1") is True

    async def resource_required(*_args):
        return True

    monkeypatch.setattr(
        "libs.security.authorization.tool_requires_resource",
        resource_required,
    )
    assert await check_action_execute_permission(account, "lion_mgmt", "some_action", "", "t1") is False
    assert await check_action_execute_permission(account, "lion_mgmt", "some_action", ", ,", "t1") is False


@pytest.mark.asyncio
async def test_check_action_execute_permission_user_granted(monkeypatch):
    perm = _action_perm({"res-1": ["user-1"]}, {})

    class _Filter:
        def __init__(self, value):
            self._value = value

        async def all(self):
            return self._value

    class _AP:
        @staticmethod
        def filter(**kwargs):
            assert kwargs["tool_name"] == "lion_mgmt"
            assert set(kwargs["action_name__in"]) == {"get_events_list", "all"}
            return _Filter([perm])

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _AP)
    account = SimpleNamespace(id="user-1", roles=[SimpleNamespace(id="r1", name="普通", permissions=["timed:r"], tenant_id="t1")])
    ok = await check_action_execute_permission(account, "lion_mgmt", "get_events_list", "res-1", "t1")
    assert ok is True


@pytest.mark.asyncio
async def test_check_action_execute_permission_ignores_other_tenant_roles(monkeypatch):
    perm = _action_perm({}, {"res-1": ["role-other"]})

    class _Filter:
        async def all(self):
            return [perm]

    class _AP:
        @staticmethod
        def filter(**_kwargs):
            return _Filter()

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _AP)
    account = SimpleNamespace(
        id="user-1",
        roles=[SimpleNamespace(id="role-other", name="外租户角色", permissions=["timed:r"], tenant_id="tenant-b")],
    )

    ok = await check_action_execute_permission(account, "lion_mgmt", "get_events_list", "res-1", "tenant-a")

    assert ok is False


@pytest.mark.asyncio
async def test_check_action_execute_permission_denied_when_no_grant(monkeypatch):
    # 未授权资源应被拒绝——正是本次 bug 里"创建时不校验、看不到"的镜像语义
    perm = _action_perm({}, {})

    class _Filter:
        async def all(self):
            return [perm]

    class _AP:
        @staticmethod
        def filter(**_kwargs):
            return _Filter()

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _AP)
    account = SimpleNamespace(id="user-1", roles=[SimpleNamespace(id="r1", name="普通", permissions=["timed:c"], tenant_id="t1")])
    ok = await check_action_execute_permission(account, "lion_mgmt", "get_events_list", "res-1", "t1")
    assert ok is False


@pytest.mark.asyncio
async def test_check_action_execute_permission_multi_resource_all_required(monkeypatch):
    # 逗号分隔的多资源：任一缺权即整体失败
    perm = _action_perm({"res-1": ["user-1"]}, {})

    class _Filter:
        async def all(self):
            return [perm]

    class _AP:
        @staticmethod
        def filter(**_kwargs):
            return _Filter()

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _AP)
    account = SimpleNamespace(id="user-1", roles=[])
    # 只对 res-1 有权，res-2 无权 → 整体拒绝
    ok = await check_action_execute_permission(account, "lion_mgmt", "get_events_list", "res-1,res-2", "t1")
    assert ok is False


@pytest.mark.asyncio
async def test_check_action_execute_permission_all_action_grants_any(monkeypatch):
    # "all" 兜底记录：只要 all 记录里授权了资源就等同于对具体动作授权
    perm_all = _action_perm({"res-1": ["user-1"]}, {}, action_name="all")

    class _Filter:
        async def all(self):
            return [perm_all]

    class _AP:
        @staticmethod
        def filter(**kwargs):
            assert "all" in kwargs["action_name__in"]
            return _Filter()

    monkeypatch.setattr("libs.security.authorization.ActionPermissions", _AP)
    account = SimpleNamespace(id="user-1", roles=[])
    ok = await check_action_execute_permission(account, "lion_mgmt", "some_action", "res-1", "t1")
    assert ok is True
