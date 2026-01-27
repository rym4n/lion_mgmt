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

    WEAK_PASSWORD = ErrorData(4006, "密码强度过低,必须包含大小写字母、数字、特殊字符、长度不小于8")
    PASSWORD_LENGTH_ERROR = ErrorData(4007, "密码长度不符合要求")
    PASSWORD_MISSING_LOWERCASE = ErrorData(4008, "密码必须包含小写字母")
    PASSWORD_MISSING_UPPERCASE = ErrorData(4009, "密码必须包含大写字母")
    PASSWORD_MISSING_DIGIT = ErrorData(4010, "密码必须包含数字")
    PASSWORD_MISSING_SPECIAL = ErrorData(4011, "密码必须包含特殊字符")
    PASSWORD_REPEATED_CHAR = ErrorData(4012, "密码包含连续重复字符")
    PASSWORD_CONTINUOUS_CHAR = ErrorData(4013, "密码包含连续重复字符")
    PASSWORD_KEYBOARD_ORDER = ErrorData(4014, "密码包含键盘顺序字符")
    PASSWORD_EXPIRED = ErrorData(4015, "密码已过期，请修改密码")

    CREDENTIALS_ERROR = ErrorData(4016, "用户名或密码错误")
    ACCOUNT_DISABLED = ErrorData(4017, "账号已禁用")
    ACCOUNT_LOCKED = ErrorData(4018, "账号已锁定，请稍后再试")
    ACCOUNT_NOT_FOUND = ErrorData(4019, "账号不存在")
    ACCOUNT_EXISTS = ErrorData(4020, "账号已存在")
    DATA_ALREADY_EXISTS = ErrorData(4021, "数据已存在，唯一性校验不通过")
    ACCOUNT_UNVERIFIED = ErrorData(4022, "账号未验证")

    MFA_ERROR = ErrorData(4023, "MFA验证失败")
    NO_NEED_MFA = ErrorData(4024, "无需MFA验证")
    SECOND_FACTOR_REQUIRED = ErrorData(4025, "需要二次验证")

    MAX_CHALLENGE = ErrorData(4026, "请求过于频繁，5分钟后再试")

    SESSION_INVALID = ErrorData(4027, "会话无效")
    SESSION_DELETED = ErrorData(4028, "会话已删除")
    SESSION_EXPIRED = ErrorData(4029, "会话已过期")
    SESSION_DEACTIVATED = ErrorData(4030, "会话已注销")

    GET_ACCESS_TOKEN_ERROR = ErrorData(4031, "获取token失败")
    GET_USER_INFO_ERROR = ErrorData(4032, "获取用户信息失败")
    REVOKE_TOKEN_ERROR = ErrorData(4033, "撤销token失败")
    REFRESH_TOKEN_ERROR = ErrorData(4034, "刷新token失败")

    ROLE_ALREADY_EXISTS = ErrorData(4035, "角色已存在")
    ROLE_NOT_FOUND = ErrorData(4036, "角色不存在")

    SYSTEM_DATA_NOT_FOUND = ErrorData(4037, "暂无系统配置信息")

    ZIP_FILE_FORMAT_ERROR = ErrorData(4038, "zip文件格式错误")
    MISSING_CONFIG_FILE_ERROR = ErrorData(4039, "缺失config文件")
    MISSING_FILEDIR_ERROR = ErrorData(4040, "缺失相关文件夹")
    MISSING_PY_FILE_ERROR = ErrorData(4041, "缺失py文件")
    TOOL_NOT_FOUND_ERROR = ErrorData(4042, "应用未找到")
    TOOL_HAS_SCRIPT_ERROR = ErrorData(4043, "该版本应用已在剧本中使用")
    TOOL_ALREADY_DELETED_ERROR = ErrorData(4044, "应用删除失败")
    RESOURCES_MODEL_NOT_FOUND_ERROR = ErrorData(4045, "应用资源模板未找到")
    RESOURCES_NAME_EXISTS_ERROR = ErrorData(4046, "应用资源模板名称已存在")
    CONFIG_FILE_ERROR = ErrorData(4047, "config文件内容错误")
    PYTHON_FILE_ERROR = ErrorData(4048, "Python文件内容错误")
    RESOURCE_ERROR = ErrorData(4049, "应用资源不一致，请新建应用或删除原应用后再次上传")
    TOOL_ALREADY_EXISTS = ErrorData(4050, "应用已存在")
    ACTION_NOT_FOUND_ERROR = ErrorData(4051, "应用动作不存在")
    RESOURCES_NOT_FOUND_ERROR = ErrorData(4052, "应用资源未找到")
    MISSING_LOGO_ERROR = ErrorData(4053, "logo文件不存在")
    INTERNAL_TOOL_ERROR = ErrorData(4054, "内置应用不支持下载")

    IP_FORBIDDEN = ErrorData(4055, "IP禁止访问")

    CONDITIONAL_EXISTS_ERROR = ErrorData(4056, "判断条件已存在")
    INVALID_USERNAME = ErrorData(4057, "无效的用户名")
    PLAYBOOK_NOT_FOUND = ErrorData(4058, "剧本不存在")
    ROLE_ALREADY_USED = ErrorData(4059, "角色已分配，请先取消分配")
    PLAYBOOK_NAME_EXISTS = ErrorData(4060, "剧本名称重复，请重新命名")
    SCRIPT_EXISTS_ERROR = ErrorData(4061, "绑定剧本已存在其他判断条件，请删除后再次进行绑定")

    ROOM_NAME_EXISTS = ErrorData(4062, "作战室名称重复，请重新命名")
    ROOM_NOT_EXISTS = ErrorData(4063, "房间不存在")
    ROOM_DELETE_NOT_ALLOWED = ErrorData(4064, "无权删除该房间，仅房间创建者或管理员可删除")
    PARAM_VALUE_ERROR = ErrorData(4065, "缺少消息查询参数")
    PLAYBOOK_CREATE_LIMIT = ErrorData(4066, "剧本创建上限，请删除已有剧本或升级软件许可")
    LICENSE_EXPIRED = ErrorData(4067, "软件许可已过期")
    INVALID_LICENSE_ERROR = ErrorData(4068, "无效的软件许可")
    SCENE_EXISTS_ERROR = ErrorData(4069, "场景已存在")
    TIMED_NAME_EXISTS = ErrorData(4070, "定时任务名称重复，请重新命名")
    TIMED_NOT_FOUND = ErrorData(4071, "定时任务不存在")
    EVENT_NAME_EXISTS = ErrorData(4072, "事件名称重复，请重新命名")

    NO_EXECUTION_RECORD = ErrorData(4073, "无执行记录")
    NO_EVENT_ACCESS = ErrorData(4074, "当前事件接入不存在")
    API_USER_NOT_FOUND = ErrorData(4075, "API用户不存在")

    CHECK_STATUS_NOT_FOUND = ErrorData(4076, "系统服务健康检查结果不存在")
    INVALID_IP = ErrorData(4077, "IP地址输入错误")
    TAG_EXIST_ERROR = ErrorData(4078, "标签已存在")
    TAG_NOT_FOUND_ERROR = ErrorData(4079, "标签不存在")
    SCENE_DELETE_NOT_ALLOWED = ErrorData(4080, "当前场景已启用，只运行删除禁用场景")
    CONDITION_EXISTS_ERROR = ErrorData(4081, "当前判断条件已存在")
    ACTION_INPUT_ERROR = ErrorData(4082, "缺少执行动作必须参数")
    PLAYBOOK_INPUT_ERROR = ErrorData(4083, "缺少执行剧本必须参数")
    CRON_EXPRESSION_ERROR = ErrorData(4084, "cron表达式错误")
    CONFIG_FILE_GENERATE_ERROR = ErrorData(4085, "配置文件生成失败，请联系管理进行处理")

    INVALID_STATE = ErrorData(4086, "非法的state参数")
    SYSTEM_USER_CANNOT_BE_DELETED = ErrorData(4087, "系统用户禁止删除")

    APPROVEMENT_STATUS_ERROR = ErrorData(4088, "审核状态已经不是待处理了")
    CANNOT_DELETE_SELF = ErrorData(4089, "不允许删除自己")
    CANNOT_DISABLE_SELF = ErrorData(4090, "不允许禁用自己")
    CANNOT_EDIT_SENSITIVE_ATTRIBUTE = ErrorData(4091, "敏感属性不允许修改")
    REQUEST_TOO_MANY_ERROR = ErrorData(4092, "请求数量不得超过50")
    PLAYBOOK_USED_BY_EVENT = ErrorData(4093, "剧本已绑定事件接入，请先解绑")
    PLAYBOOK_USED_BY_TIMER = ErrorData(4094, "剧本已绑定定时任务，请先修改定时任务")
    LOG_PARSE_ERROR = ErrorData(4095, "日志解析失败，请联系管理员")
    TYPE_NOT_SUPPORTED_ERROR = ErrorData(4096, "当前转换类型不支持，请联系管理员")

    OAUTH_USER_CANNOT_USE_MFA = ErrorData(4097, "单点登录的用户无法启用MFA")
    FILE_NOT_FOUND = ErrorData(4098, "文件不存在")

    CANNOT_DELETE_ADMIN = ErrorData(4099, "不允许删除内置管理员账号")
    OUTBOUND_ADDRESS_CAN_NOT_BE_REPEATED = ErrorData(4100, "外发地址不可重复")
    OUTBOUND_FILE_CONFIGURATION_FAILED = ErrorData(4101, "外发文件配置失败，请检查配置")
    MULTIPLE_SYSLOG_ADDRESS_NOT_SUPPORTED = ErrorData(4102, "多个syslog外发配置地址不支持用，分隔")
    INVALID_PORT_ERROR = ErrorData(4103, "端口范围超出1-65535之间，请检查配置")
    FLOW_TIME_OUT_ERROR = ErrorData(4104, "剧本运行超时时间不得小于5分钟或大于3天")
    INTERNAL_DELETE_ERROR = ErrorData(4105, "系统内置，禁止删除")
    NOT_DOWNLOAD_ERROR = ErrorData(4106, "当前应用不支持下载")
    MAINTENANCE_MODE_ERROR = ErrorData(4107, "当前用户不支持查看维护模式")

    VICTORIA_LOGS_ERROR = ErrorData(4108, "日志查询错误")

    PORT_ERROR = ErrorData(4109, "端口已被占用，请重新选择")
    WEBHOOK_EXISTS = ErrorData(4110, "已有webhook，请勿重复创建")
    NOT_SCRIPT_ERROR = ErrorData(4111, "缺少执行剧本")
    PLAYBOOK_UNAUTHORIZED = ErrorData(4112, "缺少剧本权限")

    OUT_OF_PAGE = ErrorData(4113, "最多可查看前1000条数据，请使用条件搜索缩小数据范围")

    # MCP Error Codes
    MCP_AGENT_ID_REQUIRED = ErrorData(4114, "Missing agent_id")
    MCP_ACTION_REQUIRED = ErrorData(4115, "Missing action")
    MCP_INVALID_ACTION = ErrorData(4116, "Invalid action")
    MCP_EXECUTION_FAILED = ErrorData(4117, "Execution failed")
    MCP_RATE_LIMIT_EXCEEDED = ErrorData(4118, "Rate limit exceeded")

    TENANT_ALREADY_EXISTS = ErrorData(4119, "已存在同名工作空间")
    TENANT_HAS_MEMBERS = ErrorData(4120, "工作空间存在成员，禁止删除")
    TENANT_NOT_FOUND = ErrorData(4121, "工作空间不存在")
    CANNOT_DELETE_TENANT = ErrorData(4122, "默认工作空间禁止删除")
    ROLE_CANNOT_DELETE = ErrorData(4123, "超管角色禁止删除")

    ACTION_TIMEOUT = ErrorData(4124, "执行超时")
    DELETE_SONG_USERS_FAILED = ErrorData(4125, "部分用户由于只存在一个工作空间删除失败")
    NOT_EXIST_IN_TENANT = ErrorData(4126, "当前用户不存在工作空间下")
    DEFAULT_TENANT_NOT_FOUND = ErrorData(4127, "默认工作空间未配置")
    DEFAULT_ROLE_NOT_FOUND = ErrorData(4128, "默认角色未配置")
    IMPORT_PART_EVENT_INGESTION_FAIL = ErrorData(4129, "部分事件接入导入失败，请检查名称或端口是否重复")
    IMPORT_FLOW_ERROR = ErrorData(4130, "部分剧本导入失败，请检查是否存在相同剧本")
    INVITE_USER_ERROR = ErrorData(4131, "私聊作战室禁止邀请用户")
    EXIT_ROOM_ERROR = ErrorData(4132, "禁止退出私聊作战室")