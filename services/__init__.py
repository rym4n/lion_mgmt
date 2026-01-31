# coding=utf-8

import time
import traceback

import orjson
from sanic import Sanic
from sanic.log import logger
from sanic_ext import openapi
from tortoise.contrib.sanic import register_tortoise

from core.exception_handlers import BaseExcHandler, EXC_HDL_TUPLE
from core.listeners import BaseListener, LISTENER_TUPLE
from core.middlewares import BaseMiddleware, MIDDLEWARE_TUPLE
from libs.security.authorization import load_license
from services.agent.views import agent_bp
from services.ai.views import ai_bp
from services.approvement.views import approve_bp
from services.dashboard.views import dashboard_bp
from services.event.views import event_bp
from services.event_ingestion.views import event_ingestion_bp
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
from settings import BASE_LOGGING, TORTOISE


def create_app() -> Sanic:
    app: Sanic = Sanic(__name__, dumps=orjson.dumps, loads=orjson.loads, log_config=BASE_LOGGING)
    app.config.update_config("settings/__init__.py")
    register_listener(app)
    register_tortoise(app, config=TORTOISE, generate_schemas=False)
    register_middleware(app)
    register_exception_handler(app)
    register_blueprints(app)
    openapi.exclude(bp=license_bp)
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
        }

        license_data = load_license()
        if license_data and "moduleList" in license_data:
            for item in license_data["moduleList"]:
                if item["auth"] and int(item["expireTime"]) > int(time.time() * 1000):
                    if item["module"] not in bp_dict:
                        continue
                    app.blueprint(bp_dict[item["module"]])
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
