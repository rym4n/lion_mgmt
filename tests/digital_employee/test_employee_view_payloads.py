# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

from services.digital_employee import views


class FakeQuery:
    def __init__(self, values):
        self.values = list(values)
        self._offset = 0
        self._limit = None

    async def count(self):
        return len(self.values)

    def order_by(self, *args):
        return self

    def offset(self, value):
        self._offset = value
        return self

    def limit(self, value):
        self._limit = value
        return self

    async def all(self):
        values = self.values[self._offset :]
        if self._limit is not None:
            values = values[: self._limit]
        return values


class FakeEmployee(SimpleNamespace):
    @property
    def json(self):
        return {
            "id": self.id,
            "agent_key": self.agent_key,
            "name": self.name,
            "description": "legacy command-agent description",
            "role_key": self.role_key,
            "skill_keys": self.skill_keys,
            "rbac_role_id": self.rbac_role_id,
            "max_concurrent": self.max_concurrent,
        }


class FakeRole(SimpleNamespace):
    @property
    def json(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "role_key": self.role_key,
            "name": self.name,
            "is_builtin": self.is_builtin,
            "meta": self.meta,
        }


class FakeSkill(SimpleNamespace):
    @property
    def json(self):
        return {
            "skill_key": self.skill_key,
            "name": self.name,
            "description": self.description,
            "bindings": self.bindings,
            "meta": self.meta,
        }


def _request(tenant_id="tenant-1", body=None):
    return SimpleNamespace(json=body or {}, ctx=SimpleNamespace(session={"tenant_id": tenant_id}))


def test_employee_list_payload_attaches_visible_role_meta(monkeypatch):
    employee = FakeEmployee(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        name="AI 监控值班员",
        role_key="monitor",
        skill_keys=["alert_triage"],
        rbac_role_id="monitor_dispatcher",
        max_concurrent=1,
    )
    builtin_role = FakeRole(
        id="role-builtin",
        tenant_id=views.BUILTIN_TENANT_ID,
        role_key="monitor",
        name="Builtin Monitor",
        is_builtin=True,
        meta={"responsibilities": "builtin responsibilities", "tools_notes": ""},
    )
    tenant_role = FakeRole(
        id="role-tenant",
        tenant_id="tenant-1",
        role_key="monitor",
        name="Tenant Monitor",
        is_builtin=False,
        meta={"responsibilities": "tenant expert responsibilities", "tools_notes": "tenant tool notes"},
    )

    class AgentModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            return FakeQuery([employee])

    class RoleModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            assert kwargs["role_key__in"] == ["monitor"]
            assert kwargs["enabled"] is True
            return FakeQuery([builtin_role, tenant_role])

    monkeypatch.setattr(views, "CommandAgent", AgentModel)
    monkeypatch.setattr(views, "DigitalEmployeeRole", RoleModel)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(inspect.unwrap(views.employee_list)(_request(body={"page": 1, "size": 20})))

    assert out["total"] == 1
    row = out["list"][0]
    assert "description" not in row
    assert row["role"]["id"] == "role-tenant"
    assert row["role"]["meta"]["responsibilities"] == "tenant expert responsibilities"
    assert row["role"]["meta"]["tools_notes"] == "tenant tool notes"


def test_employee_detail_resolves_visible_role_instead_of_employee_foreign_key(monkeypatch):
    employee = FakeEmployee(
        id="employee-1",
        tenant_id="tenant-1",
        agent_key="monitor-1",
        name="AI 监控值班员",
        role_key="monitor",
        skill_keys=["alert_triage"],
        rbac_role_id="monitor_dispatcher",
        max_concurrent=1,
    )
    role = FakeRole(
        id="role-tenant",
        tenant_id="tenant-1",
        role_key="monitor",
        name="Tenant Monitor",
        is_builtin=False,
        meta={"responsibilities": "tenant expert responsibilities"},
    )
    skill = FakeSkill(
        skill_key="alert_triage",
        name="告警分诊",
        description="专家级告警分诊技能",
        bindings={"tool_groups": ["monitoring", "logs"]},
        meta={"checklist": ["确认影响范围"]},
    )
    calls = []

    async def fake_employee_or_404(tenant_id, employee_id):
        assert tenant_id == "tenant-1"
        assert employee_id == "employee-1"
        return employee

    async def fake_get_visible_role(tenant_id, role_key):
        calls.append(("role", tenant_id, role_key))
        return role

    async def fake_get_visible_skills(tenant_id, skill_keys):
        calls.append(("skills", tenant_id, tuple(skill_keys)))
        return [skill]

    class TaskModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            return FakeQuery([])

    monkeypatch.setattr(views, "_employee_or_404", fake_employee_or_404)
    monkeypatch.setattr(views, "get_visible_role", fake_get_visible_role)
    monkeypatch.setattr(views, "get_visible_skills", fake_get_visible_skills)
    monkeypatch.setattr(views, "CommandTask", TaskModel)
    monkeypatch.setattr(views, "_employee_token_usage", lambda employee: _async_value({"total_tokens": 0}))
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(inspect.unwrap(views.employee_detail)(_request(body={"employee_id": "employee-1"})))

    assert calls == [("role", "tenant-1", "monitor"), ("skills", "tenant-1", ("alert_triage",))]
    assert out["role"]["id"] == "role-tenant"
    assert out["role"]["meta"]["responsibilities"] == "tenant expert responsibilities"
    assert out["skills"][0]["description"] == "专家级告警分诊技能"


async def _async_value(value):
    return value
