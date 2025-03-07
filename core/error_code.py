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

    ValidationError = ErrorData(400, "参数校验失败")

    WeakPassword = ErrorData(400, "密码强度过低")

    CredentialsError = ErrorData(400, "用户名或密码错误")
    AccountDisabled = ErrorData(403, "账号已禁用")
    AccountNotFound = ErrorData(404, "账号不存在")
    AccountExists = ErrorData(409, "账号已存在")
    AccountUnverified = ErrorData(403, "账号未验证")

    JWTDecodeError = ErrorData(401, "会话token无效")

    MFAError = ErrorData(403, "MFA验证失败")
    SecondFactorRequired = ErrorData(403, "需要二次验证")

    MaxChallenge = ErrorData(429, "请求次数过多")

    SessionDeleted = ErrorData(404, "会话已删除")
    SessionExpired = ErrorData(401, "会话已过期")
    SessionDeactivated = ErrorData(401, "会话已注销")

    GetAccessTokenError = ErrorData(500, "获取token失败")
    GetIdEmailError = ErrorData(500, "获取id和email失败")
    RevokeTokenError = ErrorData(500, "撤销token失败")
    RefreshTokenError = ErrorData(500, "刷新token失败")

    RoleAlreadyExists = ErrorData(409, "角色已存在")
    RoleNotFound = ErrorData(404, "角色不存在")