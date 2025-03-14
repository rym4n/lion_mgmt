# coding=utf-8


from .base import BaseMiddleware
from .cache import CacheMiddleware
from .security import SecurityMiddleware
from .timer import TimerMiddleware

MIDDLEWARE_TUPLE: tuple[type[BaseMiddleware], ...] = (
    CacheMiddleware,
    TimerMiddleware,
    SecurityMiddleware
)
