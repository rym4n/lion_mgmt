# coding=utf-8
import importlib
import os

import orjson
from sanic import Sanic, Blueprint
from sanic.log import logger
from tortoise.contrib.sanic import register_tortoise

from core.exception_handlers import EXC_HDL_TUPLE, BaseExcHandler
from core.listeners import LISTENER_TUPLE, BaseListener
from core.middlewares import MIDDLEWARE_TUPLE, BaseMiddleware
from libs.security.authorization import load_license, get_modules
from settings import BASE_LOGGING, TORTOISE


def create_app() -> Sanic:
    app: Sanic = Sanic(__name__, dumps=orjson.dumps, loads=orjson.loads, log_config=BASE_LOGGING)
    app.config.update_config("settings/__init__.py")
    register_listener(app)
    register_tortoise(app, config=TORTOISE, generate_schemas=False)
    register_middleware(app)
    register_exception_handler(app)
    load_license()
    register_blueprints(app)
    return app


def register_blueprints(app: Sanic) -> None:
    allowed_modules = get_modules()
    for root, _, files in os.walk('services'):
        for filename in files:
            if filename.endswith(".py") and filename != "__init__.py":
                module_path = os.path.join(root, filename)
                module_name = os.path.splitext(module_path.replace(os.sep, '.'))[0]

                try:
                    module = importlib.import_module(module_name)
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, Blueprint):
                            # 本地开发的话可以注释掉此行
                            if attr_name in allowed_modules:
                                app.blueprint(attr)
                except Exception as e:
                    logger.error(f"无法导入 {module_name}: {e}")
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
