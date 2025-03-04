# coding=utf-8


import abc
from asyncio.base_events import BaseEventLoop

from sanic import Sanic


class BaseListener(metaclass=abc.ABCMeta):
    """监听基类
    ASGI 模式下
    main_process_start 与 main_process_stop 会被忽略
    before_server_start 与 after_server_stop 定义不明确
    因此这里只推荐使用 after_server_start，before_server_stop
    """

    @abc.abstractmethod
    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        pass

    @abc.abstractmethod
    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        pass
