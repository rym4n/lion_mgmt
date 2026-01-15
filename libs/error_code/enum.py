# coding=utf-8

from enum import EnumMeta, Enum, unique
from types import DynamicClassAttribute
from typing import NamedTuple

__all__ = ("ErrorData", "BaseErrorCode")


class ErrorData(NamedTuple):
    """错误码数据"""

    # 错误码
    code: int
    # 错误信息
    message: str = "服务器异常"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ErrorData):
            raise NotImplementedError
        return self.code == other.code


class _ErrorCodeMeta(EnumMeta):
    """错误码枚举元类"""

    # noinspection PyTypeChecker
    def __new__(mcs, *args, **kwargs):
        enum_class = super(_ErrorCodeMeta, mcs).__new__(mcs, *args, **kwargs)
        # 唯一
        return unique(enum_class)


class BaseErrorCode(Enum, metaclass=_ErrorCodeMeta):
    """错误码基类"""

    @DynamicClassAttribute
    def code(self):
        """错误码"""
        return self.value.code

    @DynamicClassAttribute
    def message(self):
        """错误信息"""
        return self.value.message

    @DynamicClassAttribute
    def error(self):
        """error码"""
        return self.name
