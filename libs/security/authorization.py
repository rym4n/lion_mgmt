# coding=utf-8

import functools
import hashlib
import os

from sanic.log import logger
from sanic.request import Request
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.maintenance import effective_maintenance_mode, is_api_account
from libs.error_code.enum import ErrorData
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.security.mfa import require_sensitive_mfa_proof
from libs.utils import verify_license
from models import (
    Account,
    ActionPermissions,
    PermissionsMenu,
    Playbook,
    PlaybookPermissions,
    PlaybookVersion,
    Role,
    Tool,
    ToolResources,
    ToolResourcesModel,
)
from settings import AUTHENTICATION_SESSION_EXPIRATION, LICENSE_DIR

# 全平台超管角色是产品固定身份，初始化数据见 aioe_package/aioe_install/init.sql。
SUPERADMIN_ROLE_ID = "111111"
# *:* 是通配权限，不等价于全平台超管身份。
SUPERADMIN_PERMISSION = "*:*"
# 租户管理员是租户维度的管理角色，各租户内以名称"租户管理员"唯一（见 services/tenant/views.py:125）。
TENANT_ADMIN_ROLE_NAME = "租户管理员"
PLAYBOOK_UNCONTROLLED_PERMISSION = "workflow:uncontrolled"
PLAYBOOK_UNCONTROLLED_ACTION = "uncontrolled"
TOOL_VIEW_PERMISSION = "tool:r"
TOOL_RESOURCE_UNCONTROLLED_PERMISSION = "tool:resource:uncontrolled"
MUTATING_PERMISSION_ACTIONS = {"c", "u", "d"}
PLAYBOOK_FINE_PERMISSION_MAP = {
    "r": "workflow:r",
    "u": "workflow:u",
    "d": "workflow:d",
    "e": "workflow:u",
    "p": "workflow:u",
}
PLAYBOOK_MODULE_TO_FINE_PERMISSIONS = {
    "r": {"r"},
    "u": {"u", "e", "p"},
    "d": {"d"},
}


def _roles_have_any_permission(roles, required_permissions: tuple[str, ...]) -> bool:
    for role in roles or []:
        for role_permission in role.permissions or []:
            for required_permission in required_permissions:
                if check_wildcard(role_permission, required_permission):
                    return True
    return False


def _roles_for_tenant(roles, tenant_id: str | None):
    if roles is None:
        return []
    roles_list = list(roles)
    if tenant_id is None:
        return roles_list
    return [
        role for role in roles_list if getattr(role, "tenant_id", None) == tenant_id
    ]


def _requires_fresh_permission_check(required_permissions: tuple[str, ...]) -> bool:
    for permission in required_permissions:
        actions = permission.rsplit(":", 1)[-1].split(",")
        if MUTATING_PERMISSION_ACTIONS.intersection(actions):
            return True
    return False


async def _refresh_session_account(request: Request):
    account_id = (
        request.ctx.session.get("sub") if hasattr(request.ctx, "session") else None
    )
    if not account_id:
        return None
    account = await Account.get_or_none(id=account_id, deleted=False).prefetch_related(
        "roles"
    )
    if not account:
        return None
    request.ctx.session["account_info"] = account
    cache_client = getattr(request.app.ctx, "cache_client", None)
    if cache_client:
        await cache_client.set(
            f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION
        )
    return account


async def check_permissions(
    request: Request,
    *required_permissions: str,
    refresh: bool = False,
) -> bool:
    # payload = await token_decode(request)
    # account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
    # if not account:
    #     logger.warning("redis里已经没有account信息了")
    #     raise CustomError(ErrorCode.SESSION_INVALID)
    # request.ctx.session['account_info'] = account
    # roles = await account.roles.filter(deleted=False).all()
    session = request.ctx.session
    account = session["account_info"]
    tenant_id = session.get("tenant_id")
    if refresh:
        account = await _refresh_session_account(request)
        if not account:
            raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
    if _roles_have_any_permission(
        _roles_for_tenant(account.roles, tenant_id), required_permissions
    ):
        return True

    if not refresh:
        refreshed_account = await _refresh_session_account(request)
        if refreshed_account and _roles_have_any_permission(
            _roles_for_tenant(refreshed_account.roles, tenant_id),
            required_permissions,
        ):
            return True

    pm = (
        await PermissionsMenu.filter(
            model=request.path.split("/")[1], deleted=False
        )
        .first()
        .values("description")
    )
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

        平台级 *:* 代表全部权限，也覆盖 asset_management:r
        这类带资源层级的权限；模块级通配符仍按原有分段规则匹配。
    """
    if wildcard == SUPERADMIN_PERMISSION:
        return True

    wildcard_parts = [set(part.split(",")) for part in wildcard.split(":")]
    pattern_parts = [set(part.split(",")) for part in pattern.split(":")]
    for i, pattern_part in enumerate(pattern_parts):
        if i >= len(wildcard_parts):
            return False
        wildcard_part = wildcard_parts[i]
        if "*" not in wildcard_part and not wildcard_part.issuperset(pattern_part):
            return False
    return all("*" in part for part in wildcard_parts[len(pattern_parts) :])


def account_has_permission(
    account, *required_permissions: str, tenant_id: str | None = None
) -> bool:
    """判断账户是否拥有全部指定的垂直权限（不抛异常，供业务层做条件判断用）。

    与 check_permissions 共用 check_wildcard 匹配规则，但后者是装饰器用途、
    命中失败即 raise。业务代码需要「有没有权限」的布尔判断时用本函数，
    避免各接口自行遍历 roles/permissions 或硬编码角色 id/name。
    """
    if not required_permissions or not account or not getattr(account, "roles", None):
        return False
    grants = [
        role_permission
        for role in _roles_for_tenant(account.roles, tenant_id)
        for role_permission in role.permissions or []
    ]
    return all(
        any(check_wildcard(grant, required) for grant in grants)
        for required in required_permissions
    )


def workflow_uncontrolled_actions(role_permission: object) -> set[str]:
    if not isinstance(role_permission, str) or not role_permission.startswith(
        "workflow:"
    ):
        return set()

    actions = set(role_permission.split(":", 1)[1].split(","))
    if PLAYBOOK_UNCONTROLLED_ACTION not in actions:
        return set()
    if "*" in actions:
        return set(PLAYBOOK_MODULE_TO_FINE_PERMISSIONS)
    return actions.intersection(PLAYBOOK_MODULE_TO_FINE_PERMISSIONS)


def uncontrolled_playbook_permissions(
    account, tenant_id: str | None = None
) -> set[str]:
    """返回角色不受控时可绕过的单剧本权限，仍以功能模块权限为边界。"""
    if not account or not getattr(account, "roles", None):
        return set()

    permissions = set()
    has_global_uncontrolled = False
    for role in _roles_for_tenant(account.roles, tenant_id):
        for role_permission in role.permissions or []:
            if role_permission == SUPERADMIN_PERMISSION:
                has_global_uncontrolled = True
                continue
            if role_permission == PLAYBOOK_UNCONTROLLED_PERMISSION:
                has_global_uncontrolled = True
                continue
            for action in workflow_uncontrolled_actions(role_permission):
                if account_has_permission(
                    account, f"workflow:{action}", tenant_id=tenant_id
                ):
                    permissions.update(PLAYBOOK_MODULE_TO_FINE_PERMISSIONS[action])

    if has_global_uncontrolled:
        permissions.update(
            playbook_permission
            for playbook_permission, module_permission in PLAYBOOK_FINE_PERMISSION_MAP.items()
            if account_has_permission(account, module_permission, tenant_id=tenant_id)
        )
    return permissions


def has_explicit_playbook_uncontrolled_permission(
    account, tenant_id: str | None = None
) -> bool:
    if not account or not getattr(account, "roles", None):
        return False
    for role in _roles_for_tenant(account.roles, tenant_id):
        for role_permission in role.permissions or []:
            if role_permission == SUPERADMIN_PERMISSION:
                return True
            if role_permission == PLAYBOOK_UNCONTROLLED_PERMISSION:
                return True
            if workflow_uncontrolled_actions(role_permission):
                return True
    return False


def has_uncontrolled_playbook_permission(
    account, required_permissions, tenant_id: str | None = None
) -> bool:
    required = set(required_permissions or [])
    return bool(required) and required.issubset(
        uncontrolled_playbook_permissions(account, tenant_id=tenant_id)
    )


def has_explicit_tool_resource_uncontrolled_permission(
    account, tenant_id: str | None = None
) -> bool:
    if not account or not getattr(account, "roles", None):
        return False
    for role in _roles_for_tenant(account.roles, tenant_id):
        for role_permission in role.permissions or []:
            if role_permission == SUPERADMIN_PERMISSION:
                return True
            if role_permission == TOOL_RESOURCE_UNCONTROLLED_PERMISSION:
                return True
    return False


def has_uncontrolled_tool_resource_permission(
    account, tenant_id: str | None = None
) -> bool:
    """角色显式配置资源执行不受控时，绕过 ActionPermissions 明细授权。

    与剧本不受控保持同一边界：特殊标记只负责绕过横向明细记录，
    仍要求角色具备应用市场模块查看权限。
    """
    return has_explicit_tool_resource_uncontrolled_permission(
        account,
        tenant_id=tenant_id,
    ) and account_has_permission(account, TOOL_VIEW_PERMISSION, tenant_id=tenant_id)


def is_superadmin(account, tenant_id: str | None = None) -> bool:
    """全平台超管身份判定：只认固定超管角色 id，不按 *:* 权限推导。"""
    return bool(
        account
        and getattr(account, "roles", None)
        and any(is_superadmin_role(role) for role in account.roles)
    )


async def get_superadmin_role():
    """取全平台超管 Role 对象，供 PlaybookPermissions/ActionPermissions 等授权对象使用。"""
    return await Role.get_or_none(id=SUPERADMIN_ROLE_ID, deleted=False)


def is_superadmin_role(role) -> bool:
    """判定给定 Role 是否为全平台超管角色。"""
    return bool(role and str(getattr(role, "id", "")) == SUPERADMIN_ROLE_ID)


async def playbook_ids_for_user(account, tenant_id: str, permission: str = "r") -> set:
    """返回当前账户在指定租户下、对剧本拥有 `permission` 权限的 pb_id 集合。

    与 PlaybookPermissions 的 users(Account FK)/roles(Role FK) 结构对齐：
    用户被授权 或 用户所属任一角色被授权，视为授权命中。全平台超管不额外调用本函数——
    调用方应先判断 is_superadmin 走全量分支，本函数只负责普通用户按记录过滤。
    """
    if has_uncontrolled_playbook_permission(account, [permission], tenant_id):
        pb_filters = {"deleted": False, "tenant_id": tenant_id}
        if is_api_account(account):
            pb_filters["internal"] = False
        pb_ids = await Playbook.filter(**pb_filters).values_list("id", flat=True)
        return set(pb_ids)

    user_id = str(account.id) if account and getattr(account, "id", None) else None
    user_roles = {str(role.id) for role in (account.roles or [])} if account else set()
    if not user_id:
        return set()
    pb_ids = await PlaybookPermissions.filter(
        Q(users__id=user_id) | Q(roles__id__in=user_roles),
        permissions__contains=[permission],
        tenant_id=tenant_id,
        deleted=False,
    ).values_list("pb_id", flat=True)
    return set(pb_ids)


def _merged_playbook_permissions(permission_records) -> set[str]:
    permissions: set[str] = set()
    for permission_record in permission_records or []:
        permissions.update(permission_record.permissions or [])
    return permissions


async def _playbook_permission_records(playbook_id: str, tenant_id: str, **filters):
    return await PlaybookPermissions.filter(
        pb_id=playbook_id,
        tenant_id=tenant_id,
        deleted=False,
        **filters,
    ).all()


async def _candidate_permission_playbook_ids(
    playbook_id: str, tenant_id: str
) -> list[str]:
    playbook_ids = [playbook_id]
    playbook_version = await PlaybookVersion.get_or_none(
        id=playbook_id, tenant_id=tenant_id
    )
    if playbook_version and playbook_version.playbook_id not in playbook_ids:
        playbook_ids.append(playbook_version.playbook_id)
    return playbook_ids


async def _has_playbook_permission_record(
    playbook_ids: list[str],
    tenant_id: str,
    required_permissions,
    **filters,
) -> bool:
    for playbook_id in playbook_ids:
        permissions = _merged_playbook_permissions(
            await _playbook_permission_records(playbook_id, tenant_id, **filters)
        )
        if permissions and all(perm in permissions for perm in required_permissions):
            return True
    return False


def action_permission_hits(
    permission, resource_id: str, user_id: str, user_role_ids: set
) -> bool:
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


async def tool_requires_resource(
    tool_name,
    tenant_id,
    maintenance_mode=False,
) -> bool:
    resource_model = await ToolResourcesModel.get_or_none(
        tool_name=tool_name,
        deleted=False,
        internal=maintenance_mode,
        tenant_id=tenant_id,
    )
    return bool(resource_model and resource_model.status == 1)


async def tool_resources_exist(
    tool_name,
    resource_ids,
    tenant_id,
    maintenance_mode=False,
) -> bool:
    normalized_resource_ids = {str(item) for item in resource_ids or [] if str(item)}
    if not normalized_resource_ids:
        return False
    existing_resource_ids = await ToolResources.filter(
        id__in=list(normalized_resource_ids),
        tool_name=tool_name,
        deleted=False,
        internal=maintenance_mode,
        tenant_id=tenant_id,
    ).values_list("id", flat=True)
    return normalized_resource_ids.issubset(
        {str(resource_id) for resource_id in existing_resource_ids}
    )


async def check_action_execute_permission(
    account,
    tool_name,
    action_func,
    resource,
    tenant_id,
    maintenance_mode=False,
    tool_version=None,
) -> bool:
    """判定账户是否对 (tool, action, resource) 拥有执行权。写侧（add_timed/update_timed
    等）用它把前端下拉过滤对应的授权关卡落到 API 层——纯前端过滤是 UX，不是安全边界。

    - 全平台超管豁免。
    - 资源模型要求资源时，空 resource 拒绝；无需资源的工具允许空 resource。
    - resource 支持逗号分隔多值；要求账户对**每个** resource 都有权，任一缺失即失败。
    - tool_name / action_func 缺失时直接判定失败——上层应保证参数完备再调用。
    - 命中条件复用 action_permission_hits：查 action_name 自身 + "all" 兜底两类 ActionPermissions。
    """
    # API token accounts, including digital employee credentials, never execute
    # against the maintenance application namespace even if a caller passes True.
    maintenance_mode = effective_maintenance_mode(account, maintenance_mode)
    if not tool_name or not action_func:
        return False
    if tool_version and not await Tool.filter(
        name=tool_name,
        version=tool_version,
        deleted=False,
        maintain=maintenance_mode,
        tenant_id=tenant_id,
    ).exists():
        return False
    if not isinstance(resource, str):
        resource = ""
    resources = [item.strip() for item in resource.split(",") if item.strip()]
    if tool_version and resources and not await tool_resources_exist(
        tool_name,
        resources,
        tenant_id,
        maintenance_mode,
    ):
        return False
    if is_superadmin(account, tenant_id=tenant_id):
        return True
    if not resources:
        return not await tool_requires_resource(
            tool_name,
            tenant_id,
            maintenance_mode,
        )
    if has_uncontrolled_tool_resource_permission(account, tenant_id=tenant_id):
        if tool_version:
            return True
        return await tool_resources_exist(
            tool_name,
            resources,
            tenant_id,
            maintenance_mode,
        )
    user_id = str(account.id) if account and getattr(account, "id", None) else None
    if not user_id:
        return False
    user_role_ids = {
        str(role.id) for role in _roles_for_tenant(account.roles, tenant_id)
    }
    permissions = await ActionPermissions.filter(
        tool_name=tool_name,
        action_name__in=[action_func, "all"],
        deleted=False,
        tenant_id=tenant_id,
    ).all()
    for resource_id in resources:
        if not any(
            action_permission_hits(permission, resource_id, user_id, user_role_ids)
            for permission in permissions
        ):
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


def requires_permission(*required_permissions: str, refresh: bool = False):
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
            await check_permissions(
                request,
                *required_permissions,
                refresh=refresh
                or _requires_fresh_permission_check(required_permissions),
            )
            if any(
                permission.split(":", 1)[0] in {"user", "secure"}
                for permission in required_permissions
            ):
                await require_sensitive_mfa_proof(request)
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

                has_access = await check_playbook_permission(
                    request, user, playbook_id, required_permissions
                )

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
    tenant_id = request.ctx.session.get("tenant_id")
    playbook_ids = None
    if is_api_account(user):
        playbook_ids = await _candidate_permission_playbook_ids(playbook_id, tenant_id)
        if await Playbook.filter(
            id__in=playbook_ids,
            tenant_id=tenant_id,
            deleted=False,
            internal=True,
        ).exists():
            return False
    if has_uncontrolled_playbook_permission(
        user, required_permissions, tenant_id=tenant_id
    ):
        return True

    if playbook_ids is None:
        playbook_ids = await _candidate_permission_playbook_ids(playbook_id, tenant_id)

    user_id = str(user.id)
    user_roles = set(str(role.id) for role in user.roles)
    data = await request.app.ctx.cache_client.get((playbook_id, user_id))

    if not data:
        if await _has_playbook_permission_record(
            playbook_ids,
            tenant_id,
            required_permissions,
            users_id=user_id,
        ):
            return True
    if data and all(perm in data for perm in required_permissions):
        return True
    else:
        for role in user_roles:
            data = await request.app.ctx.cache_client.get((playbook_id, role))
            if not data:
                if await _has_playbook_permission_record(
                    playbook_ids,
                    tenant_id,
                    required_permissions,
                    roles_id=role,
                ):
                    return True
            if data and all(perm in data for perm in required_permissions):
                return True
    return False


def ticket_flow_permission(*required_permissions: str):
    """工单流程权限验证装饰器（对标 playbook_permission）。

    使用示例：
    @ticket_flow_permission('r')        # 需要读权限
    @ticket_flow_permission('u', 'd')   # 需要更新或删除权限

    从 request.json 取 flow_id 或 ticket_id（后者需先查 Ticket.flow_version.ticket_flow_id）。
    GET 请求时从 request.args 或路径参数中取。
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            # 支持 GET 请求: request.json 可能为 None
            json_data = request.json or {}
            flow_id = json_data.get("flow_id")

            # GET 请求: 尝试从 query 参数或路径参数获取
            if not flow_id:
                flow_id = request.args.get("flow_id")
            if not flow_id and kwargs.get("flow_id"):
                flow_id = kwargs.get("flow_id")

            if not flow_id:
                ticket_id = json_data.get("ticket_id")
                if ticket_id:
                    from models.ticket import Ticket

                    tenant_id = request.ctx.session.get("tenant_id")
                    ticket = await Ticket.filter(
                        id=ticket_id, tenant_id=tenant_id, deleted=False
                    ).select_related("flow_version__ticket_flow").first()
                    if ticket and ticket.flow_version:
                        flow_id = ticket.flow_version.ticket_flow_id
            if not flow_id:
                from core.error_code import ErrorCode
                from libs.error_code.exception import CustomError

                raise CustomError(ErrorCode.TICKET_FLOW_PERM_DENIED)

            user = request.ctx.session.get("account_info")
            if not user:
                from core.error_code import ErrorCode
                from libs.error_code.exception import CustomError

                raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

            has_access = await check_ticket_flow_permission(
                request, user, flow_id, required_permissions
            )
            if not has_access:
                from core.error_code import ErrorCode
                from libs.error_code.exception import CustomError

                raise CustomError(ErrorCode.TICKET_FLOW_PERM_DENIED)

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


async def check_ticket_flow_permission(request, user, flow_id, required_permissions):
    """检查用户是否拥有工单流程的指定权限（对标 check_playbook_permission）。

    :param user: 用户对象(需包含id和roles属性)
    :param flow_id: 工单流程 id
    :param required_permissions: 需要的权限列表，如 ('r', 'u')
    :return: bool 表示是否拥有权限
    """
    tenant_id = request.ctx.session.get("tenant_id")

    # 超管豁免
    if is_superadmin(user, tenant_id):
        return True

    user_id = str(user.id)
    user_roles = set(str(role.id) for role in user.roles)

    # 检查用户直授权限
    if await _has_ticket_flow_permission_record(
        flow_id, tenant_id, required_permissions, users_id=user_id
    ):
        return True

    # 检查角色授权
    for role_id in user_roles:
        if await _has_ticket_flow_permission_record(
            flow_id, tenant_id, required_permissions, roles_id=role_id
        ):
            return True

    return False


async def _has_ticket_flow_permission_record(
    flow_id: str, tenant_id: str, required_permissions, **filters
):
    """查询 TicketFlowPermission 表，判断是否有满足条件的权限记录。"""
    from models.ticket import TicketFlowPermission

    records = await TicketFlowPermission.filter(
        flow_id=flow_id, tenant_id=tenant_id, deleted=False, **filters
    ).all()
    for record in records:
        perms = set(record.permissions or [])
        if all(p in perms for p in required_permissions):
            return True
    return False


async def ticket_flow_ids_for_user(
    account, tenant_id: str, permission: str = "r"
) -> set[str]:
    """返回用户有指定权限的工单流程 id 集合（对标 playbook_ids_for_user）。

    供列表端点做 Q(id__in=flow_ids) 过滤。
    """
    if is_superadmin(account, tenant_id):
        from models.ticket import TicketFlow

        flows = await TicketFlow.filter(tenant_id=tenant_id, deleted=False).all()
        return {str(f.id) for f in flows}

    from models.ticket import TicketFlowPermission

    user_id = str(account.id)
    role_ids = [str(role.id) for role in account.roles]

    records = await TicketFlowPermission.filter(
        tenant_id=tenant_id, deleted=False
    ).filter(
        Q(users_id=user_id) | Q(roles_id__in=role_ids) if role_ids else Q(users_id=user_id)
    ).all()

    flow_ids = set()
    for record in records:
        if permission in (record.permissions or []):
            flow_ids.add(str(record.flow_id))
    return flow_ids


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
