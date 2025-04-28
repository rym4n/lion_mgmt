# coding=utf-8
import base64
import hashlib
import importlib
import os
import time

import orjson
from sanic import Sanic, Blueprint
from sanic.log import logger
from tortoise.contrib.sanic import register_tortoise

import settings
from core.exception_handlers import EXC_HDL_TUPLE, BaseExcHandler
from core.listeners import LISTENER_TUPLE, BaseListener
from core.middlewares import MIDDLEWARE_TUPLE, BaseMiddleware
from services.license.views import verify_license
from settings import BASE_LOGGING, TORTOISE, LICENSE_DIR


def create_app() -> Sanic:
    """创建app"""

    # app创建
    app: Sanic = Sanic(__name__, dumps=orjson.dumps, loads=orjson.loads, log_config=BASE_LOGGING)

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

    license_data()

    # 注册蓝图
    register_blueprints(app)

    return app


def license_data():
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            with open(full_path, "r") as f:
                data = f.read()
                if filename == hashlib.md5(f"{data}".encode()).hexdigest():
                    json_str = base64.b64decode(data.encode("utf-8")).decode("utf-8")
                    license_data = orjson.loads(json_str)
                    is_valid = verify_license(license_data)
                    if is_valid:
                        if license_data.get("expireTime"):
                            if int(time.time() * 1000) < license_data.get("expireTime"):
                                settings.LICENSE_DATA = license_data


def should_register_modules():
    """根据产品许可返回需要注册模块"""
    module_flags = {}
    if settings.LICENSE_DATA:
        for item in settings.LICENSE_DATA["menuInfo"]["moduleList"]:
            module = item["module"]
            auth = item["auth"]

            if module not in module_flags:
                module_flags[module] = False
            if auth and int(item["expireTime"]) > int(time.time() * 1000):
                module_flags[module] = True

    return [mod for mod, should_register in module_flags.items() if should_register]


def register_blueprints(app: Sanic) -> None:
    """注册蓝图"""
    allowed_modules = should_register_modules()
    logger.info(f"allowed modules: {allowed_modules}")
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
                            if attr_name not in allowed_modules:
                                logger.info(f"module {module_name} not allowed, skip register")
                                continue
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


# noinspection PyTypeChecker
def register_middleware(app: Sanic):
    """注册中间件"""
    for middle_cls in MIDDLEWARE_TUPLE:
        middle: BaseMiddleware = middle_cls()
        if hasattr(middle, 'before_request'):
            app.register_middleware(middle.before_request, attach_to='request')
        if hasattr(middle, 'before_response'):
            app.register_middleware(middle.before_response, attach_to='response')


# noinspection PyTypeChecker
def register_exception_handler(app: Sanic):
    """注册异常处理"""
    for exc_hdl_cls in EXC_HDL_TUPLE:
        exc_hdl: BaseExcHandler = exc_hdl_cls()
        app.error_handler.add(exc_hdl.get_exception(), exc_hdl.handle)
