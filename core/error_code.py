# coding=utf-8

from libs.error_code.enum import BaseErrorCode, ErrorData


class ErrorCode(BaseErrorCode):
    """错误码枚举类"""
    ServerError = ErrorData(500, "服务异常，请稍后重试")
    ParameterError = ErrorData(400, "参数错误")
    NotFound = ErrorData(404, "请求资源不存在")
    MethodNotAllowed = ErrorData(405, "请求方法不允许")
    Unauthorized = ErrorData(401, "未授权")
    Forbidden = ErrorData(403, "禁止访问")
