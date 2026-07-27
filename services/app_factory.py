# coding=utf-8

import traceback

import orjson
from sanic import Sanic
from sanic.log import logger
from sanic_ext import openapi  # noqa: F401
from tortoise.contrib.sanic import register_tortoise

from core.exception_handlers import BaseExcHandler, EXC_HDL_TUPLE
from core.listeners import BaseListener, LISTENER_TUPLE
from core.middlewares import BaseMiddleware, MIDDLEWARE_TUPLE
from core.openapi import configure_openapi_responses
from libs.security.license_modules import is_licensed_module_active
from libs.security.authorization import load_license
from services.agent.views import agent_bp
from services.ai.views import ai_bp
from services.approvement.views import approve_bp
from services.dashboard.views import dashboard_bp
from services.digital_employee.views import digital_employee_bp
from services.event.views import event_bp
from services.event_ingestion.views import event_ingestion_bp
from services.network_topology.views import topology_bp
from services.supply_chain_review.views import (
    SUPPLY_CHAIN_REVIEW_BLUEPRINTS,
    asset_management_bp,
    network_access_control_module_bp,
    supply_chain_review_action_bp,
    supply_chain_review_attachment_bp,
    supply_chain_review_cmdb_bp,
    supply_chain_review_ledger_bp,
    supply_chain_review_process_bp,
)
from services.inspection.views import inspection_bp
import services.large_screen.views  # noqa: F401
from services.command_center.views import command_bp
from services.license.views import license_bp
from services.log_view.views import logs_bp
from services.mcp.views import mcp_bp
from services.others.views import activity, all_apps, playbook_list
from services.role.views import role_bp
from services.scene.views import scene_bp
from services.secure.views import secure_bp
from services.system_config.views import system_bp
from services.tenant.views import tenant_bp
from services.timed.views import timed_bp
from services.tools.views import tools_bp
from services.user.views import user_bp
from services.variables.views import var_bp
from services.warroom.views import warroom_bp
from services.workflow.views import workflow_bp
from settings import (
    BASE_LOGGING,
    HEALTH_MAX_MISSES,
    HEALTH_MISSED_THRESHHOLD,
    HEALTH_MONITOR,
    TORTOISE,
)


def create_app() -> Sanic:
    app: Sanic = Sanic(
        "services", dumps=orjson.dumps, loads=orjson.loads, log_config=BASE_LOGGING
    )
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
    return app


def register_blueprints(app: Sanic):
    app.add_route(activity, "/activity/<activityId>")
    app.add_route(playbook_list, "/playbooks", methods=["POST"])
    app.add_route(all_apps, "/apps", methods=["POST"])
    app.blueprint(license_bp)
    app.blueprint(user_bp)
    app.blueprint(scene_bp)
    app.blueprint(dashboard_bp)
    app.blueprint(system_bp)
    app.blueprint(secure_bp)
    app.blueprint(warroom_bp)
    app.blueprint(approve_bp)
    app.blueprint(tenant_bp)
    app.blueprint(inspection_bp)

    try:
        bp_dict = {
            "event_bp": event_bp,
            "event_ingestion_bp": event_ingestion_bp,
            "role_bp": role_bp,
            "timed_bp": timed_bp,
            "tool_management_bp": tools_bp,
            "var_bp": var_bp,
            "workflow_bp": workflow_bp,
            "logs_bp": logs_bp,
            "ai_bp": ai_bp,
            "agent_bp": agent_bp,
            "mcp_bp": mcp_bp,
            "command_bp": command_bp,
            "digital_employee_bp": digital_employee_bp,
            "asset_management_bp": asset_management_bp,
            "network_access_control_bp": network_access_control_module_bp,
            "topology_bp": topology_bp,
            "supply_chain_review_bp": SUPPLY_CHAIN_REVIEW_BLUEPRINTS,
            "supply_chain_review_cmdb_bp": supply_chain_review_cmdb_bp,
            "supply_chain_review_ledger_bp": supply_chain_review_ledger_bp,
            "supply_chain_review_process_bp": supply_chain_review_process_bp,
            "supply_chain_review_action_bp": supply_chain_review_action_bp,
            "supply_chain_review_attachment_bp": supply_chain_review_attachment_bp,
        }
        license_data = load_license()
        if license_data and "moduleList" in license_data:
            registered_modules = set()
            registered_blueprint_names = set()
            for item in license_data["moduleList"]:
                module = item.get("module")
                if not module or module not in bp_dict or module in registered_modules:
                    continue
                if is_licensed_module_active(license_data, item):
                    blueprints = bp_dict[module]
                    if not isinstance(blueprints, tuple):
                        blueprints = (blueprints,)
                    for blueprint in blueprints:
                        blueprint_name = getattr(blueprint, "name", None) or id(
                            blueprint
                        )
                        if blueprint_name in registered_blueprint_names:
                            continue
                        app.blueprint(blueprint)
                        registered_blueprint_names.add(blueprint_name)
                    registered_modules.add(module)
    except Exception as ex:
        logger.info(f"{ex} | {traceback.format_exc()}")


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
                app.register_listener(getattr(listener, event), event)


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
