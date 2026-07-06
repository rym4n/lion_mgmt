# coding=utf-8

import functools
import hashlib
import os

from sanic.log import logger
from sanic.request import Request
from tortoise.expressions import Q

from core.error_code import ErrorCode
from libs.error_code.enum import ErrorData
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.utils import verify_license
from models import Account, ActionPermissions, PermissionsMenu, PlaybookPermissions, PlaybookVersion, Role
from settings import AUTHENTICATION_SESSION_EXPIRATION, LICENSE_DIR

# 超管角色的本质是拥有 *:* 通配权限的那条 Role（见 aioe_package/aioe_install/init.sql 初始化）。
# 用权限本质而非硬编码角色 id "111111" 定位，避免多环境/多次种子数据下 id 漂移导致的隐性 bug。
SUPERADMIN_PERMISSION = "*:*"
# 租户管理员是租户维度的管理角色，各租户内以名称"租户管理员"唯一（见 services/tenant/views.py:125）。
TENANT_ADMIN_ROLE_NAME = "租户管理员"


def _roles_have_any_permission(roles, required_permissions: tuple[str, ...]) -> bool:
    for role in roles or []:
        for role_permission in role.permissions or []:
            for required_permission in required_permissions:
                if check_wildcard(role_permission, required_permission):
                    return True
    return False


async def _refresh_session_account(request: Request):
    account_id = request.ctx.session.get("sub") if hasattr(request.ctx, "session") else None
    if not account_id:
        return None
    account = await Account.get_or_none(id=account_id, deleted=False).prefetch_related("roles")
    if not account:
        return None
    request.ctx.session["account_info"] = account
    cache_client = getattr(request.app.ctx, "cache_client", None)
    if cache_client:
        await cache_client.set(f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION)
    return account


async def check_permissions(request: Request, *required_permissions: str) -> bool:
    # payload = await token_decode(request)
    # account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
    # if not account:
    #     logger.warning("redis里已经没有account信息了")
    #     raise CustomError(ErrorCode.SESSION_INVALID)
    # request.ctx.session['account_info'] = account
    # roles = await account.roles.filter(deleted=False).all()
    account = request.ctx.session["account_info"]
    if _roles_have_any_permission(account.roles, required_permissions):
        return True

    refreshed_account = await _refresh_session_account(request)
    if refreshed_account and _roles_have_any_permission(refreshed_account.roles, required_permissions):
        return True

    pm = await PermissionsMenu.filter(model=request.path.split("/")[1]).first().values("description")
    module = pm.get("description") if pm else request.path.split("/")[1]
    raise CustomError(ErrorData(4004, f"缺少{module}模块的操作权限"))


def check_wildcard(wildcard: str, pattern: str):
    """
    权限校验匹配规则，wildcard 代表允许的权限，pattern 代表需要校验的权限

    例如：
        wildcard: role:* 代表允许执行role模块的所有操作
        pattern: role:create 代表需要执行role模块的create操作的权限
        True 代表满足权限要求

        wildcard: role:read
        pattern: role:create
        False
    """
    wildcard_parts = [set(part.split(",")) for part in wildcard.split(":")]
    pattern_parts = [set(part.split(",")) for part in pattern.split(":")]
    for i, pattern_part in enumerate(pattern_parts):
        if i >= len(wildcard_parts):
            return False
        wildcard_part = wildcard_parts[i]
        if "*" not in wildcard_part and not wildcard_part.issuperset(pattern_part):
            return False
    return all("*" in part for part in wildcard_parts[len(pattern_parts) :])


def account_has_permission(account, *required_permissions: str) -> bool:
    """判断账户是否拥有全部指定的垂直权限（不抛异常，供业务层做条件判断用）。

    与 check_permissions 共用 check_wildcard 匹配规则，但后者是装饰器用途、
    命中失败即 raise。业务代码需要「有没有权限」的布尔判断时用本函数，
    避免各接口自行遍历 roles/permissions 或硬编码角色 id/name。
    """
    if not account or not getattr(account, "roles", None):
        return False
    for role in account.roles:
        for role_permission in role.permissions or []:
            if all(check_wildcard(role_permission, pattern) for pattern in required_permissions):
                return True
    return False


def is_superadmin(account) -> bool:
    """超管判定：拥有 *:* 通配权限即为超管，而非硬编码角色 id '111111'。"""
    return account_has_permission(account, SUPERADMIN_PERMISSION)


async def get_superadmin_role():
    """按权限本质取超管 Role 对象，供需要将超管作为 PlaybookPermissions/ActionPermissions
    授权对象的场景使用。取第一条 permissions 里含 *:* 的角色（种子数据里只有一条）。
    """
    return await Role.filter(permissions__contains=[SUPERADMIN_PERMISSION], deleted=False).first()


def is_superadmin_role(role) -> bool:
    """判定给定 Role 是否为超管角色（用其权限本质，不看 id/name）。"""
    if not role:
        return False
    return SUPERADMIN_PERMISSION in (role.permissions or [])


async def playbook_ids_for_user(account, tenant_id: str, permission: str = "r") -> set:
    """返回当前账户在指定租户下、对剧本拥有 `permission` 权限的 pb_id 集合。

    与 PlaybookPermissions 的 users(Account FK)/roles(Role FK) 结构对齐：
    用户被授权 或 用户所属任一角色被授权，视为授权命中。超管(*:*)不额外调用本函数——
    调用方应先判断 is_superadmin 走全量分支，本函数只负责普通用户按记录过滤。
    """
    user_id = str(account.id) if account and getattr(account, "id", None) else None
    user_roles = {str(role.id) for role in (account.roles or [])} if account else set()
    if not user_id:
        return set()
    pb_ids = await PlaybookPermissions.filter(
        Q(users__id=user_id) | Q(roles__id__in=user_roles),
        permissions__contains=[permission],
        tenant_id=tenant_id,
    ).values_list("pb_id", flat=True)
    return set(pb_ids)


def action_permission_hits(permission, resource_id: str, user_id: str, user_role_ids: set) -> bool:
    """判断当前用户是否命中一条 ActionPermissions 记录里指定 resource 的授权。

    ActionPermissions.users / roles 结构（`services/tools/views.py:1841-1870` 授权处写入）：
        users = [{ resource_id: [user_id, ...], ... }]   # 长度恒为 1（如为空则可视作 [{}]）
        roles = [{ resource_id: [role_id, ...], ... }]

    命中条件：用户直接被授权 OR 用户任一角色被授权。传入 None/异常结构时安全返回 False。
    """
    def _first_dict(value):
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    user_map = _first_dict(permission.users)
    if user_id in {str(x) for x in user_map.get(resource_id, [])}:
        return True
    role_map = _first_dict(permission.roles)
    granted_roles = {str(x) for x in role_map.get(resource_id, [])}
    return not user_role_ids.isdisjoint(granted_roles)


async def check_action_execute_permission(account, tool_name, action_func, resource, tenant_id) -> bool:
    """判定账户是否对 (tool, action, resource) 拥有执行权。写侧（add_timed/update_timed
    等）用它把前端下拉过滤对应的授权关卡落到 API 层——纯前端过滤是 UX，不是安全边界。

    - 超管(*:*)豁免。
    - resource 空字符串或无值时视为动作无资源约束，直接放行（与旧列表过滤逻辑一致）。
    - resource 支持逗号分隔多值；要求账户对**每个** resource 都有权，任一缺失即失败。
    - tool_name / action_func 缺失时直接判定失败——上层应保证参数完备再调用。
    - 命中条件复用 action_permission_hits：查 action_name 自身 + "all" 兜底两类 ActionPermissions。
    """
    if is_superadmin(account):
        return True
    if not resource:
        return True
    if not tool_name or not action_func:
        return False
    user_id = str(account.id) if account and getattr(account, "id", None) else None
    if not user_id:
        return False
    user_role_ids = {str(role.id) for role in (account.roles or [])}
    permissions = await ActionPermissions.filter(
        tool_name=tool_name,
        action_name__in=[action_func, "all"],
        deleted=False,
        tenant_id=tenant_id,
    ).all()
    resources = [r for r in resource.split(",") if r] if "," in resource else [resource]
    for r in resources:
        if not any(action_permission_hits(perm, r, user_id, user_role_ids) for perm in permissions):
            return False
    return True


async def check_roles(request: Request, *required_roles: str) -> bool:
    payload = await token_decode(request)
    account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
    if not account:
        logger.warning("redis里已经没有account信息了")
        raise CustomError(ErrorCode.SESSION_INVALID)
    request.ctx.session["account_info"] = account
    if set(required_roles) & {role.name for role in account.roles}:
        return True
    raise CustomError(ErrorCode.UNAUTH)


def requires_permission(*required_permissions: str):
    """
    权限校验装饰器，定义权限需要明确，不能使用通配符。标识统一为：
        增 - c
        删 - d
        改 - u
        查 - r
        管理 - a
    请注意此装饰器只能判断垂直权限，不能判断横向权限
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await check_permissions(request, *required_permissions)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def playbook_permission(*required_permissions: str):
    """
    剧本权限验证装饰器（支持多权限验证）

    使用示例：
    @playbook_permission('r')        # 需要读权限
    @playbook_permission('u', 'd')   # 需要更新或删除权限
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            playbook_ids = request.json.get("playbook_ids")
            if not playbook_ids:
                playbook_id = request.json.get("playbook_id")  # 获取剧本id
                if not playbook_id:
                    playbook_id = request.json.get("flow_id")  # 获取剧本版本id
                    if not playbook_id:
                        raise CustomError(ErrorCode.PLAYBOOK_UNAUTHORIZED)
                playbook_ids = [playbook_id]

            for playbook_id in playbook_ids:
                user = request.ctx.session.get("account_info")
                if not user:
                    raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

                has_access = await check_playbook_permission(request, user, playbook_id, required_permissions)

                if not has_access:
                    raise CustomError(ErrorCode.UNAUTH)

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


# 权限检查工具函数
async def check_playbook_permission(request, user, playbook_id, required_permissions):
    """检查用户是否拥有剧本的指定权限
    :param user: 用户对象(需包含id和roles属性)
    :param data: 缓存中的权限数据(dict格式{"user":[...], "role":[...]})
    :return: bool 表示是否拥有权限
    """
    user_id = str(user.id)
    user_roles = set(str(role.id) for role in user.roles)
    data = await request.app.ctx.cache_client.get((playbook_id, user_id))

    tenant_id = request.ctx.session.get("tenant_id")

    if not data:
        pp = await PlaybookPermissions.get_or_none(pb_id=playbook_id, users_id=user_id, tenant_id=tenant_id)
        if pp:
            data = pp.permissions
        else:
            pv = await PlaybookVersion.get_or_none(id=playbook_id, tenant_id=tenant_id)
            if pv:
                pp = await PlaybookPermissions.get_or_none(pb_id=pv.playbook_id, users_id=user_id, tenant_id=tenant_id)
                if pp:
                    data = pp.permissions
    if data and all(perm in data for perm in required_permissions):
        return True
    else:
        for role in user_roles:
            data = await request.app.ctx.cache_client.get((playbook_id, role))
            if not data:
                pp = await PlaybookPermissions.get_or_none(pb_id=playbook_id, roles_id=role, tenant_id=tenant_id)
                if pp:
                    data = pp.permissions
                else:
                    pv = await PlaybookVersion.get_or_none(id=playbook_id, tenant_id=tenant_id)
                    if pv:
                        pp = await PlaybookPermissions.get_or_none(pb_id=pv.playbook_id, roles_id=role, tenant_id=tenant_id)
                        if pp:
                            data = pp.permissions
            if data and all(perm in data for perm in required_permissions):
                return True
    return False


def requires_role(*required_roles: str):
    """
    角色检查装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await check_roles(request, *required_roles)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def load_license():
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            with open(full_path, "r", encoding="utf-8") as f:
                data = f.read()
                if filename == hashlib.md5(f"{data}".encode()).hexdigest():
                    try:
                        lic = verify_license(data.encode())
                        return lic
                    except Exception as e:
                        logger.error(f"License无效: {e}")
    return None


# 计算lic文件的sha256值
def get_license_sha256():
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            with open(full_path, "rb") as f:
                raw_data = f.read()
                if filename == hashlib.md5(raw_data).hexdigest():
                    return hashlib.sha256(raw_data).hexdigest()
    return None
