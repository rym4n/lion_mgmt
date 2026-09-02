# coding=utf-8
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.user import Role


DEFAULT_PLATFORM_ROLE_PERMISSIONS: list[str] = []


def _default_platform_role_name(role_data: dict, role_id: str) -> str:
    role_name = str(role_data.get("name") or role_data.get("role_name") or "").strip()
    if role_name:
        if role_name.endswith("平台角色"):
            return role_name
        return f"{role_name}平台角色"
    return role_id


def _default_platform_role_description(role_data: dict) -> str:
    description = str(role_data.get("description") or "").strip()
    if description:
        return f"数字伙伴默认平台角色：{description}"
    return "数字伙伴默认平台角色"


async def ensure_platform_role(role_id: str | None, role_data: dict, tenant_id: str) -> tuple[Role | None, bool]:
    role_id = str(role_id or "").strip()
    if not role_id:
        return None, False
    role = await Role.get_or_none(id=role_id)
    if role:
        if role.deleted:
            role.deleted = False
            role.permissions = DEFAULT_PLATFORM_ROLE_PERMISSIONS.copy()
            role.name = role.name or _default_platform_role_name(role_data, role_id)
            role.description = role.description or _default_platform_role_description(role_data)
            await role.save(update_fields=["deleted", "permissions", "name", "description", "utime"])
        return role, False
    role = await Role.create(
        id=role_id,
        name=_default_platform_role_name(role_data, role_id),
        description=_default_platform_role_description(role_data),
        permissions=DEFAULT_PLATFORM_ROLE_PERMISSIONS.copy(),
        tenant_id=tenant_id,
    )
    return role, True


async def require_existing_platform_role(role_id: str | None, tenant_id: str) -> Role | None:
    role_id = str(role_id or "").strip()
    if not role_id:
        return None
    role = await Role.get_or_none(id=role_id, deleted=False)
    if not role:
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
    role_tenant_id = getattr(role, "tenant_id", None)
    if tenant_id and role_tenant_id not in {tenant_id, "__builtin__", None}:
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
    return role
