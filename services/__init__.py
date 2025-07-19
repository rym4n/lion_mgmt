# coding=utf-8
import time
import traceback

import orjson
from sanic import Sanic
from sanic.log import logger
from tortoise.contrib.sanic import register_tortoise

from core.exception_handlers import EXC_HDL_TUPLE, BaseExcHandler
from core.listeners import LISTENER_TUPLE, BaseListener
from core.middlewares import MIDDLEWARE_TUPLE, BaseMiddleware
from libs.security.authorization import load_license
from services.approvement.views import approve_bp
from services.dashboard.views import dashboard_bp
from services.event.views import event_bp
from services.event_ingestion.views import event_ingestion_bp
from services.license.views import license_bp
from services.log_view.views import logs_bp
from services.role.views import role_bp
from services.scene.views import scene_bp
from services.system_config.views import system_bp
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
    return app


def register_blueprints(app: Sanic) -> None:
    app.blueprint(license_bp)
    app.blueprint(user_bp)
    app.blueprint(scene_bp)
    app.blueprint(dashboard_bp)
    app.blueprint(system_bp)
    app.blueprint(warroom_bp)

    try:
        bp_dict = {
            "approve_bp": approve_bp,
            "event_bp": event_bp,
            "event_ingestion_bp": event_ingestion_bp,
            "role_bp": role_bp,
            "timed_bp": timed_bp,
            "tool_management_bp": tools_bp,
            "var_bp": var_bp,
            "workflow_bp": workflow_bp,
            "logs_bp": logs_bp
        }
        license_data = load_license()
        if license_data and 'moduleList' in license_data:
            for item in license_data["moduleList"]:
                if item["auth"] and int(item["expireTime"]) > int(time.time() * 1000):
                    if item["module"] not in bp_dict:
                        continue
                    app.blueprint(bp_dict[item["module"]])
    except Exception as ex:
        logger.info(f"{ex} | {traceback.format_exc()}")

    return None


def register_listener(app: Sanic) -> None:
    for listener_cls in LISTENER_TUPLE:
        listener: BaseListener = listener_cls()
        for event in ('before_server_start', 'after_server_start', 'before_server_stop', 'after_server_stop'):
            if hasattr(listener, event):
                app.register_listener(getattr(listener, event), event)
    return None


def register_middleware(app: Sanic):
    for middle_cls in MIDDLEWARE_TUPLE:
        middle: BaseMiddleware = middle_cls()
        if hasattr(middle, 'before_request'):
            app.register_middleware(middle.before_request, attach_to='request')
        if hasattr(middle, 'before_response'):
            app.register_middleware(middle.before_response, attach_to='response')


def register_exception_handler(app: Sanic):
    for exc_hdl_cls in EXC_HDL_TUPLE:
        exc_hdl: BaseExcHandler = exc_hdl_cls()
        app.error_handler.add(exc_hdl.get_exception(), exc_hdl.handle)
