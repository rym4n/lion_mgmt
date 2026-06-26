# coding=utf-8
import asyncio
import importlib.util
import io
from pathlib import Path
import sys
import types
import zipfile

import yaml


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_importer_stubs():
    transaction_events = []

    class TransactionContext:
        async def __aenter__(self):
            transaction_events.append(("enter", None))
            return self

        async def __aexit__(self, exc_type, exc, tb):
            transaction_events.append(("exit", exc_type.__name__ if exc_type else None))
            return False

    class ErrorCode:
        PACKAGE_INVALID = "PACKAGE_INVALID"
        PACKAGE_TYPE_UNSUPPORTED = "PACKAGE_TYPE_UNSUPPORTED"
        PACKAGE_VERSION_LOWER = "PACKAGE_VERSION_LOWER"
        PACKAGE_PARTIAL_FAILURE = "PACKAGE_PARTIAL_FAILURE"
        INVALID_LICENSE_ERROR = "INVALID_LICENSE_ERROR"
        PARAM_ERROR = "PARAM_ERROR"

    class CustomError(Exception):
        def __init__(self, enum):
            self.enum = enum

    class DigitalEmployeeRole:
        pass

    class DigitalEmployeeSkill:
        pass

    class Role:
        pass

    class Tool:
        records = {}

        @classmethod
        async def get_or_none(cls, **kwargs):
            key = (kwargs.get("name"), kwargs.get("version"), kwargs.get("tenant_id"))
            return cls.records.get(key)

    sys.modules.setdefault("core", types.ModuleType("core"))
    core_error_code = types.ModuleType("core.error_code")
    core_error_code.ErrorCode = ErrorCode
    sys.modules["core.error_code"] = core_error_code

    sys.modules.setdefault("libs", types.ModuleType("libs"))
    utils_module = types.ModuleType("libs.utils")
    utils_module.deleted_from_s3 = []
    async def delete_from_s3(bucket_name, file_name, prefix=""):
        utils_module.deleted_from_s3.append((bucket_name, file_name, prefix))
        return True
    utils_module.delete_from_s3 = delete_from_s3
    sys.modules["libs"].utils = utils_module
    sys.modules["libs.utils"] = utils_module
    sys.modules.setdefault("libs.error_code", types.ModuleType("libs.error_code"))
    exception_module = types.ModuleType("libs.error_code.exception")
    exception_module.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = exception_module
    security_module = types.ModuleType("libs.security")
    authorization_module = types.ModuleType("libs.security.authorization")
    authorization_module.load_license = lambda: {"moduleList": []}
    sys.modules["libs.security"] = security_module
    sys.modules["libs.security.authorization"] = authorization_module

    sys.modules.setdefault("models", types.ModuleType("models"))
    sys.modules["models"].Tool = Tool
    digital_employee_module = types.ModuleType("models.digital_employee")
    digital_employee_module.DigitalEmployeeRole = DigitalEmployeeRole
    digital_employee_module.DigitalEmployeeSkill = DigitalEmployeeSkill
    sys.modules["models.digital_employee"] = digital_employee_module
    user_module = types.ModuleType("models.user")
    user_module.Role = Role
    sys.modules["models.user"] = user_module

    settings_module = types.ModuleType("settings")
    settings_module.DIGITAL_EMPLOYEE_RESOURCES = str(Path("/tmp/digital-employee-test"))
    sys.modules["settings"] = settings_module

    sys.modules.setdefault("tortoise", types.ModuleType("tortoise"))
    transactions_module = types.ModuleType("tortoise.transactions")
    transactions_module.in_transaction = lambda: TransactionContext()
    transactions_module.transaction_events = transaction_events
    sys.modules["tortoise.transactions"] = transactions_module

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    digital_employee_pkg = types.ModuleType("services.digital_employee")
    digital_employee_pkg.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_pkg
    tools_module = types.ModuleType("services.tools")
    tools_module.__path__ = []
    tools_views_module = types.ModuleType("services.tools.views")
    async def upload_tool(zip_file, lic, creator, tenant_id, request):
        result = getattr(request, "upload_tool_result", None)
        if result is not None:
            if isinstance(result, list):
                return result.pop(0)
            return result
        return {"msg": "success", "tool_name": zip_file.name, "name": zip_file.name.removesuffix(".zip"), "version": "v1"}
    tools_views_module.upload_tool = upload_tool
    sys.modules["services.tools"] = tools_module
    sys.modules["services.tools.views"] = tools_views_module
    workflow_module = types.ModuleType("services.workflow")
    workflow_module.__path__ = []
    workflow_views_module = types.ModuleType("services.workflow.views")
    async def import_workflow(request):
        return None
    workflow_views_module.import_workflow = import_workflow
    sys.modules["services.workflow"] = workflow_module
    sys.modules["services.workflow.views"] = workflow_views_module


package_format = _load_module("services.digital_employee.package_format", "services/digital_employee/package_format.py")
inspect_package = package_format.inspect_package

_install_importer_stubs()
package_importer = _load_module("services.digital_employee.package_importer", "services/digital_employee/package_importer.py")
export_package = package_importer.export_package
_stage_files = package_importer._stage_files
_import_bundle = package_importer._import_bundle
import_package = package_importer.import_package
import_builtin_packages = package_importer.import_builtin_packages
CustomError = sys.modules["libs.error_code.exception"].CustomError
transaction_events = sys.modules["tortoise.transactions"].transaction_events
deleted_from_s3 = sys.modules["libs.utils"].deleted_from_s3


def _zip_with(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_builtin_role_default_skill_templates_exist():
    root = Path(__file__).resolve().parents[2]
    agent_root = root / "agent-template"
    skill_root = root / "skill-template"
    missing = []

    for role_dir in sorted(path for path in agent_root.iterdir() if path.is_dir()):
        package = yaml.safe_load((role_dir / "package.yaml").read_text(encoding="utf-8")) or {}
        role = yaml.safe_load((role_dir / "role.yaml").read_text(encoding="utf-8")) or {}
        package_skills = [str(skill) for skill in package.get("default_skill_keys") or []]
        role_skills = [str(skill) for skill in role.get("default_skills") or []]

        assert package_skills == role_skills
        for skill_key in package_skills:
            skill_dir = skill_root / skill_key
            if not (skill_dir / "package.yaml").exists() or not (skill_dir / "manifest.yaml").exists():
                missing.append(f"{role_dir.name}:{skill_key}")

    assert missing == []


def test_import_builtin_packages_imports_skills_before_roles(tmp_path):
    skill_dir = tmp_path / "skill-template" / "alert_triage"
    skill_dir.mkdir(parents=True)
    (skill_dir / "package.yaml").write_text(
        yaml.safe_dump({"type": "skill", "skill_key": "alert_triage", "version": 1}),
        encoding="utf-8",
    )
    (skill_dir / "manifest.yaml").write_text(
        yaml.safe_dump({"key": "alert_triage", "name": "Alert triage", "version": 1}),
        encoding="utf-8",
    )
    role_dir = tmp_path / "agent-template" / "monitor"
    role_dir.mkdir(parents=True)
    (role_dir / "package.yaml").write_text(
        yaml.safe_dump({"type": "role", "role_key": "monitor", "version": 1}),
        encoding="utf-8",
    )
    (role_dir / "role.yaml").write_text(
        yaml.safe_dump({"key": "monitor", "name": "Monitor", "version": 1, "default_skills": ["alert_triage"]}),
        encoding="utf-8",
    )

    calls = []
    original_agent_root = package_importer.BUILTIN_AGENT_TEMPLATE_ROOT
    original_skill_root = package_importer.BUILTIN_SKILL_TEMPLATE_ROOT
    original_import_package = package_importer.import_package

    async def fake_import_package(file_bytes, tenant_id, is_builtin=False, operator_id=None, request=None):
        package_type, package = inspect_package(file_bytes)
        package_key = package.get("skill_key") or package.get("role_key")
        calls.append((package_type, package_key, tenant_id, is_builtin, operator_id))
        return package_importer.ImportResult(package_type=package_type, imported=[{"package_key": package_key}])

    package_importer.BUILTIN_AGENT_TEMPLATE_ROOT = tmp_path / "agent-template"
    package_importer.BUILTIN_SKILL_TEMPLATE_ROOT = tmp_path / "skill-template"
    package_importer.import_package = fake_import_package
    try:
        result = asyncio.run(import_builtin_packages(operator_id="bootstrap"))
    finally:
        package_importer.BUILTIN_AGENT_TEMPLATE_ROOT = original_agent_root
        package_importer.BUILTIN_SKILL_TEMPLATE_ROOT = original_skill_root
        package_importer.import_package = original_import_package

    assert calls == [
        ("skill", "alert_triage", "__builtin__", True, "bootstrap"),
        ("role", "monitor", "__builtin__", True, "bootstrap"),
    ]
    assert [item["package_key"] for item in result.imported] == ["alert_triage", "monitor"]


def test_import_builtin_packages_skips_existing_newer_version(tmp_path):
    role_dir = tmp_path / "agent-template" / "monitor"
    role_dir.mkdir(parents=True)
    (role_dir / "package.yaml").write_text(
        yaml.safe_dump({"type": "role", "role_key": "monitor", "version": 1}),
        encoding="utf-8",
    )
    (role_dir / "role.yaml").write_text(yaml.safe_dump({"key": "monitor", "name": "Monitor"}), encoding="utf-8")
    skill_root = tmp_path / "skill-template"
    skill_root.mkdir()

    original_agent_root = package_importer.BUILTIN_AGENT_TEMPLATE_ROOT
    original_skill_root = package_importer.BUILTIN_SKILL_TEMPLATE_ROOT
    original_import_package = package_importer.import_package

    async def fake_import_package(*args, **kwargs):
        raise CustomError(package_importer.ErrorCode.PACKAGE_VERSION_LOWER)

    package_importer.BUILTIN_AGENT_TEMPLATE_ROOT = tmp_path / "agent-template"
    package_importer.BUILTIN_SKILL_TEMPLATE_ROOT = skill_root
    package_importer.import_package = fake_import_package
    try:
        result = asyncio.run(import_builtin_packages())
    finally:
        package_importer.BUILTIN_AGENT_TEMPLATE_ROOT = original_agent_root
        package_importer.BUILTIN_SKILL_TEMPLATE_ROOT = original_skill_root
        package_importer.import_package = original_import_package

    assert result.imported == []
    assert result.skipped == [{"package_key": "monitor", "reason": "existing package version is newer"}]


def test_inspect_role_package():
    package = _zip_with({"package.yaml": yaml.safe_dump({"type": "role", "role_key": "monitor"})})

    package_type, meta = inspect_package(package)

    assert package_type == "role"
    assert meta["role_key"] == "monitor"


def test_inspect_skill_package():
    package = _zip_with({"package.yaml": yaml.safe_dump({"type": "skill", "skill_key": "alert_triage"})})

    package_type, meta = inspect_package(package)

    assert package_type == "skill"
    assert meta["skill_key"] == "alert_triage"


def test_inspect_bundle_package():
    package = _zip_with({"package.yaml": yaml.safe_dump({"type": "bundle", "bundle_key": "builtin-monitor"})})

    package_type, meta = inspect_package(package)

    assert package_type == "bundle"
    assert meta["bundle_key"] == "builtin-monitor"


def test_stage_files_lists_nested_payload(tmp_path):
    (tmp_path / "playbooks" / "nested").mkdir(parents=True)
    (tmp_path / "playbooks" / "workflow.json").write_text("[]", encoding="utf-8")
    (tmp_path / "playbooks" / "nested" / "tool.zip").write_text("zip", encoding="utf-8")

    assert _stage_files(tmp_path / "playbooks") == ["nested/tool.zip", "workflow.json"]


def test_export_builtin_monitor_bundle_contains_role_and_skills():
    package = asyncio.run(export_package(role_key="monitor", bundle=True))

    package_type, meta = inspect_package(package)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())

    assert package_type == "bundle"
    assert meta["role_key"] == "monitor"
    assert "roles/monitor/package.yaml" in names
    assert "roles/monitor/role.yaml" in names
    assert "skills/alert_triage/package.yaml" in names
    assert "skills/shift_report_compose/manifest.yaml" in names


def test_export_bundle_requires_role_key():
    try:
        asyncio.run(export_package(skill_key="alert_triage", bundle=True))
    except CustomError as exc:
        assert exc.enum == "PARAM_ERROR"
    else:
        raise AssertionError("expected CustomError")


def test_export_bundle_fails_when_default_skill_template_is_missing(tmp_path):
    role_root = tmp_path / "agents"
    skill_root = tmp_path / "skills"
    monitor_root = role_root / "monitor"
    monitor_root.mkdir(parents=True)
    skill_root.mkdir()
    (monitor_root / "package.yaml").write_text(
        yaml.safe_dump({"type": "role", "role_key": "monitor", "default_skill_keys": ["missing_skill"]}),
        encoding="utf-8",
    )
    (monitor_root / "role.yaml").write_text(
        yaml.safe_dump({"key": "monitor", "name": "Monitor", "default_skills": ["missing_skill"]}),
        encoding="utf-8",
    )
    old_agent_root = package_importer.BUILTIN_AGENT_TEMPLATE_ROOT
    old_skill_root = package_importer.BUILTIN_SKILL_TEMPLATE_ROOT
    package_importer.BUILTIN_AGENT_TEMPLATE_ROOT = role_root
    package_importer.BUILTIN_SKILL_TEMPLATE_ROOT = skill_root
    try:
        try:
            asyncio.run(export_package(role_key="monitor", bundle=True))
        except CustomError as exc:
            assert exc.enum == "PACKAGE_INVALID"
        else:
            raise AssertionError("expected CustomError")
    finally:
        package_importer.BUILTIN_AGENT_TEMPLATE_ROOT = old_agent_root
        package_importer.BUILTIN_SKILL_TEMPLATE_ROOT = old_skill_root


def test_bundle_import_stages_playbooks_and_actions_without_request(tmp_path):
    root = tmp_path / "bundle"
    (root / "roles").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "actions").mkdir()
    (root / "playbooks").mkdir()
    (root / "actions" / "tool.zip").write_text("zip", encoding="utf-8")
    (root / "playbooks" / "workflow.json").write_text("[]", encoding="utf-8")

    result = asyncio.run(_import_bundle(root, {"type": "bundle"}, "tenant-1", False))

    assert result.imported == []
    assert {item["type"] for item in result.skipped} == {"actions", "playbooks"}


def test_bundle_import_uses_existing_import_flows_with_request(tmp_path):
    root = tmp_path / "bundle"
    (root / "roles").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "actions").mkdir()
    (root / "playbooks").mkdir()
    with zipfile.ZipFile(root / "actions" / "tool.zip", "w") as archive:
        archive.writestr("config.json", "{}")
    (root / "playbooks" / "workflow.json").write_text("[]", encoding="utf-8")
    request = types.SimpleNamespace(
        ctx=types.SimpleNamespace(session={"account_info": types.SimpleNamespace(username="tester"), "id": "session-1"}),
        app=types.SimpleNamespace(ctx=types.SimpleNamespace(cache_client=object())),
    )

    result = asyncio.run(_import_bundle(root, {"type": "bundle"}, "tenant-1", False, request=request))

    assert {"type": "action", "file": "tool.zip", "tool_name": "tool.zip"} in result.imported
    assert {"type": "playbook", "file": "playbooks.zip"} in result.imported
    assert result.skipped == []


def test_bundle_import_action_failure_aborts_inside_transaction(tmp_path):
    root = tmp_path / "bundle"
    role_root = root / "roles" / "monitor"
    role_root.mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "actions").mkdir()
    (root / "playbooks").mkdir()
    (role_root / "package.yaml").write_text(yaml.safe_dump({"type": "role", "role_key": "monitor"}), encoding="utf-8")
    with zipfile.ZipFile(root / "actions" / "tool.zip", "w") as archive:
        archive.writestr("config.json", "{}")
    request = types.SimpleNamespace(
        ctx=types.SimpleNamespace(session={"account_info": types.SimpleNamespace(username="tester"), "id": "session-1"}),
        app=types.SimpleNamespace(ctx=types.SimpleNamespace(cache_client=object())),
        upload_tool_result={"msg": "broken"},
    )
    original_upsert_role = package_importer._upsert_role
    calls = []

    async def fake_upsert_role(root_path, package, tenant_id, is_builtin):
        calls.append((root_path.name, tenant_id, is_builtin))
        return types.SimpleNamespace(role_key=package["role_key"], id="role-1")

    package_importer._upsert_role = fake_upsert_role
    transaction_events.clear()
    try:
        try:
            asyncio.run(_import_bundle(root, {"type": "bundle"}, "tenant-1", False, request=request))
        except CustomError as exc:
            assert exc.enum == "PACKAGE_PARTIAL_FAILURE"
        else:
            raise AssertionError("expected CustomError")
    finally:
        package_importer._upsert_role = original_upsert_role

    assert calls == [("monitor", "tenant-1", False)]
    assert transaction_events == [("enter", None), ("exit", "CustomError")]


def test_bundle_import_cleans_uploaded_action_artifacts_after_later_failure(tmp_path):
    root = tmp_path / "bundle"
    (root / "roles").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "actions").mkdir()
    (root / "playbooks").mkdir()
    with zipfile.ZipFile(root / "actions" / "first.zip", "w") as archive:
        archive.writestr("config.json", "{}")
    with zipfile.ZipFile(root / "actions" / "second.zip", "w") as archive:
        archive.writestr("config.json", "{}")
    package_importer.Tool.records = {
        ("first", "v1", "tenant-1"): types.SimpleNamespace(logo_path="/files/user/tools/first.ico"),
    }
    request = types.SimpleNamespace(
        ctx=types.SimpleNamespace(session={"account_info": types.SimpleNamespace(username="tester"), "id": "session-1"}),
        app=types.SimpleNamespace(ctx=types.SimpleNamespace(cache_client=object())),
        upload_tool_result=[
            {"msg": "success", "tool_name": "First", "name": "first", "version": "v1"},
            {"msg": "broken"},
        ],
    )
    deleted_from_s3.clear()
    try:
        try:
            asyncio.run(_import_bundle(root, {"type": "bundle"}, "tenant-1", False, request=request))
        except CustomError as exc:
            assert exc.enum == "PACKAGE_PARTIAL_FAILURE"
        else:
            raise AssertionError("expected CustomError")
    finally:
        package_importer.Tool.records = {}

    assert ("system", "first/v1.zip", "tools") in deleted_from_s3
    assert ("user", "first.ico", "tools") in deleted_from_s3


def test_import_package_restores_existing_resource_directory_on_failure(tmp_path):
    resources_root = tmp_path / "resources"
    package_importer.DIGITAL_EMPLOYEE_RESOURCES = str(resources_root)
    target_root = resources_root / "role" / "monitor"
    target_root.mkdir(parents=True)
    (target_root / "existing.txt").write_text("old", encoding="utf-8")
    package = _zip_with(
        {
            "package.yaml": yaml.safe_dump({"type": "role", "role_key": "monitor"}),
            "role.yaml": yaml.safe_dump({"key": "monitor", "name": "Monitor"}),
        }
    )
    original_upsert_role = package_importer._upsert_role

    async def fail_upsert_role(root_path, package, tenant_id, is_builtin):
        assert (root_path / "role.yaml").exists()
        raise CustomError("boom")

    package_importer._upsert_role = fail_upsert_role
    try:
        try:
            asyncio.run(import_package(package, tenant_id="tenant-1"))
        except CustomError as exc:
            assert exc.enum == "boom"
        else:
            raise AssertionError("expected CustomError")
    finally:
        package_importer._upsert_role = original_upsert_role

    assert (target_root / "existing.txt").read_text(encoding="utf-8") == "old"
    assert not (target_root / "role.yaml").exists()
