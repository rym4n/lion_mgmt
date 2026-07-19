# coding=utf-8

from datetime import datetime
import math

import orjson
from sanic import Blueprint, json as sanic_json
from sanic.request import Request
from sanic_ext import openapi
from tortoise.expressions import RawSQL
from tortoise.transactions import in_transaction

from core.response import response_ok
from libs.security.authorization import (
    action_permission_hits,
    is_superadmin,
    requires_permission,
)
from libs.utils import strip_timezone_to_seconds
from models import (
    ActionPermissions,
    Tool,
    ToolAction,
    ToolResources,
    ToolResourcesModel,
)
from models.network_topology import NetworkTopology
from services.network_topology.refresh import (
    is_topology_collection_action,
    validate_action_sources,
)
from settings import TZ

topology_bp = Blueprint("network_topology", url_prefix="/network-topology")
__all__ = ["topology_bp"]

_MIN_REFRESH_INTERVAL = 300
_MAX_REFRESH_INTERVAL = 86400
_MAX_ACTION_SOURCES = 20
_MAX_LAYOUT_NODES = 2000
_MAX_LAYOUT_COORDINATE = 10_000_000
_SOURCE_OPTION_KINDS = {"tools", "versions", "actions", "resources"}
_SAFE_ACTION_INPUT_FIELDS = (
    "key",
    "type",
    "required",
    "description",
    "options",
    "default",
)
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "community",
    "credential",
    "private_key",
    "private-key",
    "api_key",
    "apikey",
)


def _err(message: str, status: int = 400):
    return sanic_json(
        {"code": 4000, "message": message}, status=status, dumps=orjson.dumps
    )


def _parse_pagination(data: dict) -> tuple[int, int]:
    try:
        page = max(int(data.get("page", 1)), 1)
        page_size = min(max(int(data.get("page_size", 15)), 1), 100)
    except (TypeError, ValueError):
        raise ValueError("分页参数不合法")
    return page, page_size


def _parse_enabled(value, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError("启用状态不合法")


def _parse_refresh_interval(value) -> int:
    try:
        interval = int(value if value is not None else 3600)
    except (TypeError, ValueError):
        raise ValueError("刷新周期不合法")
    if not _MIN_REFRESH_INTERVAL <= interval <= _MAX_REFRESH_INTERVAL:
        raise ValueError("刷新周期必须在 5 分钟到 24 小时之间")
    return interval


def _parse_definition(
    data: dict, *, default_enabled: bool = True
) -> tuple[str, list, int, bool]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("拓扑名称不能为空")
    if len(name) > 200:
        raise ValueError("拓扑名称不能超过 200 个字符")

    action_sources = data.get("action_sources")
    if not isinstance(action_sources, list) or not action_sources:
        raise ValueError("请至少配置一个应用动作数据源")
    if len(action_sources) > _MAX_ACTION_SOURCES:
        raise ValueError(f"应用动作数据源不能超过 {_MAX_ACTION_SOURCES} 个")

    return (
        name,
        action_sources,
        _parse_refresh_interval(data.get("refresh_interval")),
        _parse_enabled(data.get("enabled"), default=default_enabled),
    )


def _normalize_layout(layout_json: dict, topology_json: dict | None) -> dict:
    nodes = (topology_json or {}).get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("拓扑节点数据格式错误")
    if len(nodes) > _MAX_LAYOUT_NODES or len(layout_json) > _MAX_LAYOUT_NODES:
        raise ValueError(f"拓扑节点不能超过 {_MAX_LAYOUT_NODES} 个")

    node_ids = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError("拓扑节点数据格式错误")
        raw_id = node.get("id")
        node_id = str(raw_id).strip() if raw_id is not None else ""
        node_ids.add(node_id or f"node-{index + 1}")

    if set(layout_json) != node_ids:
        raise ValueError("布局节点与当前拓扑不一致，请刷新拓扑后重试")

    normalized = {}
    for node_id, position in layout_json.items():
        if not isinstance(position, dict):
            raise ValueError(f"节点 {node_id} 的坐标格式错误")
        x = position.get("x")
        y = position.get("y")
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
        ):
            raise ValueError(f"节点 {node_id} 的坐标必须是数字")
        x_value = float(x)
        y_value = float(y)
        if (
            not math.isfinite(x_value)
            or not math.isfinite(y_value)
            or abs(x_value) > _MAX_LAYOUT_COORDINATE
            or abs(y_value) > _MAX_LAYOUT_COORDINATE
        ):
            raise ValueError(f"节点 {node_id} 的坐标超出有效范围")
        normalized[node_id] = {"x": x_value, "y": y_value}
    return normalized


async def _tenant_topology(
    topology_id: str,
    tenant_id: str,
    *,
    connection=None,
    lock: bool = False,
    fields: tuple[str, ...] | None = None,
):
    query = NetworkTopology.filter(id=topology_id, deleted=False, tenant_id=tenant_id)
    if connection is not None:
        query = query.using_db(connection)
    if lock:
        query = query.select_for_update()
    if fields:
        query = query.only(*fields)
    return await query.first()


def _looks_like_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace(" ", "_")
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


async def _definition_secret_keys(
    action_sources: list | None,
    tenant_id: str,
) -> dict[str, set[str] | None]:
    action_ids = {
        str(source.get("action_id") or "")
        for source in action_sources or []
        if isinstance(source, dict) and source.get("action_id")
    }
    if not action_ids:
        return {}
    actions = (
        await ToolAction.filter(
            id__in=action_ids,
            tenant_id=tenant_id,
            deleted=False,
        )
        .only("id", "action")
        .all()
    )
    # None means the referenced action no longer exists, so its schema cannot
    # safely distinguish credentials from ordinary input fields.
    result: dict[str, set[str] | None] = {action_id: None for action_id in action_ids}
    for action in actions:
        action_data = action.action if isinstance(action.action, dict) else {}
        keys = set()
        for definition in action_data.get("input") or []:
            if not isinstance(definition, dict):
                continue
            key = str(definition.get("key") or "").strip()
            input_type = str(
                definition.get("type") or definition.get("data_type") or ""
            ).lower()
            if key and (input_type == "password" or _looks_like_secret_key(key)):
                keys.add(key)
        result[str(action.id)] = keys
    return result


def _redact_action_sources(
    action_sources: list | None,
    secret_keys_by_action: dict[str, set[str] | None] | None = None,
) -> list[dict]:
    redacted = []
    for source in action_sources or []:
        if not isinstance(source, dict):
            continue
        safe_source = {
            key: value for key, value in source.items() if key != "secret_keys"
        }
        secret_keys = {
            str(key) for key in source.get("secret_keys") or [] if str(key).strip()
        }
        action_id = str(source.get("action_id") or "")
        definition_secret_keys = (secret_keys_by_action or {}).get(action_id)
        if definition_secret_keys:
            secret_keys.update(definition_secret_keys)
        unknown_action = (
            secret_keys_by_action is not None
            and action_id in secret_keys_by_action
            and definition_secret_keys is None
        )
        action_input = source.get("action_input")
        if isinstance(action_input, dict):
            safe_input = {}
            for key, value in action_input.items():
                key_text = str(key)
                if (
                    unknown_action
                    or key_text in secret_keys
                    or _looks_like_secret_key(key_text)
                    or (isinstance(value, str) and value.startswith("enc::"))
                ):
                    safe_input[key_text] = "********"
                else:
                    safe_input[key_text] = value
            safe_source["action_input"] = safe_input
        redacted.append(safe_source)
    return redacted


def _action_func(action: ToolAction) -> str:
    if not isinstance(action.action, dict):
        return ""
    return str(action.action.get("func") or "").strip()


def _safe_action_inputs(action: ToolAction) -> list[dict]:
    action_data = action.action if isinstance(action.action, dict) else {}
    safe_inputs = []
    for definition in action_data.get("input") or []:
        if not isinstance(definition, dict) or not definition.get("key"):
            continue
        item = {
            key: definition[key]
            for key in _SAFE_ACTION_INPUT_FIELDS
            if key in definition
        }
        input_type = str(item.get("type") or definition.get("data_type") or "string")
        is_secret = input_type.lower() == "password" or _looks_like_secret_key(
            str(item.get("key") or "")
        )
        item["type"] = "password" if is_secret else input_type
        item["required"] = item.get("required") is True or str(
            item.get("required") or ""
        ).lower() in {
            "1",
            "true",
            "yes",
        }
        # Credential defaults are secrets even when legacy apps declare them as text.
        if is_secret:
            item.pop("default", None)
        safe_inputs.append(item)
    return safe_inputs


def _permissions_by_tool(permissions: list[ActionPermissions]):
    permissions_by_tool: dict[str, list[ActionPermissions]] = {}
    for permission in permissions:
        permissions_by_tool.setdefault(str(permission.tool_name or ""), []).append(
            permission
        )
    return permissions_by_tool


def _permitted_resources(
    *,
    account,
    tenant_id: str,
    action: ToolAction,
    resources: list[ToolResources],
    permissions_by_tool: dict[str, list[ActionPermissions]],
) -> list[ToolResources]:
    if is_superadmin(account, tenant_id=tenant_id):
        return resources
    user_id = str(getattr(account, "id", "") or "")
    role_ids = {
        str(role.id)
        for role in (getattr(account, "roles", None) or [])
        if getattr(role, "tenant_id", None) == tenant_id
    }
    action_func = _action_func(action)
    permissions = [
        permission
        for permission in permissions_by_tool.get(str(action.tool_name or ""), [])
        if permission.action_name in {action_func, "all"}
    ]
    return [
        resource
        for resource in resources
        if any(
            action_permission_hits(
                permission,
                str(resource.id),
                user_id,
                role_ids,
            )
            for permission in permissions
        )
    ]


def _action_is_available(
    *,
    account,
    tenant_id: str,
    action: ToolAction,
    resource_required: dict[str, bool],
    resources_by_tool: dict[str, list[ToolResources]],
    permissions_by_tool: dict[str, list[ActionPermissions]],
) -> bool:
    tool_name = str(action.tool_name or "")
    if not _action_func(action) or not is_topology_collection_action(action.action):
        return False
    if not resource_required.get(tool_name, False):
        return True
    return bool(
        _permitted_resources(
            account=account,
            tenant_id=tenant_id,
            action=action,
            resources=resources_by_tool.get(tool_name, []),
            permissions_by_tool=permissions_by_tool,
        )
    )


async def _load_source_option_context(
    tenant_id: str, account, *, tool_name: str | None = None
):
    tool_query = Tool.filter(deleted=False, tenant_id=tenant_id, maintain=False).only(
        "id", "name", "version", "description"
    )
    action_query = ToolAction.filter(deleted=False, tenant_id=tenant_id).only(
        "id", "tool_name", "version_number", "action"
    )
    # Deliberately never select ToolResources.resources: source selectors only
    # need identity metadata and must not decrypt credentials/configuration.
    resource_query = ToolResources.filter(
        deleted=False,
        tenant_id=tenant_id,
        internal=False,
    ).only("id", "tool_name", "name")
    resource_model_query = ToolResourcesModel.filter(
        deleted=False, tenant_id=tenant_id
    ).only("id", "tool_name", "status")
    permission_query = ActionPermissions.filter(
        deleted=False, tenant_id=tenant_id
    ).only("id", "tool_name", "action_name", "users", "roles")
    if tool_name:
        tool_query = tool_query.filter(name=tool_name)
        action_query = action_query.filter(tool_name=tool_name)
        resource_query = resource_query.filter(tool_name=tool_name)
        resource_model_query = resource_model_query.filter(tool_name=tool_name)
        permission_query = permission_query.filter(tool_name=tool_name)

    tools = await tool_query.all()
    actions = await action_query.all()
    resources = await resource_query.all()
    resource_models = await resource_model_query.all()
    permissions = await permission_query.all()

    tools_by_key = {
        (str(tool.name or ""), str(tool.version or "")): tool for tool in tools
    }
    actions = [
        action
        for action in actions
        if (str(action.tool_name or ""), str(action.version_number or ""))
        in tools_by_key
    ]
    resources_by_tool: dict[str, list[ToolResources]] = {}
    for resource in resources:
        resources_by_tool.setdefault(str(resource.tool_name or ""), []).append(resource)
    resource_required: dict[str, bool] = {}
    for resource_model in resource_models:
        tool_key = str(resource_model.tool_name or "")
        resource_required[tool_key] = (
            resource_required.get(tool_key, False) or resource_model.status == 1
        )
    permissions_by_tool = _permissions_by_tool(permissions)
    return (
        tools_by_key,
        actions,
        resources_by_tool,
        resource_required,
        permissions_by_tool,
    )


@topology_bp.post("/source-options")
@openapi.definition(summary="获取网络拓扑采集动作的最小可选项")
@requires_permission("network_topology:c", "network_topology:u")
async def source_options(request: Request):
    data = request.json or {}
    kind = str(data.get("kind") or "").strip()
    if kind not in _SOURCE_OPTION_KINDS:
        return _err("kind 必须是 tools、versions、actions 或 resources")

    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    if not tenant_id or not account:
        return _err("执行账户或租户不存在")

    tool_name = str(data.get("tool_name") or "").strip()
    if kind in {"versions", "actions"} and not tool_name:
        return _err("tool_name 不能为空")

    tool_version = str(data.get("tool_version") or "").strip()
    if kind == "actions" and not tool_version:
        return _err("tool_version 不能为空")

    action_id = str(data.get("action_id") or "").strip()
    if kind == "resources" and not action_id:
        return _err("action_id 不能为空")

    context_tool_name = tool_name
    selected_action = None
    if kind == "resources":
        selected_action = await ToolAction.get_or_none(
            id=action_id,
            deleted=False,
            tenant_id=tenant_id,
        )
        if not selected_action:
            return _err("动作不存在", status=404)
        if not is_topology_collection_action(selected_action.action):
            return _err("该动作不是只读查询动作，不能用于拓扑采集", status=400)
        context_tool_name = str(selected_action.tool_name or "")

    (
        tools_by_key,
        actions,
        resources_by_tool,
        resource_required,
        permissions_by_tool,
    ) = await _load_source_option_context(
        tenant_id,
        account,
        tool_name=context_tool_name or None,
    )
    available_actions = [
        action
        for action in actions
        if _action_is_available(
            account=account,
            tenant_id=tenant_id,
            action=action,
            resource_required=resource_required,
            resources_by_tool=resources_by_tool,
            permissions_by_tool=permissions_by_tool,
        )
    ]

    if kind == "tools":
        available_tool_names = {
            str(action.tool_name or "") for action in available_actions
        }
        labels = {}
        for (name, _version), tool in tools_by_key.items():
            if name in available_tool_names:
                labels.setdefault(name, str(tool.description or name))
        items = [
            {"name": name, "label": label}
            for name, label in sorted(
                labels.items(), key=lambda item: (item[1], item[0])
            )
        ]
    elif kind == "versions":
        available_versions = {
            str(action.version_number or "") for action in available_actions
        }
        items = [
            {"id": str(tool.id), "version": version, "label": version}
            for (name, version), tool in sorted(
                tools_by_key.items(),
                key=lambda item: item[0][1],
                reverse=True,
            )
            if name == tool_name and version in available_versions
        ]
    elif kind == "actions":
        items = []
        for action in available_actions:
            if str(action.version_number or "") != tool_version:
                continue
            action_data = action.action if isinstance(action.action, dict) else {}
            items.append(
                {
                    "id": str(action.id),
                    "name": str(
                        action_data.get("name") or action_data.get("func") or ""
                    ),
                    "func": _action_func(action),
                    "input": _safe_action_inputs(action),
                    "resource_required": resource_required.get(
                        str(action.tool_name or ""),
                        False,
                    ),
                }
            )
        items.sort(key=lambda item: (item["name"], item["id"]))
    else:
        matching_action = next(
            (action for action in actions if str(action.id) == action_id),
            None,
        )
        if (
            not matching_action
            or (
                str(matching_action.tool_name or ""),
                str(matching_action.version_number or ""),
            )
            not in tools_by_key
        ):
            return _err("动作对应的应用不存在", status=404)
        permitted = _permitted_resources(
            account=account,
            tenant_id=tenant_id,
            action=matching_action,
            resources=resources_by_tool.get(str(matching_action.tool_name or ""), []),
            permissions_by_tool=permissions_by_tool,
        )
        items = sorted(
            [
                {"id": str(resource.id), "name": str(resource.name or "")}
                for resource in permitted
            ],
            key=lambda item: (item["name"], item["id"]),
        )

    return response_ok({"items": items})


@topology_bp.post("/parse")
@openapi.definition(summary="旧版手工配置解析接口（已停用）")
@requires_permission("network_topology:r")
async def parse_topology(_request: Request):
    return _err("手工解析拓扑已停用，请配置应用动作数据源", status=410)


@topology_bp.post("/save")
@openapi.definition(summary="旧版手工保存接口（已停用）")
@requires_permission("network_topology:u")
async def save_topology(_request: Request):
    return _err("手工保存拓扑已停用，请配置应用动作数据源", status=410)


@topology_bp.post("/create")
@openapi.definition(summary="创建配置驱动的网络拓扑")
@requires_permission("network_topology:c")
async def create_topology(request: Request):
    data = request.json or {}
    try:
        name, action_sources, refresh_interval, enabled = _parse_definition(data)
    except ValueError as exc:
        return _err(str(exc))

    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    if not account:
        return _err("执行账户不存在")
    if await NetworkTopology.filter(
        name=name, deleted=False, tenant_id=tenant_id
    ).exists():
        return _err("拓扑名称已存在")

    try:
        normalized_sources = await validate_action_sources(
            action_sources, tenant_id, account
        )
    except ValueError as exc:
        return _err(str(exc))

    now = datetime.now(TZ)
    topology = await NetworkTopology.create(
        name=name,
        source_config="",
        topology_json={"nodes": [], "edges": []},
        layout_json={},
        action_sources=normalized_sources,
        refresh_interval=refresh_interval,
        enabled=enabled,
        executor_id=str(account.id),
        refresh_status="pending" if enabled else "disabled",
        next_refresh_at=now if enabled else None,
        tenant_id=tenant_id,
    )
    return response_ok(topology.json)


@topology_bp.post("/update")
@openapi.definition(summary="更新网络拓扑采集定义")
@requires_permission("network_topology:u")
async def update_topology(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    try:
        name, action_sources, refresh_interval, enabled = _parse_definition(data)
    except ValueError as exc:
        return _err(str(exc))

    tenant_id = request.ctx.session.get("tenant_id")
    account = request.ctx.session.get("account_info")
    if not account:
        return _err("执行账户不存在")
    duplicate = (
        await NetworkTopology.filter(name=name, deleted=False, tenant_id=tenant_id)
        .exclude(id=topology_id)
        .exists()
    )
    if duplicate:
        return _err("拓扑名称已存在")

    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)

        expected_revision = data.get("revision")
        if expected_revision is not None:
            try:
                if int(expected_revision) != topology.revision:
                    return _err("拓扑配置已被更新，请刷新后重试", status=409)
            except (TypeError, ValueError):
                return _err("拓扑配置版本不合法")

        try:
            normalized_sources = await validate_action_sources(
                action_sources,
                tenant_id,
                account,
                existing_action_sources=topology.action_sources,
            )
        except ValueError as exc:
            return _err(str(exc))

        topology.name = name
        topology.action_sources = normalized_sources
        topology.refresh_interval = refresh_interval
        topology.enabled = enabled
        topology.executor_id = str(account.id)
        topology.revision += 1
        topology.refresh_status = "pending" if enabled else "disabled"
        topology.next_refresh_at = datetime.now(TZ) if enabled else None
        topology.refresh_started_at = None
        topology.refresh_token = None
        topology.refresh_error = None
        await topology.save(
            using_db=connection,
            update_fields=[
                "name",
                "action_sources",
                "refresh_interval",
                "enabled",
                "executor_id",
                "revision",
                "refresh_status",
                "next_refresh_at",
                "refresh_started_at",
                "refresh_token",
                "refresh_error",
                "utime",
            ],
        )

    return response_ok(topology.json)


@topology_bp.post("/list")
@openapi.definition(summary="获取网络拓扑列表")
@requires_permission("network_topology:r")
async def list_topology(request: Request):
    data = request.json or {}
    try:
        page, page_size = _parse_pagination(data)
    except ValueError as exc:
        return _err(str(exc))

    tenant_id = request.ctx.session.get("tenant_id")
    queryset = NetworkTopology.filter(deleted=False, tenant_id=tenant_id)
    name = str(data.get("name") or "").strip()
    if name:
        queryset = queryset.filter(name__icontains=name)

    total = await queryset.count()
    item_data = await (
        queryset.annotate(
            source_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"action_sources\") = 'array' "
                'THEN jsonb_array_length("action_sources") ELSE 0 END'
            ),
            node_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"topology_json\"->'nodes') = 'array' "
                "THEN jsonb_array_length(\"topology_json\"->'nodes') ELSE 0 END"
            ),
            edge_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"topology_json\"->'edges') = 'array' "
                "THEN jsonb_array_length(\"topology_json\"->'edges') ELSE 0 END"
            ),
        )
        .order_by("-utime", "-ctime")
        .offset((page - 1) * page_size)
        .limit(page_size)
        .values(
            "id",
            "name",
            "source_count",
            "node_count",
            "edge_count",
            "refresh_interval",
            "enabled",
            "revision",
            "refresh_status",
            "last_refresh_at",
            "next_refresh_at",
            "refresh_error",
            "ctime",
            "utime",
        )
    )
    for item in item_data:
        for field in ("last_refresh_at", "next_refresh_at", "ctime", "utime"):
            item[field] = strip_timezone_to_seconds(item.get(field))

    return response_ok(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": item_data,
        }
    )


@topology_bp.post("/detail")
@openapi.definition(summary="获取网络拓扑详情")
@requires_permission("network_topology:r")
async def get_topology(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    topology = await _tenant_topology(
        topology_id,
        request.ctx.session.get("tenant_id"),
        fields=(
            "id",
            "name",
            "topology_json",
            "layout_json",
            "refresh_interval",
            "enabled",
            "revision",
            "refresh_status",
            "last_refresh_at",
            "next_refresh_at",
            "refresh_error",
            "source_hash",
            "ctime",
            "utime",
        ),
    )
    if not topology:
        return _err("拓扑数据不存在", status=404)
    topology_json = topology.topology_json or {}
    payload = {
        "id": topology.id,
        "name": topology.name,
        "topology_json": topology_json,
        "layout_json": topology.layout_json,
        "node_count": len(topology_json.get("nodes", [])),
        "edge_count": len(topology_json.get("edges", [])),
        "refresh_interval": topology.refresh_interval,
        "enabled": topology.enabled,
        "revision": topology.revision,
        "refresh_status": topology.refresh_status,
        "last_refresh_at": strip_timezone_to_seconds(topology.last_refresh_at),
        "next_refresh_at": strip_timezone_to_seconds(topology.next_refresh_at),
        "refresh_error": topology.refresh_error,
        "source_hash": topology.source_hash,
        "ctime": strip_timezone_to_seconds(topology.ctime),
        "utime": strip_timezone_to_seconds(topology.utime),
    }
    return response_ok(payload)


@topology_bp.post("/definition")
@openapi.definition(summary="获取网络拓扑采集定义")
@requires_permission("network_topology:u")
async def get_topology_definition(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    topology = await _tenant_topology(
        topology_id,
        request.ctx.session.get("tenant_id"),
        fields=(
            "id",
            "name",
            "refresh_interval",
            "enabled",
            "revision",
            "action_sources",
        ),
    )
    if not topology:
        return _err("拓扑数据不存在", status=404)
    secret_keys_by_action = await _definition_secret_keys(
        topology.action_sources,
        request.ctx.session.get("tenant_id"),
    )
    return response_ok(
        {
            "id": topology.id,
            "name": topology.name,
            "refresh_interval": topology.refresh_interval,
            "enabled": topology.enabled,
            "revision": topology.revision,
            "action_sources": _redact_action_sources(
                topology.action_sources,
                secret_keys_by_action,
            ),
        }
    )


@topology_bp.post("/refresh")
@openapi.definition(summary="立即刷新网络拓扑")
@requires_permission("network_topology:u")
async def refresh_topology(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    tenant_id = request.ctx.session.get("tenant_id")
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        if topology.refresh_status == "running":
            return response_ok(topology.json)
        if not topology.action_sources:
            return _err("请先配置应用动作数据源")

        topology.refresh_status = "pending"
        topology.next_refresh_at = datetime.now(TZ)
        topology.refresh_started_at = None
        topology.refresh_token = None
        topology.refresh_error = None
        await topology.save(
            using_db=connection,
            update_fields=[
                "refresh_status",
                "next_refresh_at",
                "refresh_started_at",
                "refresh_token",
                "refresh_error",
                "utime",
            ],
        )
    return response_ok(topology.json)


@topology_bp.post("/layout")
@openapi.definition(summary="保存自动拓扑的节点布局")
@requires_permission("network_topology:u")
async def update_layout(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    layout_json = data.get("layout_json")
    if not topology_id:
        return _err("拓扑ID不能为空")
    if not isinstance(layout_json, dict):
        return _err("layout_json 必须是对象")
    if "source_hash" not in data:
        return _err("source_hash 不能为空")
    expected_source_hash = data.get("source_hash")
    if expected_source_hash is not None and not isinstance(expected_source_hash, str):
        return _err("source_hash 格式错误")

    tenant_id = request.ctx.session.get("tenant_id")
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id,
            tenant_id,
            connection=connection,
            lock=True,
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        current_source_hash = topology.source_hash or ""
        if (expected_source_hash or "") != current_source_hash:
            return _err("拓扑已自动刷新，请重新打开后保存布局", status=409)
        try:
            normalized_layout = _normalize_layout(layout_json, topology.topology_json)
        except ValueError as exc:
            return _err(str(exc))
        topology.layout_json = normalized_layout
        await topology.save(
            using_db=connection,
            update_fields=["layout_json", "utime"],
        )
    return response_ok({"id": topology.id, "source_hash": current_source_hash})


@topology_bp.post("/delete")
@openapi.definition(summary="删除网络拓扑")
@requires_permission("network_topology:d")
async def delete_topology(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    tenant_id = request.ctx.session.get("tenant_id")
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        topology.deleted = True
        topology.enabled = False
        topology.revision += 1
        topology.next_refresh_at = None
        topology.refresh_started_at = None
        topology.refresh_token = None
        topology.refresh_status = "disabled"
        await topology.save(
            using_db=connection,
            update_fields=[
                "deleted",
                "enabled",
                "revision",
                "next_refresh_at",
                "refresh_started_at",
                "refresh_token",
                "refresh_status",
                "utime",
            ],
        )
    return response_ok({"id": topology.id})
