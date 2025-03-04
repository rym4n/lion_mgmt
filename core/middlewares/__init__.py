# coding=utf-8


from .base import BaseMiddleware
from .cache import CacheMiddleware
from .timer import TimerMiddleware

MIDDLEWARE_TUPLE: tuple[type[BaseMiddleware], ...] = (
    TimerMiddleware,
    CacheMiddleware,
)
