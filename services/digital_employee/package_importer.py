# coding=utf-8
import io
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.digital_employee import DigitalEmployeeRole, DigitalEmployeeSkill
from models import Tool
from settings import DIGITAL_EMPLOYEE_RESOURCES
from .package_format import PackageFormatError, UnsupportedPackageTypeError, inspect_package

ROLE_PROMPT_TEXT_FIELDS = ("identity", "responsibilities", "workflows", "tools_notes")
AIOE_CLI_SKILL_KEY = "aioe-cli"
AIOE_CLI_SKILL_DESCRIPTION = (
    "内置 AIOE 平台操作技能。负责读取当前员工的 AIOE 平台 RBAC 角色授权，"
    "按授权调用 AIOE API 完成资产、告警、任务、剧本、报告、应用和系统配置等平台操作；"
    "未配置 RBAC 角色时不得调用任何 AIOE API。"
)
RBAC_PACKAGE_FIELDS = (
    "default_rbac_role",
    "default_rbac_role_id",
    "default_platform_role",
    "rbac_role",
    "rbac_role_id",
    "rbac_roles",
)


@dataclass
class ImportResult:
    package_type: str
    imported: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def json(self) -> dict:
        return {"package_type": self.package_type, "imported": self.imported, "skipped": self.skipped}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    return data


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CustomError(ErrorCode.PACKAGE_INVALID) from exc


def _strip_rbac_fields(data: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(data or {})
    for field in RBAC_PACKAGE_FIELDS:
        sanitized.pop(field, None)
    return sanitized


def _format_prompt_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                scene = item.get("scene")
                steps = item.get("steps")
                if scene:
                    lines.append(f"### {scene}")
                if isinstance(steps, (list, tuple)):
                    lines.extend(f"- {step}" for step in steps if step)
                elif steps:
                    lines.append(str(steps))
                extra = {key: val for key, val in item.items() if key not in {"scene", "steps"}}
                if extra:
                    lines.append(json.dumps(extra, ensure_ascii=False, sort_keys=True))
                if not scene and not steps and not extra:
                    lines.append(str(item))
                continue
            lines.append(f"- {item}")
        return "\n".join(lines)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def _merge_workflow_text(principles: str, workflows: str) -> str:
    principles = (principles or "").strip()
    workflows = (workflows or "").strip()
    if principles and workflows:
        if principles in workflows:
            return workflows
        return f"### 判断原则\n{principles}\n\n{workflows}"
    return workflows or principles


def normalize_role_prompt_fields(role_data: dict[str, Any]) -> dict[str, Any]:
    normalized = _strip_rbac_fields(role_data or {})
    normalized.pop("description", None)
    principles = _format_prompt_text(normalized.pop("principles", None))
    workflows = _format_prompt_text(normalized.get("workflows"))
    merged_workflows = _merge_workflow_text(principles, workflows)
    if merged_workflows:
        normalized["workflows"] = merged_workflows
    normalized.pop("safety_rules", None)
    for field in ROLE_PROMPT_TEXT_FIELDS:
        if field in normalized:
            normalized[field] = _format_prompt_text(normalized[field])
    return normalized


def require_role_identity(role_data: dict[str, Any], error_code=ErrorCode.PARAM_ERROR) -> str:
    identity = str((role_data or {}).get("identity") or "").strip()
    if not identity:
        raise CustomError(error_code)
    return identity


def _inspect_or_error(file_bytes: bytes) -> tuple[str, dict]:
    try:
        return inspect_package(file_bytes)
    except UnsupportedPackageTypeError as exc:
        raise CustomError(ErrorCode.PACKAGE_TYPE_UNSUPPORTED) from exc
    except PackageFormatError as exc:
        raise CustomError(ErrorCode.PACKAGE_INVALID) from exc


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        if root != member_path and root not in member_path.parents:
            raise CustomError(ErrorCode.PACKAGE_INVALID)
    archive.extractall(destination)


def _archive_directory(archive: zipfile.ZipFile, source_root: Path, archive_root: str = "") -> None:
    if not source_root.exists() or not source_root.is_dir():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    for path in sorted(source_root.rglob("*")):
        if path.is_dir():
            continue
        relative_path = path.relative_to(source_root).as_posix()
        archive_path = f"{archive_root.rstrip('/')}/{relative_path}" if archive_root else relative_path
        archive.write(path, archive_path)


def _is_rbac_payload_path(relative_path: str) -> bool:
    return "rbac_roles" in Path(relative_path).parts


def _write_sanitized_yaml(archive: zipfile.ZipFile, path: Path, archive_path: str) -> bool:
    if path.suffix.lower() not in {".yaml", ".yml"} or path.name not in {"package.yaml", "role.yaml"}:
        return False
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return False
    sanitized = _strip_rbac_fields(data)
    if sanitized == data:
        return False
    archive.writestr(archive_path, yaml.safe_dump(sanitized, allow_unicode=True, sort_keys=False))
    return True


def _archive_package_directory(archive: zipfile.ZipFile, source_root: Path, archive_root: str = "") -> None:
    if not source_root.exists() or not source_root.is_dir():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    for path in sorted(source_root.rglob("*")):
        if path.is_dir():
            continue
        relative_path = path.relative_to(source_root).as_posix()
        if _is_rbac_payload_path(relative_path):
            continue
        archive_path = f"{archive_root.rstrip('/')}/{relative_path}" if archive_root else relative_path
        if _write_sanitized_yaml(archive, path, archive_path):
            continue
        archive.write(path, archive_path)


def _strip_rbac_payload(root: Path) -> None:
    for path in sorted(root.rglob("rbac_roles"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"} or path.name not in {"package.yaml", "role.yaml"}:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        sanitized = _strip_rbac_fields(data)
        if sanitized != data:
            path.write_text(yaml.safe_dump(sanitized, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _package_skill_keys(skill_keys: list[Any]) -> list[str]:
    keys = [str(key) for key in skill_keys if key]
    if AIOE_CLI_SKILL_KEY not in keys:
        keys.append(AIOE_CLI_SKILL_KEY)
    return keys


def _write_builtin_aioe_cli_skill(archive: zipfile.ZipFile, archive_root: str = "") -> None:
    root = archive_root.rstrip("/")
    prefix = f"{root}/" if root else ""
    package = {"type": "skill", "skill_key": AIOE_CLI_SKILL_KEY, "version": 1, "vendor": "aioe"}
    manifest = {
        "key": AIOE_CLI_SKILL_KEY,
        "name": "AIOE CLI",
        "description": AIOE_CLI_SKILL_DESCRIPTION,
        "bindings": {
            "runtime_skills": [AIOE_CLI_SKILL_KEY],
            "aioe_api": {"requires_rbac_role": True},
        },
        "capabilities": ["aioe_api_operation", "aioe_cli", "platform_workflow"],
        "risk_level": "high",
        "require_approval": False,
        "enabled": True,
        "version": 1,
    }
    instructions = "\n".join(
        [
            "# AIOE CLI",
            "",
            AIOE_CLI_SKILL_DESCRIPTION,
            "",
            "## Usage Contract",
            "- AIOE API 调用权限只来自员工实例绑定的 RBAC 角色，不来自数字员工角色或其他 skill。",
            "- 未配置 RBAC 角色时，停止 API 调用并提示需要人工配置平台角色。",
            "- 不把剧本、应用、平台角色或 AIOE 工具当成数字员工 skill 授权。",
            "",
        ]
    )
    archive.writestr(f"{prefix}package.yaml", yaml.safe_dump(package, allow_unicode=True, sort_keys=False))
    archive.writestr(f"{prefix}manifest.yaml", yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
    archive.writestr(f"{prefix}instructions.md", instructions)


def _iter_child_dirs(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if not path.is_dir():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    return sorted(child for child in path.iterdir() if child.is_dir())


def _stage_files(path: Path) -> list[str]:
    if not path.exists():
        return []
    if not path.is_dir():
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    return sorted(child.relative_to(path).as_posix() for child in path.rglob("*") if child.is_file())


def _zip_directory_bytes(source: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        _archive_directory(archive, source)
    return buffer.getvalue()


def _package_resource_root(package_type: str, package_name: str, tenant_id: str, is_builtin: bool) -> Path:
    scope = "__builtin__" if is_builtin else tenant_id
    return Path(DIGITAL_EMPLOYEE_RESOURCES) / scope / package_type / package_name


async def _cleanup_imported_action_artifacts(actions: list[dict]) -> None:
    if not actions:
        return
    from libs import utils

    for action in actions:
        name = action.get("name")
        version = action.get("version")
        if not name or not version:
            continue
        try:
            await utils.delete_from_s3("system", f"{name}/{version}.zip", "tools")
            logo_path = action.get("logo_path")
            if isinstance(logo_path, str) and logo_path.startswith("/files/user/tools/"):
                await utils.delete_from_s3("user", logo_path.removeprefix("/files/user/tools/"), "tools")
        except Exception as exc:
            logger.warning("cleanup digital employee action artifact failed name=%s version=%s error=%s", name, version, exc)


def _unwrap_route_handler(func):
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


def _single_file_request(request, file_name: str, file_bytes: bytes):
    class FileCollection:
        def __init__(self, file_obj):
            self.file_obj = file_obj

        def get(self, key, default=None):
            return self.file_obj if key == "file" else default

    return SimpleNamespace(
        files=FileCollection(SimpleNamespace(name=file_name, body=file_bytes)),
        ctx=request.ctx,
        app=request.app,
    )


async def _upsert_role(root: Path, package: dict, tenant_id: str, is_builtin: bool) -> DigitalEmployeeRole:
    role_data = normalize_role_prompt_fields(_read_yaml(root / "role.yaml"))
    role_key = str(package.get("role_key") or role_data.get("key") or "").strip()
    if not role_key:
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    require_role_identity(role_data, ErrorCode.PACKAGE_INVALID)
    version = int(package.get("version") or role_data.get("version") or 1)
    existing = await DigitalEmployeeRole.get_or_none(tenant_id=tenant_id, role_key=role_key, deleted=False)
    if existing and existing.version > version:
        raise CustomError(ErrorCode.PACKAGE_VERSION_LOWER)
    defaults = {
        "name": role_data.get("name") or package.get("name") or role_key,
        "template_dir": str(root),
        "default_skill_keys": role_data.get("default_skills") or role_data.get("default_skill_keys") or [],
        "default_rbac_role_id": None,
        "license_required": bool(package.get("license_required") or role_data.get("license_required") or False),
        "version": version,
        "is_builtin": is_builtin,
        "enabled": bool(role_data.get("enabled", True)),
        "meta": role_data,
        "deleted": False,
    }
    role, _ = await DigitalEmployeeRole.update_or_create(
        tenant_id=tenant_id,
        role_key=role_key,
        defaults=defaults,
    )
    return role


async def _upsert_skill(root: Path, package: dict, tenant_id: str, is_builtin: bool) -> DigitalEmployeeSkill:
    manifest = _read_yaml(root / "manifest.yaml")
    skill_key = str(package.get("skill_key") or manifest.get("key") or "").strip()
    if not skill_key:
        raise CustomError(ErrorCode.PACKAGE_INVALID)
    version = int(package.get("version") or manifest.get("version") or 1)
    existing = await DigitalEmployeeSkill.get_or_none(tenant_id=tenant_id, skill_key=skill_key, deleted=False)
    if existing and existing.version > version:
        raise CustomError(ErrorCode.PACKAGE_VERSION_LOWER)
    defaults = {
        "name": manifest.get("name") or package.get("name") or skill_key,
        "description": manifest.get("description") or package.get("description"),
        "skill_dir": str(root),
        "bindings": manifest.get("bindings") or {},
        "require_approval": bool(manifest.get("require_approval") or False),
        "version": version,
        "is_builtin": is_builtin,
        "enabled": bool(manifest.get("enabled", True)),
        "meta": manifest,
        "deleted": False,
    }
    skill, _ = await DigitalEmployeeSkill.update_or_create(
        tenant_id=tenant_id,
        skill_key=skill_key,
        defaults=defaults,
    )
    return skill


async def _upsert_rbac_roles(root: Path) -> list[dict]:
    return []


async def _import_bundle_actions(root: Path, tenant_id: str, request, cleanup_actions: list[dict]) -> tuple[list[dict], list[dict]]:
    staged = _stage_files(root)
    if not staged:
        return [], []
    if request is None:
        return [], [
            {
                "type": "actions",
                "reason": "action import requires request context; payload is staged in the extracted bundle",
                "files": staged,
            }
        ]

    from libs.security.authorization import load_license
    from services.tools.views import upload_tool

    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)

    creator = request.ctx.session["account_info"].username
    archives: list[tuple[str, bytes]] = []
    archives.extend((item.name, item.read_bytes()) for item in sorted(root.rglob("*.zip")) if item.is_file())
    if (root / "config.json").exists():
        archives.append((f"{root.name}.zip", _zip_directory_bytes(root)))
    else:
        for child in sorted(item for item in root.iterdir() if item.is_dir() and (item / "config.json").exists()):
            archives.append((f"{child.name}.zip", _zip_directory_bytes(child)))

    if not archives:
        raise CustomError(ErrorCode.PACKAGE_INVALID)

    imported: list[dict] = []
    skipped: list[dict] = []
    for archive_name, archive_bytes in archives:
        result = await upload_tool(SimpleNamespace(name=archive_name, body=archive_bytes), lic, creator, tenant_id, request)
        if result and result.get("msg") == "success":
            tool_name = result.get("name") or result.get("tool_name")
            version = result.get("version")
            tool_record = None
            if tool_name and version:
                tool_record = await Tool.get_or_none(name=tool_name, version=version, deleted=False, tenant_id=tenant_id)
            imported.append({"type": "action", "file": archive_name, "tool_name": result.get("tool_name")})
            if tool_name and version:
                cleanup_actions.append(
                    {
                        "name": tool_name,
                        "version": version,
                        "logo_path": getattr(tool_record, "logo_path", None),
                    }
                )
        else:
            raise CustomError(ErrorCode.PACKAGE_PARTIAL_FAILURE)
    return imported, skipped


async def _import_bundle_playbooks(root: Path, request) -> tuple[list[dict], list[dict]]:
    staged = _stage_files(root)
    if not staged:
        return [], []
    if request is None:
        return [], [
            {
                "type": "playbooks",
                "reason": "playbook import requires request context; payload is staged in the extracted bundle",
                "files": staged,
            }
        ]

    from services.workflow.views import import_workflow

    archives: list[tuple[str, bytes]] = []
    if (root / "workflow.json").exists() or (root / "resources.json").exists():
        archives.append((f"{root.name}.zip", _zip_directory_bytes(root)))
    else:
        archives.extend((item.name, item.read_bytes()) for item in sorted(root.rglob("*.zip")) if item.is_file())

    if not archives:
        raise CustomError(ErrorCode.PACKAGE_INVALID)

    imported: list[dict] = []
    handler = _unwrap_route_handler(import_workflow)
    for archive_name, archive_bytes in archives:
        await handler(_single_file_request(request, archive_name, archive_bytes))
        imported.append({"type": "playbook", "file": archive_name})
    return imported, []


async def _import_bundle(root: Path, package: dict, tenant_id: str, is_builtin: bool, request=None) -> ImportResult:
    package_tenant_id = "__builtin__" if is_builtin else tenant_id
    imported: list[dict] = []
    skipped: list[dict] = []

    async with in_transaction():
        for role_root in _iter_child_dirs(root / "roles"):
            role_package = _read_yaml(role_root / "package.yaml")
            role = await _upsert_role(role_root, role_package, package_tenant_id, is_builtin)
            imported.append({"type": "role", "role_key": role.role_key, "id": role.id})

        for skill_root in _iter_child_dirs(root / "skills"):
            skill_package = _read_yaml(skill_root / "package.yaml")
            skill = await _upsert_skill(skill_root, skill_package, package_tenant_id, is_builtin)
            imported.append({"type": "skill", "skill_key": skill.skill_key, "id": skill.id})

        rbac_root = root / "rbac_roles"
        if rbac_root.exists():
            for role in await _upsert_rbac_roles(rbac_root):
                imported.append({"type": "rbac_role", **role})

        imported_action_artifacts: list[dict] = []
        try:
            action_imported, action_skipped = await _import_bundle_actions(root / "actions", tenant_id, request, imported_action_artifacts)
            imported.extend(action_imported)
            skipped.extend(action_skipped)
            playbook_imported, playbook_skipped = await _import_bundle_playbooks(root / "playbooks", request)
            imported.extend(playbook_imported)
            skipped.extend(playbook_skipped)
        except Exception:
            await _cleanup_imported_action_artifacts(imported_action_artifacts)
            raise

    return ImportResult(
        package_type=str(package.get("type") or "bundle"),
        imported=imported,
        skipped=skipped,
    )


async def _auto_create_employees(role_keys: list[str], tenant_id: str) -> tuple[list[dict], list[dict]]:
    """为导入的岗位自动创建完整数字员工。

    符合「导入即得到一名可用员工」的产品设计：同岗位尚无员工时直接新建，
    已存在员工时自动追加序号重命名后再建，避免界面上出现重名。
    单个员工创建失败（例如 lion_agent 暂不可用导致 workspace 同步异常）只记入 skipped，
    不影响岗位/技能本身的导入结果。
    """
    if not role_keys or not tenant_id:
        return [], []
    from models.command_center import CommandAgent
    from .manager import create_employee, get_visible_role

    created: list[dict] = []
    skipped: list[dict] = []
    for role_key in dict.fromkeys(role_keys):
        try:
            role = await get_visible_role(tenant_id, role_key)
        except CustomError:
            skipped.append({"type": "employee", "role_key": role_key, "reason": "role not visible or disabled"})
            continue
        base_name = role.name or role_key
        existing = await CommandAgent.filter(tenant_id=tenant_id, role_key=role_key, deleted=False).all()
        name = base_name
        if existing:
            taken = {employee.name for employee in existing}
            index = len(existing) + 1
            name = f"{base_name} {index}"
            while name in taken:
                index += 1
                name = f"{base_name} {index}"
        try:
            employee = await create_employee(tenant_id=tenant_id, role_key=role_key, name=name)
        except Exception as exc:
            logger.warning("auto create digital employee failed role_key=%s error=%s", role_key, exc)
            skipped.append({"type": "employee", "role_key": role_key, "reason": str(exc)})
            continue
        created.append(
            {
                "type": "employee",
                "employee_id": employee.id,
                "agent_key": employee.agent_key,
                "role_key": role_key,
                "name": name,
            }
        )
    return created, skipped


async def import_package(file_bytes: bytes, tenant_id: str, is_builtin: bool = False, operator_id: str | None = None, request=None) -> ImportResult:
    package_type, package = _inspect_or_error(file_bytes)
    package_tenant_id = "__builtin__" if is_builtin else tenant_id
    package_name = package.get("role_key") or package.get("skill_key") or package.get("bundle_key") or "package"
    target_root = _package_resource_root(package_type, str(package_name), tenant_id, is_builtin)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_base = Path(tmp_dir)
        tmp_root = tmp_base / "extract"
        tmp_root.mkdir()
        backup_root = tmp_base / "target-backup"
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
                _safe_extract(archive, tmp_root)
        except zipfile.BadZipFile as exc:
            raise CustomError(ErrorCode.PACKAGE_INVALID) from exc
        _strip_rbac_payload(tmp_root)

        target_root.parent.mkdir(parents=True, exist_ok=True)
        had_existing_target = target_root.exists()
        if had_existing_target:
            shutil.move(str(target_root), str(backup_root))

        try:
            shutil.copytree(tmp_root, target_root)
            if package_type == "role":
                role = await _upsert_role(target_root, package, package_tenant_id, is_builtin)
                result = ImportResult(package_type="role", imported=[{"type": "role", "role_key": role.role_key, "id": role.id}])
                if not is_builtin:
                    created, skipped = await _auto_create_employees([role.role_key], tenant_id)
                    result.imported.extend(created)
                    result.skipped.extend(skipped)
                return result
            if package_type == "skill":
                skill = await _upsert_skill(target_root, package, package_tenant_id, is_builtin)
                return ImportResult(package_type="skill", imported=[{"type": "skill", "skill_key": skill.skill_key, "id": skill.id}])
            if package_type == "bundle":
                result = await _import_bundle(target_root, package, tenant_id, is_builtin, request=request)
                if not is_builtin:
                    role_keys = [item["role_key"] for item in result.imported if item.get("type") == "role" and item.get("role_key")]
                    created, skipped = await _auto_create_employees(role_keys, tenant_id)
                    result.imported.extend(created)
                    result.skipped.extend(skipped)
                return result
        except Exception:
            if target_root.exists():
                shutil.rmtree(target_root)
            if had_existing_target and backup_root.exists():
                shutil.move(str(backup_root), str(target_root))
            raise

    raise CustomError(ErrorCode.PACKAGE_TYPE_UNSUPPORTED)


async def import_builtin_packages(operator_id: str = "system") -> ImportResult:
    logger.warning(
        "digital employee builtin package bootstrap is deprecated; "
        "builtin role and skill packages are managed by aioe_package"
    )
    return ImportResult(
        package_type="builtin",
        skipped=[
            {
                "reason": "builtin packages are managed by aioe_package",
                "operator_id": operator_id,
            }
        ],
    )


async def export_package(role_key: str | None = None, skill_key: str | None = None, bundle: bool = False) -> bytes:
    return await export_package_for_tenant("__builtin__", role_key=role_key, skill_key=skill_key, bundle=bundle)


async def export_package_for_tenant(
    tenant_id: str | None,
    role_key: str | None = None,
    skill_key: str | None = None,
    bundle: bool = False,
) -> bytes:
    effective_tenant_id = tenant_id or "__builtin__"
    if not role_key and not skill_key:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if bundle and not role_key:
        raise CustomError(ErrorCode.PARAM_ERROR)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if bundle:
            from .manager import get_visible_role, get_visible_skills

            role = await get_visible_role(effective_tenant_id, str(role_key))
            role_source = Path(role.template_dir)
            role_package = _read_yaml(role_source / "package.yaml")
            skill_keys = _package_skill_keys(
                role_package.get("default_skill_keys")
                or (role.meta or {}).get("default_skills")
                or (role.meta or {}).get("default_skill_keys")
                or []
            )
            skills = await get_visible_skills(effective_tenant_id, [str(key) for key in skill_keys])
            skill_sources = {
                skill.skill_key: Path(skill.skill_dir)
                for skill in skills
                if getattr(skill, "skill_dir", None)
            }
            role_package = _read_yaml(role_source / "package.yaml")
            bundle_key = role_package.get("bundle_key") or role_key
            archive.writestr(
                "package.yaml",
                yaml.safe_dump(
                    {
                        "type": "bundle",
                        "bundle_key": bundle_key,
                        "role_key": role_key,
                        "version": role_package.get("version", 1),
                        "vendor": role_package.get("vendor", "aioe"),
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
            )
            _archive_package_directory(archive, role_source, f"roles/{role_key}")
            for default_skill_key in skill_keys:
                skill_source = skill_sources.get(str(default_skill_key))
                if str(default_skill_key) == AIOE_CLI_SKILL_KEY and (not skill_source or not skill_source.exists()):
                    _write_builtin_aioe_cli_skill(archive, f"skills/{default_skill_key}")
                    continue
                if not skill_source or not skill_source.exists():
                    raise CustomError(ErrorCode.PACKAGE_INVALID)
                _archive_package_directory(archive, skill_source, f"skills/{default_skill_key}")
        else:
            package_type = "role" if role_key else "skill"
            package_key = role_key or skill_key
            from .manager import get_visible_role, get_visible_skills

            if role_key:
                source_root = Path((await get_visible_role(effective_tenant_id, str(role_key))).template_dir)
            else:
                skills = await get_visible_skills(effective_tenant_id, [str(skill_key)])
                source_root = Path(skills[0].skill_dir)
            if source_root.exists():
                _archive_package_directory(archive, source_root)
            else:
                archive.writestr("package.yaml", yaml.safe_dump({"type": package_type, f"{package_type}_key": package_key}, allow_unicode=True))
    return buffer.getvalue()
