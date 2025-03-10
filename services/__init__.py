# coding=utf-8

import importlib
import os

from sanic import Sanic, Blueprint
from sanic.log import logger
from tortoise.contrib.sanic import register_tortoise

from core.exception_handlers import EXC_HDL_TUPLE, BaseExcHandler
from core.listeners import LISTENER_TUPLE, BaseListener
from core.middlewares import MIDDLEWARE_TUPLE, BaseMiddleware
from settings import BASE_LOGGING, TORTOISE


def create_app() -> Sanic:
    """创建app"""

    # app创建
    app: Sanic = Sanic(__name__, log_config=BASE_LOGGING)

    # sanic应用配置
    app.config.update_config("settings/__init__.py")

    # 注册监听
    register_listener(app)

    # 注册tortoise
    register_tortoise(app, config=TORTOISE, generate_schemas=False)

    # 注册中间件
    register_middleware(app)

    # 注册异常处理
    register_exception_handler(app)

    # 注册蓝图
    register_blueprints(app)

    return app


def register_blueprints(app: Sanic) -> None:
    """注册蓝图"""
    for root, _, files in os.walk('services'):
        for filename in files:
            if filename.endswith(".py") and filename != "__init__.py":
                module_path = os.path.join(root, filename)
                module_name = os.path.splitext(module_path.replace(os.sep, '.'))[0]
                try:
                    module = importlib.import_module(module_name)
                    # 检查模块中所有变量，寻找 Blueprint 实例
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, Blueprint):
                            app.blueprint(attr)
                except Exception as e:
                    logger.error(f"无法导入 {module_name}: {e}")
    return None


def register_listener(app: Sanic) -> None:
    """注册监听"""
    for listener_cls in LISTENER_TUPLE:
        listener: BaseListener = listener_cls()
        for event in ('before_server_start', 'after_server_start', 'before_server_stop', 'after_server_stop'):
            if hasattr(listener, event):
                app.register_listener(getattr(listener, event), event)
    return None


def register_middleware(app: Sanic):
    """注册中间件"""
    for middle_cls in MIDDLEWARE_TUPLE:
        middle: BaseMiddleware = middle_cls()
        if hasattr(middle, 'before_request'):
            app.register_middleware(middle.before_request, attach_to='request')
        if hasattr(middle, 'before_response'):
            app.register_middleware(middle.before_response, attach_to='response')


def register_exception_handler(app: Sanic):
    """注册异常处理"""
    for exc_hdl_cls in EXC_HDL_TUPLE:
        exc_hdl: BaseExcHandler = exc_hdl_cls()
        app.error_handler.add(exc_hdl.get_exception(), exc_hdl.handle)
