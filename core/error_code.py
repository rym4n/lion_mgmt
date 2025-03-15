# coding=utf-8

from libs.error_code.enum import BaseErrorCode, ErrorData


class ErrorCode(BaseErrorCode):
    """错误码枚举类"""
    ServerError = ErrorData(5000, "服务异常，请稍后重试")
    ParameterError = ErrorData(4000, "参数错误")
    InvalidJsonError = ErrorData(4001, "请求体是无效的json格式")
    NotFound = ErrorData(4002, "请求资源不存在")
    MethodNotAllowed = ErrorData(4003, "请求方法不允许")
    Unauthorized = ErrorData(4004, "权限不足")
    Forbidden = ErrorData(4005, "禁止访问")

    WeakPassword = ErrorData(4006, "密码强度过低")

    CredentialsError = ErrorData(4007, "用户名或密码错误")
    AccountDisabled = ErrorData(4008, "账号已禁用")
    AccountNotFound = ErrorData(4009, "账号不存在")
    AccountExists = ErrorData(4010, "账号已存在")
    DataAlreadyExists = ErrorData(4011, "数据已存在，唯一性校验不通过")
    AccountUnverified = ErrorData(4012, "账号未验证")

    MFAError = ErrorData(4013, "MFA验证失败")
    NONEEDMFA = ErrorData(4014, "无需MFA验证")
    SecondFactorRequired = ErrorData(4015, "需要二次验证")

    MaxChallenge = ErrorData(4016, "请求次数过多")

    SessionInvalid = ErrorData(4017, "会话无效")
    SessionDeleted = ErrorData(4018, "会话已删除")
    SessionExpired = ErrorData(4019, "会话已过期")
    SessionDeactivated = ErrorData(4020, "会话已注销")

    GetAccessTokenError = ErrorData(4021, "获取token失败")
    GetIdEmailError = ErrorData(4022, "获取id和email失败")
    RevokeTokenError = ErrorData(4023, "撤销token失败")
    RefreshTokenError = ErrorData(4024, "刷新token失败")

    RoleAlreadyExists = ErrorData(4025, "角色已存在")
    RoleNotFound = ErrorData(4026, "角色不存在")

    SystemDataNotFound = ErrorData(4027, "暂无系统配置信息")
