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
    "core.maintenance",
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
    "services.digital_employee.skill_storage",
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

    class LlmModelConfig:
        """绑定校验（P6 Task 7 / D14）会按 config_id 查库。这些用例关心的是
        RBAC 与技能持久化，不是绑定校验本身，所以恒返回一条合法的 default
        租户记录，让校验放行。"""

        @staticmethod
        async def get_or_none(**kwargs):
            return SimpleNamespace(
                id=kwargs.get("id"),
                tenant_id="8bce552d-c27c-41d4-8a1b-8c5bf38dfe18",
                deleted=False,
                enabled=True,
            )

    class LionAgentClient:
        async def sync_workspace(
            self,
            workspace_id,
            files,
            role_version=None,
            skill_versions=None,
            tenant_id=None,
            employee_id=None,
        ):
            sync_calls.append(
                {
                    "workspace_id": workspace_id,
                    "files": files,
                    "role_version": role_version,
                    "skill_versions": skill_versions or {},
                    "tenant_id": tenant_id,
                    "employee_id": employee_id,
                }
            )
            return {"etag": "runtime-etag"}

    sys.modules["core"] = types.ModuleType("core")
    core_error_code = types.ModuleType("core.error_code")
    core_error_code.ErrorCode = ErrorCode
    sys.modules["core.error_code"] = core_error_code
    # resolver 起就从 core.maintenance 取 DEFAULT_TENANT_ID；stub 掉 core 后
    # 这个子模块也必须一并补上，否则 manager 的 import 链会断。
    core_maintenance = types.ModuleType("core.maintenance")
    core_maintenance.DEFAULT_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"
    sys.modules["core.maintenance"] = core_maintenance

    sys.modules["libs"] = types.ModuleType("libs")
    sys.modules["libs.error_code"] = types.ModuleType("libs.error_code")
    exception_module = types.ModuleType("libs.error_code.exception")
    exception_module.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = exception_module

    models_module = types.ModuleType("models")
    # manager 起就从 models 顶层导入 LlmModelConfig 做绑定校验（P6 Task 7）。
    models_module.LlmModelConfig = LlmModelConfig
    sys.modules["models"] = models_module
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
    prompt_builder_module.workspace_etag = lambda role, skills, employee: "expected-etag"
    prompt_builder_module.render_workspace_files = lambda employee, role, skills: {"system_prompt.md": "role prompt"}
    sys.modules["services.digital_employee.prompt_builder"] = prompt_builder_module

    rbac_module = types.ModuleType("services.digital_employee.rbac")
    rbac_module.require_existing_platform_role = lambda role_id, tenant_id: _async_value(None)
    sys.modules["services.digital_employee.rbac"] = rbac_module

    skill_storage_module = types.ModuleType("services.digital_employee.skill_storage")
    skill_storage_module.relative_skill_dir = lambda path: str(path)
    skill_storage_module.skill_storage_root = lambda: Path("/tmp/skills")
    skill_storage_module.tenant_skill_root = lambda tenant_id: Path("/tmp/skills") / tenant_id
    sys.modules["services.digital_employee.skill_storage"] = skill_storage_module

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
                "skill_versions": {"triage": 2},
                "tenant_id": "tenant-1",
                "employee_id": "employee-1",
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
        skill = SimpleNamespace(skill_key="triage", version=2)
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

        employee = asyncio.run(
            manager.create_employee(
                "tenant-1", "ops", "Ops", llm_config_id="llm-config-1"
            )
        )

        assert checked_roles == [None]
        assert created[0]["rbac_role_id"] is None
        assert created[0]["llm_config_id"] == "llm-config-1"
        assert created[0]["skill_keys"] == ["triage"]
        assert "aioe-cli" not in created[0]["capabilities"]
        assert employee.workspace_etag == "runtime-etag"
    finally:
        _restore_modules(original_modules)


def test_update_employee_persists_llm_binding_and_preserves_workspace_sync():
    sync_calls = []
    manager, original_modules = _load_manager(sync_calls)
    try:
        role = SimpleNamespace(role_key="ops", version=7)
        employee = FakeEmployee(
            id="employee-1",
            tenant_id="tenant-1",
            role_key="ops",
            skill_keys=[],
            workspace_etag="old-etag",
            workspace_synced_at=datetime.now(timezone.utc),
            llm_config_id="old-config",
            saved_update_fields=[],
        )

        async def fake_get_visible_role(tenant_id, role_key):
            return role

        async def fake_get_visible_skills(tenant_id, skill_keys):
            return []

        manager.get_visible_role = fake_get_visible_role
        manager.get_visible_skills = fake_get_visible_skills

        result = asyncio.run(
            manager.update_employee(
                employee,
                name="Updated",
                max_concurrent=0,
                llm_config_id=None,
            )
        )

        assert result is employee
        assert employee.name == "Updated"
        assert employee.max_concurrent == 1
        assert employee.llm_config_id is None
        assert employee.saved_update_fields[0] is None
        assert sync_calls[0]["workspace_id"] == "employee-1"
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
