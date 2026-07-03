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
        ROLE_NOT_FOUND = "ROLE_NOT_FOUND"

    class CustomError(Exception):
        def __init__(self, enum):
            self.enum = enum

    class DigitalEmployeeRole:
        pass

    class DigitalEmployeeSkill:
        pass

    class Role:
        records = {}

        def __init__(self, **kwargs):
            self.id = kwargs.get("id")
            self.name = kwargs.get("name")
            self.description = kwargs.get("description")
            self.permissions = kwargs.get("permissions")
            self.tenant_id = kwargs.get("tenant_id")
            self.deleted = kwargs.get("deleted", False)
            self.saved_update_fields = []

        @classmethod
        async def get_or_none(cls, **kwargs):
            role = cls.records.get(kwargs.get("id"))
            if role and kwargs.get("deleted") is not None and role.deleted != kwargs["deleted"]:
                return None
            return role

        @classmethod
        async def create(cls, **kwargs):
            role = cls(**kwargs)
            cls.records[role.id] = role
            return role

        @classmethod
        async def get_or_create(cls, id, defaults):
            if id in cls.records:
                return cls.records[id], False
            role = cls(id=id, **defaults)
            cls.records[id] = role
            return role, True

        async def save(self, update_fields=None):
            self.saved_update_fields.append(update_fields)
            Role.records[self.id] = self

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
rbac = _load_module("services.digital_employee.rbac", "services/digital_employee/rbac.py")
package_importer = _load_module("services.digital_employee.package_importer", "services/digital_employee/package_importer.py")
export_package = package_importer.export_package
export_package_for_tenant = package_importer.export_package_for_tenant
_stage_files = package_importer._stage_files
_import_bundle = package_importer._import_bundle
import_package = package_importer.import_package
import_builtin_packages = package_importer.import_builtin_packages
CustomError = sys.modules["libs.error_code.exception"].CustomError
Role = sys.modules["models.user"].Role
transaction_events = sys.modules["tortoise.transactions"].transaction_events
deleted_from_s3 = sys.modules["libs.utils"].deleted_from_s3


def _zip_with(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_builtin_templates_are_not_owned_by_lion_mgmt():
    root = Path(__file__).resolve().parents[2]
    assert not (root / "agent-template").exists()
    assert not (root / "skill-template").exists()


def test_normalize_role_prompt_fields_keeps_explicit_configurable_fields_only():
    normalized = package_importer.normalize_role_prompt_fields(
        {
            "identity": "监控值班员",
            "description": "旧描述不应保留",
            "responsibilities": "分诊告警",
            "principles": ["先确认影响范围", "结论可追溯"],
            "workflows": [{"scene": "告警分诊", "steps": ["读取告警", "输出结论"]}],
            "safety_rules": "不执行破坏性变更",
            "tools_notes": "只使用已绑定工具",
        }
    )

    assert "description" not in normalized
    assert normalized["identity"] == "监控值班员"
    assert normalized["responsibilities"] == "分诊告警"
    assert "- 先确认影响范围" in normalized["principles"]
    assert "### 告警分诊" in normalized["workflows"]
    assert "- 输出结论" in normalized["workflows"]
    assert normalized["safety_rules"] == "不执行破坏性变更"
    assert normalized["tools_notes"] == "只使用已绑定工具"


def test_import_builtin_packages_no_longer_reads_lion_mgmt_templates():
    result = asyncio.run(import_builtin_packages(operator_id="bootstrap"))

    assert result.package_type == "builtin"
    assert result.imported == []
    assert result.skipped == [
        {
            "reason": "builtin packages are managed by aioe_package",
            "operator_id": "bootstrap",
        }
    ]


def test_upsert_role_auto_creates_missing_default_platform_role(tmp_path):
    role_root = tmp_path / "monitor"
    role_root.mkdir()
    (role_root / "role.yaml").write_text(
        yaml.safe_dump(
            {
                "key": "monitor",
                "name": "AI 监控值班员",
                "identity": "你是 AIOE 的 AI 监控值班员。",
                "description": "接收监控告警并分诊。",
                "default_rbac_role": "monitor_dispatcher",
                "default_skills": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    package = {"type": "role", "role_key": "monitor", "version": 1}
    calls = []

    class StubDigitalEmployeeRole:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return None

        @classmethod
        async def update_or_create(cls, **kwargs):
            calls.append(kwargs)
            defaults = kwargs["defaults"]
            return types.SimpleNamespace(role_key=kwargs["role_key"], id="role-record-1", **defaults), True

    original_role_model = package_importer.DigitalEmployeeRole
    package_importer.DigitalEmployeeRole = StubDigitalEmployeeRole
    Role.records.clear()
    try:
        role = asyncio.run(package_importer._upsert_role(role_root, package, "tenant-1", False))
        created = Role.records.get("monitor_dispatcher")
    finally:
        package_importer.DigitalEmployeeRole = original_role_model
        Role.records.clear()

    platform_role = calls and calls[0]["defaults"]["default_rbac_role_id"]
    assert role.role_key == "monitor"
    assert platform_role == "monitor_dispatcher"
    assert "description" not in calls[0]["defaults"]
    assert "description" not in calls[0]["defaults"]["meta"]
    assert created is not None
    assert created.permissions == []
    assert created.tenant_id == "tenant-1"


def test_upsert_role_rejects_missing_identity(tmp_path):
    role_root = tmp_path / "monitor"
    role_root.mkdir()
    (role_root / "role.yaml").write_text(
        yaml.safe_dump({"key": "monitor", "name": "AI 监控值班员", "default_skills": []}, allow_unicode=True),
        encoding="utf-8",
    )

    try:
        asyncio.run(package_importer._upsert_role(role_root, {"type": "role", "role_key": "monitor"}, "tenant-1", False))
    except CustomError as exc:
        assert exc.enum == "PACKAGE_INVALID"
    else:
        raise AssertionError("missing role identity must be rejected")


def test_require_role_identity_rejects_blank_identity():
    try:
        package_importer.require_role_identity({"identity": "  "})
    except CustomError as exc:
        assert exc.enum == "PARAM_ERROR"
    else:
        raise AssertionError("blank role identity must be rejected")


def test_upsert_role_normalizes_prompt_fields_to_strings(tmp_path):
    role_root = tmp_path / "monitor"
    role_root.mkdir()
    (role_root / "role.yaml").write_text(
        yaml.safe_dump(
            {
                "key": "monitor",
                "name": "AI 监控值班员",
                "identity": ["监控值班员"],
                "principles": ["先确认影响范围"],
                "responsibilities": ["接收告警", "判断影响范围"],
                "workflows": [
                    {
                        "scene": "告警分诊",
                        "steps": ["读取告警源", "输出严重性"],
                    }
                ],
                "safety_rules": ["不直接执行破坏性变更"],
                "tools_notes": ["只使用授权工具"],
                "default_skills": [],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    calls = []

    class StubDigitalEmployeeRole:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return None

        @classmethod
        async def update_or_create(cls, **kwargs):
            calls.append(kwargs)
            defaults = kwargs["defaults"]
            return types.SimpleNamespace(role_key=kwargs["role_key"], id="role-record-1", **defaults), True

    original_role_model = package_importer.DigitalEmployeeRole
    package_importer.DigitalEmployeeRole = StubDigitalEmployeeRole
    try:
        asyncio.run(package_importer._upsert_role(role_root, {"type": "role", "role_key": "monitor"}, "tenant-1", False))
    finally:
        package_importer.DigitalEmployeeRole = original_role_model

    meta = calls[0]["defaults"]["meta"]
    for field in package_importer.ROLE_PROMPT_TEXT_FIELDS:
        assert isinstance(meta[field], str)
    assert "- 接收告警" in meta["responsibilities"]
    assert "### 告警分诊" in meta["workflows"]
    assert "- 输出严重性" in meta["workflows"]


def test_ensure_platform_role_creates_missing_role_without_granting_permissions():
    Role.records.clear()
    try:
        role, created = asyncio.run(
            rbac.ensure_platform_role(
                "monitor_dispatcher",
                {"name": "AI 监控值班员", "description": "接收监控告警并分诊。"},
                "tenant-1",
            )
        )

        assert created is True
        assert role.id == "monitor_dispatcher"
        assert role.tenant_id == "tenant-1"
        assert role.permissions == []
        assert role.name == "AI 监控值班员平台角色"
    finally:
        Role.records.clear()


def test_ensure_platform_role_keeps_existing_permissions():
    existing = Role(
        id="monitor_dispatcher",
        name="人工维护角色",
        description="keep",
        permissions=["digital_employee:r"],
        tenant_id="tenant-1",
    )
    Role.records = {"monitor_dispatcher": existing}
    try:
        role, created = asyncio.run(
            rbac.ensure_platform_role(
                "monitor_dispatcher",
                {"name": "AI 监控值班员", "description": "接收监控告警并分诊。"},
                "tenant-1",
            )
        )

        assert created is False
        assert role is existing
        assert role.name == "人工维护角色"
        assert role.permissions == ["digital_employee:r"]
    finally:
        Role.records.clear()


def test_ensure_platform_role_restores_deleted_role_with_empty_permissions():
    deleted_role = Role(
        id="monitor_dispatcher",
        name="旧角色",
        description="old",
        permissions=["*:*", "digital_employee:u"],
        tenant_id="tenant-1",
        deleted=True,
    )
    Role.records = {"monitor_dispatcher": deleted_role}
    try:
        role, created = asyncio.run(
            rbac.ensure_platform_role(
                "monitor_dispatcher",
                {"name": "AI 监控值班员", "description": "接收监控告警并分诊。"},
                "tenant-1",
            )
        )

        assert created is False
        assert role.deleted is False
        assert role.permissions == []
        assert role.saved_update_fields == [["deleted", "permissions", "name", "description", "utime"]]
    finally:
        Role.records.clear()


def test_bundle_rbac_roles_do_not_grant_permissions_from_package(tmp_path):
    rbac_root = tmp_path / "rbac_roles"
    rbac_root.mkdir()
    (rbac_root / "monitor.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "monitor_dispatcher",
                "name": "AI 监控值班员平台角色",
                "description": "包内声明权限不应生效",
                "permissions": ["*:*", "digital_employee:u"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    Role.records.clear()
    try:
        imported = asyncio.run(package_importer._upsert_rbac_roles(rbac_root))

        assert imported == [
            {
                "rbac_role_id": "monitor_dispatcher",
                "name": "AI 监控值班员平台角色",
                "created": True,
                "permissions": [],
            }
        ]
        assert Role.records["monitor_dispatcher"].permissions == []
    finally:
        Role.records.clear()


def test_require_existing_platform_role_rejects_missing_role():
    Role.records.clear()
    try:
        try:
            asyncio.run(rbac.require_existing_platform_role("missing-role", "tenant-1"))
        except CustomError as exc:
            assert exc.enum == "ROLE_NOT_FOUND"
        else:
            raise AssertionError("expected CustomError")
    finally:
        Role.records.clear()


def test_require_existing_platform_role_accepts_existing_tenant_role():
    existing = Role(
        id="monitor_dispatcher",
        name="人工维护角色",
        description="keep",
        permissions=[],
        tenant_id="tenant-1",
    )
    Role.records = {"monitor_dispatcher": existing}
    try:
        role = asyncio.run(rbac.require_existing_platform_role("monitor_dispatcher", "tenant-1"))

        assert role is existing
    finally:
        Role.records.clear()


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


def test_export_builtin_monitor_bundle_contains_role_and_skills(tmp_path):
    role_root = tmp_path / "resources" / "bundle" / "monitor" / "roles" / "monitor"
    alert_root = tmp_path / "resources" / "bundle" / "monitor" / "skills" / "alert_triage"
    shift_root = tmp_path / "resources" / "bundle" / "monitor" / "skills" / "shift_report_compose"
    role_root.mkdir(parents=True)
    alert_root.mkdir(parents=True)
    shift_root.mkdir(parents=True)
    (role_root / "package.yaml").write_text(
        yaml.safe_dump({"type": "role", "role_key": "monitor", "default_skill_keys": ["alert_triage", "shift_report_compose"]}),
        encoding="utf-8",
    )
    (role_root / "role.yaml").write_text(
        yaml.safe_dump({"key": "monitor", "name": "Monitor", "default_skills": ["alert_triage", "shift_report_compose"]}),
        encoding="utf-8",
    )
    for skill_root, skill_key in [(alert_root, "alert_triage"), (shift_root, "shift_report_compose")]:
        (skill_root / "package.yaml").write_text(
            yaml.safe_dump({"type": "skill", "skill_key": skill_key, "version": 1}),
            encoding="utf-8",
        )
        (skill_root / "manifest.yaml").write_text(
            yaml.safe_dump({"key": skill_key, "name": skill_key, "version": 1}),
            encoding="utf-8",
        )

    async def fake_get_visible_role(tenant_id, role_key):
        assert tenant_id == "__builtin__"
        assert role_key == "monitor"
        return types.SimpleNamespace(
            role_key="monitor",
            template_dir=str(role_root),
            meta={"default_skills": ["alert_triage", "shift_report_compose"]},
        )

    async def fake_get_visible_skills(tenant_id, skill_keys):
        assert tenant_id == "__builtin__"
        assert skill_keys == ["alert_triage", "shift_report_compose"]
        return [
            types.SimpleNamespace(skill_key="alert_triage", skill_dir=str(alert_root)),
            types.SimpleNamespace(skill_key="shift_report_compose", skill_dir=str(shift_root)),
        ]

    manager_module = types.ModuleType("services.digital_employee.manager")
    manager_module.get_visible_role = fake_get_visible_role
    manager_module.get_visible_skills = fake_get_visible_skills
    old_manager = sys.modules.get("services.digital_employee.manager")
    sys.modules["services.digital_employee.manager"] = manager_module
    try:
        package = asyncio.run(export_package(role_key="monitor", bundle=True))
    finally:
        if old_manager is None:
            sys.modules.pop("services.digital_employee.manager", None)
        else:
            sys.modules["services.digital_employee.manager"] = old_manager

    package_type, meta = inspect_package(package)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())

    assert package_type == "bundle"
    assert meta["role_key"] == "monitor"
    assert "roles/monitor/package.yaml" in names
    assert "roles/monitor/role.yaml" in names
    assert "skills/alert_triage/package.yaml" in names
    assert "skills/shift_report_compose/manifest.yaml" in names


def test_export_tenant_bundle_uses_imported_resource_dirs(tmp_path):
    role_root = tmp_path / "resources" / "bundle" / "monitor" / "roles" / "monitor"
    skill_root = tmp_path / "resources" / "bundle" / "monitor" / "skills" / "alert_triage"
    role_root.mkdir(parents=True)
    skill_root.mkdir(parents=True)
    (role_root / "package.yaml").write_text(
        yaml.safe_dump({"type": "role", "role_key": "monitor", "default_skill_keys": ["alert_triage"]}),
        encoding="utf-8",
    )
    (role_root / "role.yaml").write_text(
        yaml.safe_dump({"key": "monitor", "name": "Custom monitor", "default_skills": ["alert_triage"]}),
        encoding="utf-8",
    )
    (skill_root / "package.yaml").write_text(
        yaml.safe_dump({"type": "skill", "skill_key": "alert_triage", "version": 1}),
        encoding="utf-8",
    )
    (skill_root / "manifest.yaml").write_text(
        yaml.safe_dump({"key": "alert_triage", "name": "Custom alert triage", "version": 1}),
        encoding="utf-8",
    )

    async def fake_get_visible_role(tenant_id, role_key):
        assert tenant_id == "tenant-1"
        assert role_key == "monitor"
        return types.SimpleNamespace(
            role_key="monitor",
            template_dir=str(role_root),
            meta={"default_skills": ["alert_triage"]},
        )

    async def fake_get_visible_skills(tenant_id, skill_keys):
        assert tenant_id == "tenant-1"
        assert skill_keys == ["alert_triage"]
        return [types.SimpleNamespace(skill_key="alert_triage", skill_dir=str(skill_root))]

    manager_module = types.ModuleType("services.digital_employee.manager")
    manager_module.get_visible_role = fake_get_visible_role
    manager_module.get_visible_skills = fake_get_visible_skills
    old_manager = sys.modules.get("services.digital_employee.manager")
    sys.modules["services.digital_employee.manager"] = manager_module
    try:
        package = asyncio.run(export_package_for_tenant("tenant-1", role_key="monitor", bundle=True))
    finally:
        if old_manager is None:
            sys.modules.pop("services.digital_employee.manager", None)
        else:
            sys.modules["services.digital_employee.manager"] = old_manager

    package_type, meta = inspect_package(package)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())
        role_yaml = yaml.safe_load(archive.read("roles/monitor/role.yaml")) or {}
        skill_yaml = yaml.safe_load(archive.read("skills/alert_triage/manifest.yaml")) or {}

    assert package_type == "bundle"
    assert meta["role_key"] == "monitor"
    assert names == {
        "package.yaml",
        "roles/monitor/package.yaml",
        "roles/monitor/role.yaml",
        "skills/alert_triage/package.yaml",
        "skills/alert_triage/manifest.yaml",
    }
    assert role_yaml["name"] == "Custom monitor"
    assert skill_yaml["name"] == "Custom alert triage"


def test_export_bundle_requires_role_key():
    try:
        asyncio.run(export_package(skill_key="alert_triage", bundle=True))
    except CustomError as exc:
        assert exc.enum == "PARAM_ERROR"
    else:
        raise AssertionError("expected CustomError")


def test_export_bundle_fails_when_default_skill_template_is_missing(tmp_path):
    role_root = tmp_path / "resources" / "bundle" / "monitor" / "roles" / "monitor"
    role_root.mkdir(parents=True)
    (role_root / "package.yaml").write_text(
        yaml.safe_dump({"type": "role", "role_key": "monitor", "default_skill_keys": ["missing_skill"]}),
        encoding="utf-8",
    )
    (role_root / "role.yaml").write_text(
        yaml.safe_dump({"key": "monitor", "name": "Monitor", "default_skills": ["missing_skill"]}),
        encoding="utf-8",
    )

    async def fake_get_visible_role(tenant_id, role_key):
        assert tenant_id == "__builtin__"
        assert role_key == "monitor"
        return types.SimpleNamespace(
            role_key="monitor",
            template_dir=str(role_root),
            meta={"default_skills": ["missing_skill"]},
        )

    async def fake_get_visible_skills(tenant_id, skill_keys):
        assert tenant_id == "__builtin__"
        assert skill_keys == ["missing_skill"]
        raise CustomError("PACKAGE_INVALID")

    manager_module = types.ModuleType("services.digital_employee.manager")
    manager_module.get_visible_role = fake_get_visible_role
    manager_module.get_visible_skills = fake_get_visible_skills
    old_manager = sys.modules.get("services.digital_employee.manager")
    sys.modules["services.digital_employee.manager"] = manager_module
    try:
        try:
            asyncio.run(export_package(role_key="monitor", bundle=True))
        except CustomError as exc:
            assert exc.enum == "PACKAGE_INVALID"
        else:
            raise AssertionError("expected CustomError")
    finally:
        if old_manager is None:
            sys.modules.pop("services.digital_employee.manager", None)
        else:
            sys.modules["services.digital_employee.manager"] = old_manager


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
    target_root = resources_root / "tenant-1" / "role" / "monitor"
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


def test_import_package_separates_builtin_and_tenant_resource_dirs(tmp_path):
    resources_root = tmp_path / "resources"
    package_importer.DIGITAL_EMPLOYEE_RESOURCES = str(resources_root)
    package = _zip_with(
        {
            "package.yaml": yaml.safe_dump({"type": "skill", "skill_key": "alert_triage", "version": 1}),
            "manifest.yaml": yaml.safe_dump({"key": "alert_triage", "name": "Alert triage", "version": 1}),
        }
    )
    original_upsert_skill = package_importer._upsert_skill
    calls = []

    async def fake_upsert_skill(root_path, package, tenant_id, is_builtin):
        calls.append((root_path, tenant_id, is_builtin))
        return types.SimpleNamespace(skill_key=package["skill_key"], id=f"{tenant_id}:{package['skill_key']}")

    package_importer._upsert_skill = fake_upsert_skill
    try:
        asyncio.run(import_package(package, tenant_id="tenant-1"))
        (resources_root / "tenant-1" / "skill" / "alert_triage" / "tenant.txt").write_text("custom", encoding="utf-8")
        asyncio.run(import_package(package, tenant_id="tenant-1", is_builtin=True))
    finally:
        package_importer._upsert_skill = original_upsert_skill

    assert calls == [
        (resources_root / "tenant-1" / "skill" / "alert_triage", "tenant-1", False),
        (resources_root / "__builtin__" / "skill" / "alert_triage", "__builtin__", True),
    ]
    assert (resources_root / "tenant-1" / "skill" / "alert_triage" / "tenant.txt").read_text(encoding="utf-8") == "custom"
    assert (resources_root / "__builtin__" / "skill" / "alert_triage" / "manifest.yaml").exists()
