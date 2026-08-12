# coding=utf-8

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from services.app_factory import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_app"]
