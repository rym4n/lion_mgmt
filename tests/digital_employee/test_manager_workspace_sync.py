# coding=utf-8
import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


STUB_MODULES = [
    "core",
    "core.error_code",
    "libs",
    "libs.error_code",
    "libs.error_code.exception",
    "models",
    "models.command_center",
    "models.digital_employee",
    "settings",
    "tortoise",
    "tortoise.expressions",
    "services",
    "services.digital_employee",
    "services.digital_employee.aioe_credential_manager",
    "services.digital_employee.lion_agent_client",
    "services.digital_employee.prompt_builder",
    "services.digital_employee.rbac",
    "services.digital_employee.manager_workspace_sync_test",
]
_MISSING_MODULE = object()


def _load_manager(sync_calls: list[dict]):
    original_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in STUB_MODULES}
    for name in STUB_MODULES:
        sys.modules.pop(name, None)

    class ErrorCode:
        DATA_ALREADY_EXISTS = "DATA_ALREADY_EXISTS"
        DIGITAL_EMPLOYEE_ROLE_NOT_FOUND = "DIGITAL_EMPLOYEE_ROLE_NOT_FOUND"
        DIGITAL_EMPLOYEE_SKILL_NOT_FOUND = "DIGITAL_EMPLOYEE_SKILL_NOT_FOUND"
        EMPLOYEE_HAS_PENDING_TASKS = "EMPLOYEE_HAS_PENDING_TASKS"

    class CustomError(Exception):
        def __init__(self, enum):
            self.enum = enum

    class Q:
        def __init__(self, *args, **kwargs):
            pass

        def __and__(self, other):
            return self

        def __or__(self, other):
            return self

    class CommandAgent:
        pass

    class CommandTask:
        pass

    class DigitalEmployeeRole:
        pass

    class DigitalEmployeeSkill:
        pass

    class LionAgentClient:
        async def sync_workspace(self, workspace_id, files, role_version=None, skill_versions=None):
            sync_calls.append(
                {
                    "workspace_id": workspace_id,
                    "files": files,
                    "role_version": role_version,
                    "skill_versions": skill_versions or {},
                }
            )
            return {"etag": "runtime-etag"}

    sys.modules["core"] = types.ModuleType("core")
    core_error_code = types.ModuleType("core.error_code")
    core_error_code.ErrorCode = ErrorCode
    sys.modules["core.error_code"] = core_error_code

    sys.modules["libs"] = types.ModuleType("libs")
    sys.modules["libs.error_code"] = types.ModuleType("libs.error_code")
    exception_module = types.ModuleType("libs.error_code.exception")
    exception_module.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = exception_module

    sys.modules["models"] = types.ModuleType("models")
    command_center_module = types.ModuleType("models.command_center")
    command_center_module.CommandAgent = CommandAgent
    command_center_module.CommandTask = CommandTask
    sys.modules["models.command_center"] = command_center_module
    digital_employee_module = types.ModuleType("models.digital_employee")
    digital_employee_module.DigitalEmployeeRole = DigitalEmployeeRole
    digital_employee_module.DigitalEmployeeSkill = DigitalEmployeeSkill
    sys.modules["models.digital_employee"] = digital_employee_module

    settings_module = types.ModuleType("settings")
    settings_module.TZ = timezone.utc
    sys.modules["settings"] = settings_module

    sys.modules["tortoise"] = types.ModuleType("tortoise")
    expressions_module = types.ModuleType("tortoise.expressions")
    expressions_module.Q = Q
    sys.modules["tortoise.expressions"] = expressions_module

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    digital_employee_pkg = types.ModuleType("services.digital_employee")
    digital_employee_pkg.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_pkg

    aioe_credential_manager = types.ModuleType("services.digital_employee.aioe_credential_manager")
    aioe_credential_manager.create_employee_api_account = lambda employee: _async_value(None)
    sys.modules["services.digital_employee.aioe_credential_manager"] = aioe_credential_manager

    lion_agent_client_module = types.ModuleType("services.digital_employee.lion_agent_client")
    lion_agent_client_module.LionAgentClient = LionAgentClient
    sys.modules["services.digital_employee.lion_agent_client"] = lion_agent_client_module

    prompt_builder_module = types.ModuleType("services.digital_employee.prompt_builder")
    prompt_builder_module.AIOE_CLI_SKILL_KEY = "aioe-cli"
    prompt_builder_module.AIOE_CLI_SKILL_VERSION = 1
    prompt_builder_module.workspace_etag = lambda role, skills, employee: "expected-etag"
    prompt_builder_module.render_workspace_files = lambda employee, role, skills: {"system_prompt.md": "role prompt"}
    sys.modules["services.digital_employee.prompt_builder"] = prompt_builder_module

    rbac_module = types.ModuleType("services.digital_employee.rbac")
    rbac_module.require_existing_platform_role = lambda role_id, tenant_id: _async_value(None)
    sys.modules["services.digital_employee.rbac"] = rbac_module

    path = Path(__file__).resolve().parents[2] / "services/digital_employee/manager.py"
    spec = importlib.util.spec_from_file_location("services.digital_employee.manager_workspace_sync_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.digital_employee.manager_workspace_sync_test"] = module
    spec.loader.exec_module(module)
    return module, original_modules


def _restore_modules(original_modules):
    for name in STUB_MODULES:
        original = original_modules[name]
        if original is _MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


async def _async_value(value):
    return value


class FakeEmployee(SimpleNamespace):
    async def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)


def test_sync_employee_workspace_force_bypasses_matching_etag_shortcut():
    sync_calls = []
    manager, original_modules = _load_manager(sync_calls)
    try:
        role = SimpleNamespace(role_key="ops", version=7)
        skill = SimpleNamespace(skill_key="triage", version=2)
        employee = FakeEmployee(
            id="employee-1",
            tenant_id="tenant-1",
            skill_keys=["triage"],
            workspace_etag="expected-etag",
            workspace_synced_at=datetime.now(timezone.utc),
            saved_update_fields=[],
        )

        result = asyncio.run(manager.sync_employee_workspace(employee, role=role, skills=[skill], force=True))

        assert result is employee
        assert sync_calls == [
            {
                "workspace_id": "employee-1",
                "files": {"system_prompt.md": "role prompt"},
                "role_version": 7,
                "skill_versions": {"triage": 2, "aioe-cli": 1},
            }
        ]
        assert employee.workspace_etag == "runtime-etag"
        assert employee.saved_update_fields == [["workspace_etag", "workspace_synced_at", "utime"]]
    finally:
        _restore_modules(original_modules)


def test_sync_employee_workspace_resolves_visible_role_by_role_key():
    sync_calls = []
    manager, original_modules = _load_manager(sync_calls)
    try:
        visible_role = SimpleNamespace(role_key="ops", version=9)
        stale_role = SimpleNamespace(role_key="ops", version=1)
        skill = SimpleNamespace(skill_key="triage", version=2)
        employee = FakeEmployee(
            id="employee-1",
            tenant_id="tenant-1",
            role_key="ops",
            skill_keys=["triage"],
            workspace_etag="old-etag",
            workspace_synced_at=datetime.now(timezone.utc),
            saved_update_fields=[],
            role=stale_role,
        )
        calls = []

        async def fake_get_visible_role(tenant_id, role_key):
            calls.append((tenant_id, role_key))
            return visible_role

        async def fake_get_visible_skills(tenant_id, skill_keys):
            assert tenant_id == "tenant-1"
            assert skill_keys == ["triage"]
            return [skill]

        manager.get_visible_role = fake_get_visible_role
        manager.get_visible_skills = fake_get_visible_skills

        result = asyncio.run(manager.sync_employee_workspace(employee))

        assert result is employee
        assert calls == [("tenant-1", "ops")]
        assert sync_calls[0]["role_version"] == 9
    finally:
        _restore_modules(original_modules)


def test_create_employee_does_not_inherit_role_default_rbac_and_persists_only_selected_skills():
    sync_calls = []
    manager, original_modules = _load_manager(sync_calls)
    try:
        role = SimpleNamespace(
            role_key="ops",
            version=7,
            default_skill_keys=["triage"],
            default_rbac_role_id="role-from-package",
            meta={"capabilities": ["ops"]},
        )
        skill = SimpleNamespace(skill_key="triage", version=2, meta={}, bindings={})
        created = []
        checked_roles = []

        async def fake_get_visible_role(tenant_id, role_key):
            return role

        async def fake_get_visible_skills(tenant_id, skill_keys):
            assert skill_keys == ["triage"]
            return [skill]

        async def fake_require_existing_platform_role(role_id, tenant_id):
            checked_roles.append(role_id)

        class AgentModel:
            @classmethod
            async def get_or_none(cls, **kwargs):
                return None

            @classmethod
            async def create(cls, **kwargs):
                created.append(kwargs)
                return FakeEmployee(id="employee-1", workspace_etag=None, workspace_synced_at=None, saved_update_fields=[], **kwargs)

        manager.get_visible_role = fake_get_visible_role
        manager.get_visible_skills = fake_get_visible_skills
        manager.require_existing_platform_role = fake_require_existing_platform_role
        manager.CommandAgent = AgentModel

        employee = asyncio.run(manager.create_employee("tenant-1", "ops", "Ops"))

        assert checked_roles == [None]
        assert created[0]["rbac_role_id"] is None
        assert created[0]["skill_keys"] == ["triage"]
        assert "aioe-cli" in created[0]["capabilities"]
        assert employee.workspace_etag == "runtime-etag"
    finally:
        _restore_modules(original_modules)


def test_sync_employee_workspace_skips_matching_etag_without_force():
    sync_calls = []
    manager, original_modules = _load_manager(sync_calls)
    try:
        role = SimpleNamespace(role_key="ops", version=7)
        skill = SimpleNamespace(skill_key="triage", version=2)
        employee = FakeEmployee(
            id="employee-1",
            tenant_id="tenant-1",
            skill_keys=["triage"],
            workspace_etag="expected-etag",
            workspace_synced_at=datetime.now(timezone.utc),
            saved_update_fields=[],
        )

        result = asyncio.run(manager.sync_employee_workspace(employee, role=role, skills=[skill]))

        assert result is employee
        assert sync_calls == []
        assert employee.saved_update_fields == []
    finally:
        _restore_modules(original_modules)


def test_prefer_tenant_records_keeps_tenant_customization_over_builtin_baseline():
    manager, original_modules = _load_manager([])
    try:
        builtin = SimpleNamespace(tenant_id="__builtin__", skill_key="alert_triage", name="builtin")
        customized = SimpleNamespace(tenant_id="tenant-1", skill_key="alert_triage", name="customized")
        other_builtin = SimpleNamespace(tenant_id="__builtin__", skill_key="vuln_scan", name="vuln")

        preferred = manager.prefer_tenant_records([builtin, other_builtin, customized], "tenant-1", "skill_key")

        assert preferred == [customized, other_builtin]
    finally:
        _restore_modules(original_modules)


def test_get_visible_skills_supplies_builtin_aioe_cli_without_database_row():
    manager, original_modules = _load_manager([])
    try:
        calls = []
        stored_skill = SimpleNamespace(
            tenant_id="tenant-1",
            skill_key="triage",
            name="Triage",
            enabled=True,
            meta={},
            bindings={},
        )

        class Query:
            async def all(self):
                return [stored_skill]

        class SkillModel:
            @classmethod
            def filter(cls, *args, **kwargs):
                calls.append(kwargs)
                return Query()

        manager.DigitalEmployeeSkill = SkillModel

        skills = asyncio.run(manager.get_visible_skills("tenant-1", ["triage", "aioe-cli"]))

        assert calls == [{"skill_key__in": ["triage"]}]
        assert [skill.skill_key for skill in skills] == ["triage", "aioe-cli"]
        assert skills[1].json["skill_key"] == "aioe-cli"
        assert skills[1].json["bindings"]["aioe_api"]["requires_rbac_role"] is True
    finally:
        _restore_modules(original_modules)


def test_get_visible_skills_applies_tenant_preference_before_enabled_filter():
    manager, original_modules = _load_manager([])
    try:
        builtin = SimpleNamespace(tenant_id="__builtin__", skill_key="triage", enabled=True)
        tenant_override = SimpleNamespace(tenant_id="tenant-1", skill_key="triage", enabled=False)

        class Query:
            async def all(self):
                return [builtin, tenant_override]

        class SkillModel:
            @classmethod
            def filter(cls, *args, **kwargs):
                assert kwargs == {"skill_key__in": ["triage"]}
                return Query()

        manager.DigitalEmployeeSkill = SkillModel

        manageable = asyncio.run(manager.get_visible_skills("tenant-1", ["triage"], enabled=None))
        assert manageable == [tenant_override]

        try:
            asyncio.run(manager.get_visible_skills("tenant-1", ["triage"]))
        except manager.CustomError as exc:
            assert exc.enum == manager.ErrorCode.DIGITAL_EMPLOYEE_SKILL_NOT_FOUND
        else:
            raise AssertionError("disabled tenant override must hide the enabled builtin baseline")
    finally:
        _restore_modules(original_modules)


def test_get_visible_skills_applies_tenant_tombstone_before_deleted_filter():
    manager, original_modules = _load_manager([])
    try:
        builtin = SimpleNamespace(
            tenant_id="__builtin__",
            skill_key="triage",
            enabled=True,
            deleted=False,
        )
        tenant_tombstone = SimpleNamespace(
            tenant_id="tenant-1",
            skill_key="triage",
            enabled=True,
            deleted=True,
        )

        class Query:
            async def all(self):
                return [builtin, tenant_tombstone]

        class SkillModel:
            @classmethod
            def filter(cls, *args, **kwargs):
                assert kwargs == {"skill_key__in": ["triage"]}
                return Query()

        manager.DigitalEmployeeSkill = SkillModel

        try:
            asyncio.run(manager.get_visible_skills("tenant-1", ["triage"], enabled=None))
        except manager.CustomError as exc:
            assert exc.enum == manager.ErrorCode.DIGITAL_EMPLOYEE_SKILL_NOT_FOUND
        else:
            raise AssertionError("tenant tombstone must hide the builtin baseline")
    finally:
        _restore_modules(original_modules)
