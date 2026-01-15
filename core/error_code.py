# coding=utf-8

from libs.error_code.enum import BaseErrorCode, ErrorData


class ErrorCode(BaseErrorCode):
    """错误码枚举类"""

    SERVER_ERROR = ErrorData(5000, "服务异常，请稍后重试")
    PARAM_ERROR = ErrorData(4000, "参数错误")
    INVALID_JSON = ErrorData(4001, "请求体是无效的json格式")
    NOT_FOUND = ErrorData(4002, "请求资源不存在")
    METHOD_NOT_ALLOWED = ErrorData(4003, "请求方法不允许")
    UNAUTH = ErrorData(4004, "权限不足")
    FORBIDDEN = ErrorData(4005, "禁止访问")

    WeakPassword = ErrorData(4006, "密码强度过低,必须包含大小写字母、数字、特殊字符、长度不小于8")
    PasswordLengthError = ErrorData(40061, "密码长度不符合要求")
    PasswordMissingLowercase = ErrorData(40062, "密码必须包含小写字母")
    PasswordMissingUppercase = ErrorData(40063, "密码必须包含大写字母")
    PasswordMissingDigit = ErrorData(40064, "密码必须包含数字")
    PasswordMissingSpecial = ErrorData(40065, "密码必须包含特殊字符")
    PasswordRepeatedChar = ErrorData(40066, "密码包含连续重复字符")
    PasswordContinuousChar = ErrorData(40067, "密码包含连续重复字符")
    PasswordKeyboardOrder = ErrorData(40068, "密码包含键盘顺序字符")
    PasswordExpired = ErrorData(40069, "密码已过期，请修改密码")

    CredentialsError = ErrorData(4007, "用户名或密码错误")
    AccountDisabled = ErrorData(4008, "账号已禁用")
    AccountLocked = ErrorData(40081, "账号已锁定，请稍后再试")
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
    ToolNotFoundError = ErrorData(4032, "应用未找到")
    ToolHasScriptError = ErrorData(4033, "该版本应用已在剧本中使用")
    ToolAlreadyDeletedError = ErrorData(4034, "应用删除失败")
    ResourcesModelNotFoundError = ErrorData(4035, "应用资源模板未找到")
    ResourcesNameExistsError = ErrorData(4036, "应用资源模板名称已存在")
    ConfigFileError = ErrorData(4037, "config文件内容错误")
    PythonFileError = ErrorData(4038, "Python文件内容错误")
    ResourceError = ErrorData(4039, "应用资源不一致，请新建应用或删除原应用后再次上传")
    ToolAlreadyExists = ErrorData(4040, "应用已存在")
    ActionNotFoundError = ErrorData(4041, "应用动作不存在")
    ResourcesNotFoundError = ErrorData(4042, "应用资源未找到")
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
    RequestTooManyError = ErrorData(4078, "请求数量不得超过50")
    PlaybookUsedByEvent = ErrorData(4079, "剧本已绑定事件接入，请先解绑")
    PlaybookUsedByTimer = ErrorData(4080, "剧本已绑定定时任务，请先修改定时任务")
    LogParseError = ErrorData(4081, "日志解析失败，请联系管理员")
    TypeNotSupportedError = ErrorData(4082, "当前转换类型不支持，请联系管理员")

    OauthUserCannotUseMfa = ErrorData(4083, "单点登录的用户无法启用MFA")
    FileNotFound = ErrorData(4084, "文件不存在")

    CannotDeleteAdmin = ErrorData(4085, "不允许删除内置管理员账号")
    OutboundAddressCanNotBeRepeated = ErrorData(4086, "外发地址不可重复")
    OutboundFileConfigurationFailed = ErrorData(4087, "外发文件配置失败，请检查配置")
    MultipleSyslogAddressNotSupported = ErrorData(4088, "多个syslog外发配置地址不支持用，分隔")
    InvalidPortError = ErrorData(4089, "端口范围超出1-65535之间，请检查配置")
    FlowTimeOutError = ErrorData(4090, "剧本运行超时时间不得小于5分钟或大于3天")
    InternalDeleteError = ErrorData(4091, "系统内置，禁止删除")
    NotDownloadError = ErrorData(4091, "当前应用不支持下载")
    MaintenanceModeError = ErrorData(4091, "当前用户不支持查看维护模式")

    VictoriaLogsError = ErrorData(4092, "日志查询错误")

    PortError = ErrorData(4093, "端口已被占用，请重新选择")
    WebhookExists = ErrorData(4094, "已有webhook，请勿重复创建")
    NotScriptError = ErrorData(4095, "缺少执行剧本")
    PlaybookUnauthorized = ErrorData(4096, "缺少剧本权限")

    OutOfPage = ErrorData(4097, "最多可查看前1000条数据，请使用条件搜索缩小数据范围")

    # MCP Error Codes
    MCPAgentIdRequired = ErrorData(4100, "Missing agent_id")
    MCPActionRequired = ErrorData(4101, "Missing action")
    MCPInvalidAction = ErrorData(4102, "Invalid action")
    MCPExecutionFailed = ErrorData(4103, "Execution failed")
    MCPRateLimitExceeded = ErrorData(4104, "Rate limit exceeded")

    TenantAlreadyExists = ErrorData(4105, "已存在同名工作空间")
    TenantHasMembers = ErrorData(4106, "当前工作空间存在成员，禁止删除")
    TenantNotFound = ErrorData(4107, "当前工作空间不存在")
    CannotDeleteTenant = ErrorData(4108, "默认工作空间禁止删除")
    RoleCannotDelete = ErrorData(4109, "超管角色禁止删除")

    DefaultTenantNotFound = ErrorData(4111, "默认工作空间未配置")
    DefaultRoleNotFound = ErrorData(4112, "默认角色未配置")

    ActionTimeout = ErrorData(4110, "执行超时")
