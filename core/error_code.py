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

    WeakPassword = ErrorData(4006, "密码强度过低,必须包含大小写字母、数字、特殊字符、长度不小于8")

    CredentialsError = ErrorData(4007, "用户名或密码错误")
    AccountDisabled = ErrorData(4008, "账号已禁用")
    AccountNotFound = ErrorData(4009, "账号不存在")
    AccountExists = ErrorData(4010, "账号已存在")
    DataAlreadyExists = ErrorData(4011, "数据已存在，唯一性校验不通过")
    AccountUnverified = ErrorData(4012, "账号未验证")

    MFAError = ErrorData(4013, "MFA验证失败")
    NONEEDMFA = ErrorData(4014, "无需MFA验证")
    SecondFactorRequired = ErrorData(4015, "需要二次验证")

    MaxChallenge = ErrorData(4016, "请求过于频繁，5分钟后再试")

    SessionInvalid = ErrorData(4017, "会话无效")
    SessionDeleted = ErrorData(4018, "会话已删除")
    SessionExpired = ErrorData(4019, "会话已过期")
    SessionDeactivated = ErrorData(4020, "会话已注销")

    GetAccessTokenError = ErrorData(4021, "获取token失败")
    GetUserInfoError = ErrorData(4022, "获取用户信息失败")
    RevokeTokenError = ErrorData(4023, "撤销token失败")
    RefreshTokenError = ErrorData(4024, "刷新token失败")

    RoleAlreadyExists = ErrorData(4025, "角色已存在")
    RoleNotFound = ErrorData(4026, "角色不存在")

    SystemDataNotFound = ErrorData(4027, "暂无系统配置信息")

    ZipFileFormatError = ErrorData(4028, "zip文件格式错误")
    MissingConfigFileError = ErrorData(4029, "缺失config文件")
    MissingFiledirError = ErrorData(4030, "缺失相关文件夹")
    MissingPyFileError = ErrorData(4031, "缺失py文件")
    ToolNotFoundError = ErrorData(4032, "工具未找到")
    ToolHasScriptError = ErrorData(4033, "工具已被脚本使用")
    ToolAlreadyDeletedError = ErrorData(4034, "工具已删除")
    ResourcesModelNotFoundError = ErrorData(4035, "工具资源模板未找到")
    ResourcesNameExistsError = ErrorData(4036, "工具资源模板名称已存在")
    ConfigFileError = ErrorData(4037, "config文件内容错误")
    PythonFileError = ErrorData(4038, "Python文件内容错误")
    ResourceError = ErrorData(4039, "工具资源不一致，请新建工具或删除原工具后再次上传")
    ToolAlreadyExists = ErrorData(4040, "工具已存在")
    ActionNotFoundError = ErrorData(4041, "工具动作不存在")
    ResourcesNotFoundError = ErrorData(4042, "工具资源未找到")
    MissingLogoError = ErrorData(4043, "logo文件不存在")
    InternalToolError = ErrorData(4044, "内置应用不支持下载")

    IpForbidden = ErrorData(4045, "IP禁止访问")

    ConditionalExistsError = ErrorData(4046, "判断条件已存在")
    InvalidUsername = ErrorData(4047, "无效的用户名")
    PlaybookNotFound = ErrorData(4048, "剧本不存在")
    RoleAlreadyUsed = ErrorData(4049, "角色已分配，请先取消分配")
    PlaybookNameExists = ErrorData(4050, "剧本名称重复，请重新命名")
    ScriptExistsError = ErrorData(4051, "绑定剧本已存在其他判断条件，请删除后再次进行绑定")

    RoomNameExists = ErrorData(4052, "作战室名称重复，请重新命名")
    RoomNotExists = ErrorData(4053, "房间不存在")
    RoomDeleteNotAllowed = ErrorData(4054, "无权删除该房间，仅房间创建者或管理员可删除")
    ParamValueError = ErrorData(4055, "缺少消息查询参数")
    PlaybookCreateLimit = ErrorData(4052, "剧本创建上限，请删除已有剧本或升级软件许可")
    LicenseExpired = ErrorData(4053, "软件许可已过期")
    InvalidLicenseError = ErrorData(4054, "无效的软件许可")
    SceneExistsError = ErrorData(4055, "场景已存在")
    TimedNameExists = ErrorData(4056, "定时任务名称重复，请重新命名")
    TimedNotFound = ErrorData(4057, "定时任务不存在")
    EventNameExists = ErrorData(4058, "事件名称重复，请重新命名")

    NoExecutionRecord = ErrorData(4059, "无执行记录")
    NoEventAccess = ErrorData(4060, "当前事件接入不存在")
    ApiUserNotFound = ErrorData(4061, "API用户不存在")

    CheckStatusNotFound = ErrorData(4062, "系统服务健康检查结果不存在")
    InvalidIP = ErrorData(4063, "IP地址输入错误")
    TagExistError = ErrorData(4064, "标签已存在")
    TagNotFoundError = ErrorData(4065, "标签不存在")
    SceneDeleteNotAllowed = ErrorData(4066, "当前场景已启用，只运行删除禁用场景")
    ConditionExistsError = ErrorData(4067, "当前判断条件已存在")
    ActionInputError = ErrorData(4068, "缺少执行动作必须参数")
    PlaybookInputError = ErrorData(4069, "缺少执行剧本必须参数")
    CronExpressionError = ErrorData(4070, "cron表达式错误")
    ConfigFileGenerateError = ErrorData(4071, "配置文件生成失败，请联系管理进行处理")

    InvalidState = ErrorData(4072, "非法的state参数")
    SystemUserCannotBeDeleted = ErrorData(4073, "系统用户禁止删除")

    ApprovementStatusError = ErrorData(4074, "审核状态已经不是待处理了")
    CannotDeleteSelf = ErrorData(4075, "不允许删除自己")
    CannotDisableSelf = ErrorData(4076, "不允许禁用自己")
    CannotEditSensitiveAttribute = ErrorData(4077, "敏感属性不允许修改")
