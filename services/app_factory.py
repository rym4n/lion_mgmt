# coding=utf-8

import functools
from importlib import import_module
from time import perf_counter
import traceback

import orjson
from sanic import Sanic
from sanic.log import logger
from sanic_ext import openapi  # noqa: F401
from tortoise.contrib.sanic import register_tortoise

from core.exception_handlers import EXC_HDL_TUPLE, BaseExcHandler
from core.listeners import LISTENER_TUPLE, BaseListener
from core.middlewares import MIDDLEWARE_TUPLE, BaseMiddleware
from core.openapi import configure_openapi_responses
from libs.security.authorization import get_license_sha256, load_license
from libs.security.license_modules import is_licensed_module_active
from services.dashboard.views import dashboard_bp
from services.inspection.views import inspection_bp
from services.knowledge.views import knowledge_bp
from services.knowledge.agent_views import knowledge_agent_bp
from services.license.views import license_bp
from services.others.views import activity, all_apps, playbook_list
from services.scene.views import scene_bp
from services.secure.views import secure_bp
from services.system_config.views import system_bp
from services.tenant.views import tenant_bp
from services.user.views import user_bp
from services.warroom.views import warroom_bp
from settings import (
    BASE_LOGGING,
    HEALTH_MAX_MISSES,
    HEALTH_MISSED_THRESHHOLD,
    HEALTH_MONITOR,
    TORTOISE,
)


BlueprintSpec = tuple[str, str]

LICENSED_BLUEPRINT_SPECS: dict[str, tuple[BlueprintSpec, ...]] = {
    "event_bp": (("services.event.views", "event_bp"),),
    "event_ingestion_bp": (("services.event_ingestion.views", "event_ingestion_bp"),),
    "role_bp": (("services.role.views", "role_bp"),),
    "timed_bp": (("services.timed.views", "timed_bp"),),
    "tool_management_bp": (("services.tools.views", "tools_bp"),),
    "var_bp": (("services.variables.views", "var_bp"),),
    "workflow_bp": (
        ("services.workflow.views", "workflow_bp"),
        ("services.approvement.views", "approve_bp"),
    ),
    "logs_bp": (("services.log_view.views", "logs_bp"),),
    "ai_bp": (
        ("services.ai.views", "ai_bp"),
        ("services.llm_config.views", "llm_config_bp"),
    ),
    "agent_bp": (("services.agent.views", "agent_bp"),),
    "mcp_bp": (("services.mcp.views", "mcp_bp"),),
    "command_bp": (("services.command_center.views", "command_bp"),),
    "digital_employee_bp": (
        ("services.digital_employee.views", "digital_employee_bp"),
    ),
    "knowledge_bp": (
        ("services.knowledge.views", "knowledge_bp"),
        ("services.knowledge.embedding_views", "knowledge_embedding_bp"),
    ),
    "large_screen_bp": (("services.large_screen.views", "large_screen_bp"),),
    "asset_management_bp": (
        ("services.asset_management.views", "asset_management_bp"),
    ),
    "network_access_control_bp": (
        ("services.network_access_control.views", "network_access_control_bp"),
        ("services.network_access_control.collection.views", "policy_collection_bp"),
        ("services.network_access_control.collection.agent_views", "policy_agent_internal_bp"),
    ),
    "topology_bp": (
        ("services.network_topology.views", "topology_bp"),
        ("services.network_topology.upload_views", "upload_bp"),
        ("services.network_topology.collection.views", "collection_bp"),
        ("services.network_topology.agent_views", "agent_internal_bp"),
        ("services.network_topology.agent_maintenance_views", "agent_maintenance_bp"),
    ),
    "ticket_bp": (("services.ticket.views", "ticket_bp"),),
}

# 老 license 中按名称识别的模块 → 新的 LICENSED_BLUEPRINT_SPECS key
LEGACY_LICENSE_MODULE_ALIASES: dict[str, str] = {
    "大屏管理": "large_screen_bp",
}


def create_app(process_started_at: float | None = None) -> Sanic:
    process_started_at = (
        process_started_at if process_started_at is not None else perf_counter()
    )
    create_started_at = perf_counter()
    app: Sanic = Sanic(
        "services", dumps=orjson.dumps, loads=orjson.loads, log_config=BASE_LOGGING
    )
    app.ctx.process_started_at = process_started_at
    app.ctx.worker_ready = False
    app.config.update_config("settings/__init__.py")
    app.extend(
        config={
            "HEALTH_MAX_MISSES": HEALTH_MAX_MISSES,
            "HEALTH_MISSED_THRESHHOLD": HEALTH_MISSED_THRESHHOLD,
            "HEALTH_MONITOR": HEALTH_MONITOR,
        }
    )
    register_tortoise(app, config=TORTOISE, generate_schemas=False)
    register_listener(app)
    register_middleware(app)
    register_exception_handler(app)
    register_blueprints(app)
    configure_openapi_responses()
    logger.info(
        "应用构建完成: create_app=%.3fs process_elapsed=%.3fs routes=%s",
        perf_counter() - create_started_at,
        perf_counter() - process_started_at,
        len(app.router.routes),
    )
    return app


def _resolve_blueprints(specs: tuple[BlueprintSpec, ...]) -> tuple:
    blueprints = []
    for module_path, attribute in specs:
        started_at = perf_counter()
        module = import_module(module_path)
        blueprints.append(getattr(module, attribute))
        logger.info(
            "蓝图模块导入完成: module=%s duration=%.3fs",
            module_path,
            perf_counter() - started_at,
        )
    return tuple(blueprints)


def _licensed_blueprints() -> dict:
    """Resolve every licensed blueprint for compatibility and contract tests."""
    result = {}
    for module, specs in LICENSED_BLUEPRINT_SPECS.items():
        blueprints = _resolve_blueprints(specs)
        result[module] = blueprints[0] if len(blueprints) == 1 else blueprints
    return result


def _resolve_licensed_module_key(item: dict) -> str | None:
    """Map a license moduleList entry to a LICENSED_BLUEPRINT_SPECS key.

    历史 license 把「大屏管理」和「仪表盘」都签成 module=dashboard_bp，二者无法单独
    授权。大屏改用独立的 large_screen_bp 后，老 license 仍按名称识别，避免已签发的
    授权在升级后出现「菜单可见但接口 404」的割裂状态。
    """
    module = item.get("module")
    if module in LICENSED_BLUEPRINT_SPECS:
        return module
    name = item.get("name")
    if not name:
        return None
    return LEGACY_LICENSE_MODULE_ALIASES.get(name)


def _register_licensed_blueprints(app: Sanic, license_data: dict) -> set[str]:
    registered_modules = set()
    registered_blueprint_names = set()
    for item in license_data["moduleList"]:
        module = _resolve_licensed_module_key(item)
        if not module or module in registered_modules:
            continue
        if not is_licensed_module_active(license_data, item):
            continue
        for blueprint in _resolve_blueprints(LICENSED_BLUEPRINT_SPECS[module]):
            blueprint_name = getattr(blueprint, "name", None) or id(blueprint)
            if blueprint_name in registered_blueprint_names:
                continue
            app.blueprint(blueprint)
            registered_blueprint_names.add(blueprint_name)
        registered_modules.add(module)
    return registered_modules


def register_blueprints(app: Sanic):
    app.add_route(activity, "/activity/<activityId>")
    app.add_route(playbook_list, "/playbooks", methods=["POST"])
    app.add_route(all_apps, "/apps", methods=["POST"])
    for blueprint in (
        license_bp,
        user_bp,
        scene_bp,
        dashboard_bp,
        system_bp,
        secure_bp,
        warroom_bp,
        tenant_bp,
        inspection_bp,
        knowledge_bp,
        knowledge_agent_bp,
    ):
        app.blueprint(blueprint)
    license_data = None
    registered_modules = set()
    try:
        license_data = load_license()
        if license_data and "moduleList" in license_data:
            registered_modules = _register_licensed_blueprints(app, license_data)
    except Exception as ex:
        logger.info(f"{ex} | {traceback.format_exc()}")
    if "ticket_bp" not in registered_modules:
        logger.warning("ticket 模块未授权，跳过加载")
    else:
        ticket_ws_bp = _resolve_blueprints(
            (("services.ticket.websocket", "ticket_ws_bp"),)
        )[0]
        app.blueprint(ticket_ws_bp)
    app.ctx.license_data = license_data
    app.ctx.active_license_sha256 = get_license_sha256() if license_data else None
    app.ctx.registered_license_modules = frozenset(registered_modules)
    app.ctx.supported_license_modules = frozenset(LICENSED_BLUEPRINT_SPECS)


def register_listener(app: Sanic):
    for listener_cls in LISTENER_TUPLE:
        listener: BaseListener = listener_cls()
        for event in (
            "before_server_start",
            "after_server_start",
            "before_server_stop",
            "after_server_stop",
        ):
            if hasattr(listener, event):
                callback = getattr(listener, event)
                if event in {"before_server_start", "after_server_start"}:
                    callback = _timed_listener(listener_cls.__name__, event, callback)
                app.register_listener(callback, event)

    async def mark_worker_ready(app: Sanic, _loop) -> None:
        app.ctx.worker_ready = True
        logger.info(
            "工作进程启动就绪: duration=%.3fs license_sha256=%s modules=%s",
            perf_counter() - app.ctx.process_started_at,
            app.ctx.active_license_sha256,
            sorted(app.ctx.registered_license_modules),
        )

    app.register_listener(mark_worker_ready, "after_server_start")


def _timed_listener(listener_name: str, event: str, callback):
    @functools.wraps(callback)
    async def wrapped(app: Sanic, loop):
        started_at = perf_counter()
        try:
            return await callback(app, loop)
        finally:
            logger.info(
                "启动监听器完成: listener=%s event=%s duration=%.3fs",
                listener_name,
                event,
                perf_counter() - started_at,
            )

    wrapped.__name__ = f"{listener_name}_{event}"
    return wrapped


def register_middleware(app: Sanic):
    for middle_cls in MIDDLEWARE_TUPLE:
        middle: BaseMiddleware = middle_cls()
        if hasattr(middle, "before_request"):
            app.register_middleware(middle.before_request, attach_to="request")
        if hasattr(middle, "before_response"):
            app.register_middleware(middle.before_response, attach_to="response")


def register_exception_handler(app: Sanic):
    for exc_hdl_cls in EXC_HDL_TUPLE:
        exc_hdl: BaseExcHandler = exc_hdl_cls()
        app.error_handler.add(exc_hdl.get_exception(), exc_hdl.handle)
