# coding=utf-8
import asyncio
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.digital_employee import views  # noqa: E402


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
        }


def _request(tenant_id="tenant-1", body=None):
    return SimpleNamespace(json=body or {}, ctx=SimpleNamespace(session={"tenant_id": tenant_id}))


def test_employee_dispatch_normalizes_rich_content_and_attachments_into_payload(monkeypatch):
    employee = SimpleNamespace(
        agent_key="ops-1",
        capabilities=["ops"],
    )
    captured = []

    async def fake_employee_or_404(tenant_id, employee_id):
        assert (tenant_id, employee_id) == ("tenant-1", "ops-1")
        return employee

    async def fake_create_command_task(tenant_id, account_info, data):
        captured.append((tenant_id, account_info, data))
        return SimpleNamespace(json={"id": "task-1"})

    monkeypatch.setattr(views, "_employee_or_404", fake_employee_or_404)
    monkeypatch.setattr(views, "create_command_task", fake_create_command_task)
    request = _request(
        body={
            "employee_id": "ops-1",
            "description": "检查图片",
            "content_html": "<p>检查 <strong>图片</strong></p>",
            "attachments": [{"file_id": "attach-1.png", "kind": "image"}],
        }
    )

    task = asyncio.run(views._dispatch_employee_task(request, "tenant-1", "operator-1"))

    assert task.json == {"id": "task-1"}
    assert captured[0][2]["payload"]["content_html"] == "<p>检查 <strong>图片</strong></p>"
    assert captured[0][2]["payload"]["attachments"] == [{"file_id": "attach-1.png", "kind": "image"}]


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
    )
    calls = []

    async def fake_employee_or_404(tenant_id, employee_id):
        assert tenant_id == "tenant-1"
        assert employee_id == "employee-1"
        return employee

    async def fake_get_visible_role(tenant_id, role_key):
        calls.append(("role", tenant_id, role_key))
        return role

    async def fake_get_visible_skills(tenant_id, skill_keys, enabled=True):
        calls.append(("skills", tenant_id, tuple(skill_keys), enabled))
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

    assert calls == [("role", "tenant-1", "monitor"), ("skills", "tenant-1", ("alert_triage",), None)]
    assert out["role"]["id"] == "role-tenant"
    assert out["role"]["meta"]["responsibilities"] == "tenant expert responsibilities"
    assert len(out["skills"]) == 1
    assert out["skills"][0]["description"] == "专家级告警分诊技能"


def test_skill_delete_checks_disabled_tenant_skill_usage_before_soft_delete(monkeypatch):
    lookup_calls = []
    usage_calls = []
    delete_calls = []

    skill = SimpleNamespace(skill_key="period_report_compose", deleted=False)

    async def fake_manageable_skill(tenant_id, skill_key):
        lookup_calls.append((tenant_id, skill_key))
        return skill

    async def fake_soft_delete(tenant_id, selected_skill):
        delete_calls.append((tenant_id, selected_skill))

    class UsageQuery:
        async def count(self):
            return 1

    class AgentModel:
        @classmethod
        def filter(cls, **kwargs):
            usage_calls.append(kwargs)
            return UsageQuery()

    monkeypatch.setattr(views, "_manageable_skill_or_404", fake_manageable_skill)
    monkeypatch.setattr(views, "_soft_delete_skill_for_tenant", fake_soft_delete)
    monkeypatch.setattr(views, "CommandAgent", AgentModel)

    with pytest.raises(views.CustomError) as exc_info:
        asyncio.run(
            inspect.unwrap(views.skill_delete)(
                _request(body={"skill_key": "period_report_compose"})
            )
        )

    assert exc_info.value.enum is views.ErrorCode.SKILL_IN_USE_BY_EMPLOYEES
    assert lookup_calls == [("tenant-1", "period_report_compose")]
    assert usage_calls == [
        {
            "tenant_id": "tenant-1",
            "skill_keys__contains": ["period_report_compose"],
            "deleted": False,
        }
    ]
    assert skill.deleted is False
    assert delete_calls == []


def test_builtin_skill_delete_creates_tenant_tombstone_without_mutating_baseline(monkeypatch):
    builtin = SimpleNamespace(
        tenant_id=views.BUILTIN_TENANT_ID,
        skill_key="alert_triage",
        name="告警分诊",
        description="内置技能",
        skill_dir="/data/__builtin__/alert_triage",
        version=3,
        enabled=True,
        deleted=False,
    )
    upserts = []

    async def fake_manageable_skill(tenant_id, skill_key):
        assert (tenant_id, skill_key) == ("tenant-1", "alert_triage")
        return builtin

    class UsageQuery:
        async def count(self):
            return 0

    class AgentModel:
        @classmethod
        def filter(cls, **kwargs):
            return UsageQuery()

    class SkillModel:
        @classmethod
        async def update_or_create(cls, **kwargs):
            upserts.append(kwargs)
            return SimpleNamespace(), True

    monkeypatch.setattr(views, "_manageable_skill_or_404", fake_manageable_skill)
    monkeypatch.setattr(views, "CommandAgent", AgentModel)
    monkeypatch.setattr(views, "DigitalEmployeeSkill", SkillModel)

    asyncio.run(views._delete_skill_for_tenant("tenant-1", "alert_triage"))

    assert builtin.deleted is False
    assert upserts == [
        {
            "tenant_id": "tenant-1",
            "skill_key": "alert_triage",
            "defaults": {
                "name": "告警分诊",
                "description": "内置技能",
                "skill_dir": "/data/__builtin__/alert_triage",
                "version": 3,
                "is_builtin": False,
                "enabled": True,
                "deleted": True,
            },
        }
    ]


def test_tenant_skill_delete_soft_deletes_owned_record():
    class Skill(SimpleNamespace):
        async def save(self, update_fields=None):
            self.saved_update_fields.append(update_fields)

    skill = Skill(tenant_id="tenant-1", deleted=False, saved_update_fields=[])

    asyncio.run(views._soft_delete_skill_for_tenant("tenant-1", skill))

    assert skill.deleted is True
    assert skill.saved_update_fields == [["deleted", "utime"]]


def test_skill_save_restores_tenant_tombstone_as_custom_skill(monkeypatch):
    builtin = SimpleNamespace(
        tenant_id=views.BUILTIN_TENANT_ID,
        skill_key="alert_triage",
        name="Builtin alert triage",
        description="builtin",
        skill_dir="/data/__builtin__/alert_triage",
        version=4,
        enabled=True,
        deleted=False,
    )
    tombstone = SimpleNamespace(
        tenant_id="tenant-1",
        skill_key="alert_triage",
        name="Old alert triage",
        description="old",
        skill_dir="/data/tenant-1/alert_triage",
        version=2,
        enabled=False,
        deleted=True,
    )
    permission_checks = []
    upserts = []

    class SkillModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            assert kwargs == {"skill_key": "alert_triage"}
            return FakeQuery([builtin, tombstone])

        @classmethod
        async def update_or_create(cls, **kwargs):
            upserts.append(kwargs)
            return SimpleNamespace(json={"skill_key": kwargs["skill_key"]}), False

    async def fake_require_save_permission(request, exists, create_permission, update_permission):
        permission_checks.append((exists, create_permission, update_permission))

    monkeypatch.setattr(views, "DigitalEmployeeSkill", SkillModel)
    monkeypatch.setattr(views, "_require_save_permission", fake_require_save_permission)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(
        views.skill_save(
            _request(
                body={
                    "skill_key": "alert_triage",
                    "name": "Restored alert triage",
                    "description": "tenant restored",
                    "files": [],
                }
            )
        )
    )

    assert out == {"skill_key": "alert_triage"}
    assert permission_checks == [
        (False, "digital_employee_skill:c", "digital_employee_skill:u")
    ]
    assert upserts[0]["tenant_id"] == "tenant-1"
    assert upserts[0]["defaults"]["is_builtin"] is False
    assert upserts[0]["defaults"]["deleted"] is False
    assert upserts[0]["defaults"]["enabled"] is False
    assert upserts[0]["defaults"]["version"] == 3


def test_skill_list_tenant_tombstone_hides_builtin_before_keyword_filter(monkeypatch):
    builtin = SimpleNamespace(
        tenant_id=views.BUILTIN_TENANT_ID,
        skill_key="alert_triage",
        name="Renamed builtin skill",
        description="new baseline description",
        is_builtin=True,
        deleted=False,
        json={"skill_key": "alert_triage"},
    )
    tombstone = SimpleNamespace(
        tenant_id="tenant-1",
        skill_key="alert_triage",
        name="Old builtin skill",
        description="old baseline description",
        is_builtin=False,
        deleted=True,
        json={"skill_key": "alert_triage"},
    )

    class SkillModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            return FakeQuery([builtin, tombstone])

    monkeypatch.setattr(views, "DigitalEmployeeSkill", SkillModel)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(
        inspect.unwrap(views.skill_list)(
            _request(body={"page": 1, "size": 20, "keyword": "renamed"})
        )
    )

    assert out["list"] == []
    assert out["total"] == 0


def test_package_list_tenant_tombstone_hides_builtin(monkeypatch):
    builtin = SimpleNamespace(
        tenant_id=views.BUILTIN_TENANT_ID,
        skill_key="alert_triage",
        is_builtin=True,
        deleted=False,
        json={"skill_key": "alert_triage"},
    )
    tombstone = SimpleNamespace(
        tenant_id="tenant-1",
        skill_key="alert_triage",
        is_builtin=False,
        deleted=True,
        json={"skill_key": "alert_triage"},
    )

    class RoleModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            return FakeQuery([])

    class SkillModel:
        @classmethod
        def filter(cls, *args, **kwargs):
            return FakeQuery([builtin, tombstone])

    monkeypatch.setattr(views, "DigitalEmployeeRole", RoleModel)
    monkeypatch.setattr(views, "DigitalEmployeeSkill", SkillModel)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(inspect.unwrap(views.package_list)(_request()))

    assert out == {"roles": [], "skills": []}


def test_derive_capabilities_excludes_aioe_cli_and_skill_meta():
    from services.digital_employee.manager import derive_capabilities

    role = SimpleNamespace(
        role_key="desktop_ops",
        meta={"capabilities": ["endpoint_compliance", "baseline_gap"]},
    )
    skills = [
        SimpleNamespace(skill_key="patch_dispatch"),
        SimpleNamespace(skill_key="endpoint_hardening"),
    ]

    caps = derive_capabilities(role, skills)

    assert "aioe-cli" not in caps
    assert "desktop_ops" in caps
    assert "patch_dispatch" in caps
    assert "baseline_gap" in caps
    assert caps == sorted(caps)


@pytest.mark.parametrize(
    ("submitted_config_id", "expected_config_id"),
    [("config-1", "config-1"), ("", None)],
)
def test_employee_create_forwards_llm_config_id_and_normalizes_blank_value(
    monkeypatch, submitted_config_id, expected_config_id
):
    employee = SimpleNamespace(id="employee-1", tenant_id="tenant-1")
    create_calls = []
    events = []

    async def fake_create_employee(**kwargs):
        create_calls.append(kwargs)
        return employee

    async def fake_publish(tenant_id, event, employee_id):
        events.append((tenant_id, event, employee_id))

    async def fake_payload(value):
        return {"id": value.id}

    monkeypatch.setattr(views, "create_employee", fake_create_employee)
    monkeypatch.setattr(views, "publish_command_center_update", fake_publish)
    monkeypatch.setattr(views, "_employee_payload", fake_payload)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(
        inspect.unwrap(views.employee_create)(
            _request(
                body={
                    "role_key": "ops",
                    "name": "Operations",
                    "llm_config_id": submitted_config_id,
                }
            )
        )
    )

    assert out == {"id": "employee-1"}
    assert create_calls[0]["llm_config_id"] == expected_config_id
    assert events == [("tenant-1", "employee_created", "employee-1")]


@pytest.mark.parametrize(
    ("submitted_config_id", "expected_config_id"),
    [("config-2", "config-2"), ("", None)],
)
def test_employee_update_delegates_llm_config_id_and_normalizes_blank_value(
    monkeypatch, submitted_config_id, expected_config_id
):
    employee = SimpleNamespace(id="employee-1", tenant_id="tenant-1")
    manager_calls = []
    events = []

    async def fake_employee_or_404(tenant_id, employee_id):
        assert (tenant_id, employee_id) == ("tenant-1", "employee-1")
        return employee

    async def fake_update_employee(value, **kwargs):
        manager_calls.append((value, kwargs))
        return value

    async def fake_publish(tenant_id, event, employee_id):
        events.append((tenant_id, event, employee_id))

    async def fake_payload(value):
        return {"id": value.id}

    monkeypatch.setattr(views, "_employee_or_404", fake_employee_or_404)
    monkeypatch.setattr(views, "update_employee", fake_update_employee)
    monkeypatch.setattr(views, "publish_command_center_update", fake_publish)
    monkeypatch.setattr(views, "_employee_payload", fake_payload)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(
        inspect.unwrap(views.employee_update)(
            _request(
                body={
                    "employee_id": "employee-1",
                    "name": "Updated operations",
                    "llm_config_id": submitted_config_id,
                }
            )
        )
    )

    assert out == {"id": "employee-1"}
    assert manager_calls == [
        (
            employee,
            {
                "name": "Updated operations",
                "llm_config_id": expected_config_id,
            },
        )
    ]
    assert events == [("tenant-1", "employee_updated", "employee-1")]


async def _async_value(value):
    return value
