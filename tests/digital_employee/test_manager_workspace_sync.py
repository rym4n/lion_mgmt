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
            }
        ]
        assert employee.workspace_etag == "runtime-etag"
        assert employee.saved_update_fields == [["workspace_etag", "workspace_synced_at", "utime"]]
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
