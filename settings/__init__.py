# coding=utf-8

import os

import pytz

from libs.sanic_worker import (
    resolve_health_max_misses,
    resolve_health_missed_threshold,
    resolve_worker_ack_timeout,
)

# 项目根路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 日志文件路径
LOG_PATH = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_PATH, exist_ok=True)

# vector配置目录
VECTOR_CFG = os.path.join(BASE_DIR, "data/ship")
os.makedirs(VECTOR_CFG, exist_ok=True)

# 子进程配置文件输出目录
SUPERVISOR_PATH = os.path.join(BASE_DIR, "data/action")
os.makedirs(SUPERVISOR_PATH, exist_ok=True)

# 存储许可文件的目录
LICENSE_DIR = os.path.join(BASE_DIR, "data/license")
os.makedirs(LICENSE_DIR, exist_ok=True)

# 报告模板目录（内置模板 .docx 文件存放位置）
REPORT_TEMPLATE_BASE = os.path.join(BASE_DIR, "resources/report-templates")
os.makedirs(REPORT_TEMPLATE_BASE, exist_ok=True)

# 用户报告文件存储目录
REPORT_USER_BASE = os.path.join(BASE_DIR, "data/user-reports")
os.makedirs(REPORT_USER_BASE, exist_ok=True)

# 数字伙伴资源目录
DIGITAL_EMPLOYEE_RESOURCES = os.environ.get(
    "RY_DIGITAL_EMPLOYEE_RESOURCES"
) or os.path.join(BASE_DIR, "data/digital-employee")
os.makedirs(DIGITAL_EMPLOYEE_RESOURCES, exist_ok=True)

if os.environ.get("RY_ENV") == "DEV":
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(BASE_DIR, "settings/.env"))
    except Exception:
        pass

TZ = pytz.timezone(os.environ.get("TZ", "Asia/Shanghai"))


def _strict_env_bool(name: str, default: bool) -> bool:
    """Read one explicit true/false environment switch."""
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _strict_env_choice(name: str, default: str, allowed: frozenset[str]) -> str:
    """读取一个只允许固定取值的环境开关；未知值 fail-closed。

    与 _strict_env_bool 同源：配错了就让进程起不来，而不是静默回落默认值——
    这类开关没有运行时提示，静默回落会把一次拼写错误变成长期的行为错配。
    """
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return normalized


# Topology v2 defaults on; explicit environment gates can disable each surface.
TOPOLOGY_V2_INGRESS_ENABLED = _strict_env_bool("TOPOLOGY_V2_INGRESS_ENABLED", True)
TOPOLOGY_V2_ANALYSIS_ENABLED = _strict_env_bool("TOPOLOGY_V2_ANALYSIS_ENABLED", True)
TOPOLOGY_V2_PUBLISH_ENABLED = _strict_env_bool("TOPOLOGY_V2_PUBLISH_ENABLED", True)
TOPOLOGY_V2_VIEW_ENABLED = _strict_env_bool("TOPOLOGY_V2_VIEW_ENABLED", True)
TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED = _strict_env_bool(
    "TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED", True
)
TOPOLOGY_LEGACY_SYNC_ENABLED = _strict_env_bool("TOPOLOGY_LEGACY_SYNC_ENABLED", True)

# 透传真实客户端IP的请求头
REAL_IP_HEADER = os.environ.get("RY_REAL_IP_HEADER") or "x-real-ip"

# 应用资源配置加密密钥
RY_RES_KEY = os.environ.get("RY_RES_KEY")

# supervisord地址
RY_JOB_SERVER = os.environ.get("RY_JOB_SERVER")

# kafka配置
KAFKA_SERVERS = os.environ.get("RY_KAFKA_SERVERS")
KAFKA_CONFIG = {
    "bootstrap_servers": KAFKA_SERVERS,
    "security_protocol": os.environ.get("RY_KAFKA_PROTOCOL") or "PLAINTEXT",
    "sasl_mechanism": os.environ.get("RY_KAFKA_MECHANISM"),
    "sasl_plain_username": os.environ.get("RY_KAFKA_USER"),
    "sasl_plain_password": os.environ.get("RY_KAFKA_PASS"),
}

# 定时任务发送主题
TASK_TOPIC = os.environ.get("RY_TASK_TOPIC")
RUNLOG_TOPIC = os.environ.get("RY_RUNLOG_TOPIC")
WR_TOPIC = os.environ.get("RY_WR_TOPIC")
AUDIT_TOPIC = os.environ.get("RY_AUDIT_TOPIC")

# VictoriaLogs配置
VL_HOST = os.environ.get("VLOGS_HOST", "http://lion-vl:9428")

# 对象存储设置
S3_HOST = os.environ.get("RY_FS_HOST")
FS_AK = os.environ.get("RUSTFS_ACCESS_KEY")
FS_SK = os.environ.get("RUSTFS_SECRET_KEY")

# 系统文件专用存储桶，例如license、自定义图片配置等
RY_SYS_BUCKET = os.environ.get("RY_FS_SYS") or "system"

# 用户文件专用存储桶
RY_USER_BUCKET = os.environ.get("RY_FS_USER") or "user"

# lion_agent 配置（知识库执行层）
LION_MGMT_INTERNAL_BASE_URL = os.environ.get(
    "RY_LION_MGMT_INTERNAL_BASE_URL", "http://lion-mgmt:8000"
)

OAS_UI_DEFAULT = "redoc"
OAS_PATH_TO_REDOC_HTML = os.path.join(BASE_DIR, "settings", "apidoc.html")
OAS_UI_REDOC = True
OAS_UI_SWAGGER = False

# 安全相关
SECRET = os.environ.get("RY_SECRET_KEY", "5tdPY^zTX6I$akvNtLXey3y#gFEG@^46f!iLUdSy$Y")

SESSION_SAMESITE = "None"  # 可选值: "Strict", "Lax", "None" (None 需要 HTTPS)
SESSION_SECURE = True
SESSION_HTTPONLY = True
SESSION_DOMAIN = None
SESSION_ENCODING_ALGORITHM = "HS256"
MAX_CHALLENGE_ATTEMPTS = 5
AUTHENTICATION_SESSION_EXPIRATION = 60 * 30
AUTHENTICATION_REFRESH_EXPIRATION = 60 * 60 * 8

SECURE_CONFIG_DEFAULT = {
    "password_length_min": 8,
    "password_length_max": 32,
    "password_include_lowercase": True,
    "password_include_uppercase": True,
    "password_include_digit": True,
    "password_include_special": True,
    "password_exclude_continuous_length": 0,
    "password_exclude_keyboard_order": False,
    "password_exclude_letter_order": False,
    "password_exclude_digit_order": False,
    "password_exclude_special_order": False,
    "password_exclude_repeat": False,
    "password_validity_period": "forever",
    "password_max_retries": 5,
    "account_lock_duration": 600,
    "account_auto_freeze_days": 180,
    "account_auto_freeze_enabled": False,
    "force_change_password_first_login": False,
    "mfa_enabled": False,
    "session_hijacking_protection": False,
}

# 健康检查相关
HEALTH = True
HEALTH_ENDPOINT = True
HEALTH_MONITOR = False
HEALTH_URL_PREFIX = "/health"
HEALTH_MAX_MISSES = resolve_health_max_misses()
HEALTH_MISSED_THRESHHOLD = resolve_health_missed_threshold(
    timeout_seconds=resolve_worker_ack_timeout(),
    max_misses=HEALTH_MAX_MISSES,
)

# 跨域相关
ENABLE_CORS: bool = bool(os.environ.get("RY_CORS", True))
CORS_SUPPORTS_CREDENTIALS: bool = bool(os.environ.get("RY_CORS_CREDENTIAL", True))

# Websocket
WEBSOCKET_MAX_SIZE = 2**20  # 消息最大1MB
WEBSOCKET_PING_INTERVAL = 45
RESPONSE_TIMEOUT = 600
REQUEST_TIMEOUT = 600
REQUEST_MAX_SIZE = 100 * 1024 * 1024  # 100MB
KEEP_ALIVE_TIMEOUT = 45
WEBSOCKET_PING_TIMEOUT = 60

# 日志配置
BASE_LOGGING = {
    "version": 1,
    "loggers": {
        "": {
            "level": "INFO",
            "handlers": ["console", "info_file", "error_file"],
            "propagate": False,
        },
        "sanic": {
            "level": "INFO",
            "handlers": ["console", "info_file", "error_file"],
            "propagate": False,
        },
    },
    "formatters": {
        "default": {
            "format": "%(asctime)s [%(filename)s:%(lineno)d:%(funcName)s] [%(levelname)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "default",
        },
        "info_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_PATH, "info.log"),
            "maxBytes": 100 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf8",
            "level": "INFO",
            "formatter": "default",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_PATH, "error.log"),
            "maxBytes": 100 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf8",
            "level": "ERROR",
            "formatter": "default",
        },
    },
}

# tortoise
TORTOISE = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.asyncpg",
            "credentials": {
                "host": os.environ.get("RY_DB_HOST", "lion-db"),
                "port": int(os.environ.get("RY_DB_PORT", 5432)),
                "user": os.environ.get("RY_DB_USER"),
                "password": os.environ.get("RY_DB_PASS"),
                "database": os.environ.get("RY_DB_NAME"),
                "maxsize": 50,
                "minsize": 5,
                # 关闭池中非活动连接的秒数。通过 0 则禁用此机制。
                "max_inactive_connection_lifetime": 60,
            },
        }
    },
    "apps": {
        "models": {
            "models": ["aerich.models", "models"],
            "default_connection": "default",
        }
    },
    "use_tz": False,
    # set session time_zone
    "timezone": "Asia/Shanghai",
}

CACHE = {
    "default": {
        "cache": "aiocache.SimpleMemoryCache",
        "serializer": {"class": "aiocache.serializers.PickleSerializer"},
    },
    "redis_mgmt": {
        "cache": "aiocache.RedisCache",
        "serializer": {"class": "aiocache.serializers.PickleSerializer"},
        "namespace": "lion_mgmt",
        "timeout": 2,
        "endpoint": os.environ.get("RY_REDIS_HOST"),
        "port": os.environ.get("RY_REDIS_PORT"),
        "db": os.environ.get("RY_REDIS_DB"),
        "password": os.environ.get("RY_REDIS_PASS", ""),
        "pool_max_size": 100,
    },
    "redis_chat": {
        "cache": "aiocache.RedisCache",
        "serializer": {
            "class": "aiocache.serializers.MsgPackSerializer",
            "encoding": None,
        },
        "timeout": 2,
        "endpoint": os.environ.get("RY_REDIS_HOST"),
        "port": os.environ.get("RY_REDIS_PORT"),
        "db": os.environ.get("RY_CHAT_DB"),
        "password": os.environ.get("RY_REDIS_PASS", ""),
        "pool_max_size": 100,
    },
    "redis_action": {
        "cache": "aiocache.RedisCache",
        "serializer": {
            "class": "aiocache.serializers.MsgPackSerializer",
            "encoding": None,
        },
        "namespace": "lion_mgmt",
        "timeout": 2,
        "endpoint": os.environ.get("RY_REDIS_HOST"),
        "port": os.environ.get("RY_REDIS_PORT"),
        "db": os.environ.get("RY_ACTION_CACHE"),
        "password": os.environ.get("RY_REDIS_PASS", ""),
        "pool_max_size": 100,
    },
}

# Agent Server 配置
RY_AGENT_SERVER = os.environ.get("RY_AGENT_SERVER", "http://192.168.12.254:8283")

# 数字伙伴运行时服务
LION_AGENT_ENDPOINT = os.environ.get("RY_LION_AGENT_ENDPOINT", "http://lion-agent:8000")
LION_AGENT_INTERNAL_TOKEN = os.environ.get("RY_LION_AGENT_INTERNAL_TOKEN", "")
MGMT_INTERNAL_BASE_URL = os.environ.get("MGMT_INTERNAL_BASE_URL", "http://lion-mgmt:8000")
# 已弃用：运行时凭证改由 llm_model_config 表提供（见 services/llm_config/resolver.py）。
# 保留定义仅为兼容 .env 文件与 lion_memory 容器；lion_mgmt 业务代码不再读取。
RY_AI_LITE_MODEL = (
    os.environ.get("RY_AI_LITE_MODEL") or "aioe-lite"
).strip() or "aioe-lite"
LION_ACTION_INTERNAL_TOKEN = os.environ.get("RY_INTERNAL_TOKEN") or os.environ.get(
    "RY_LION_ACTION_INTERNAL_TOKEN", ""
)

# 指挥中心任务派单超时：pending_dispatch 超过该秒数仍未调度成功则自动置为失败
COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS = int(
    os.environ.get("RY_COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS") or 300
)
# 单次 lion_agent 派单决策请求超时；需短于上面的完整业务派单窗口
COMMAND_DISPATCH_REQUEST_TIMEOUT_SECONDS = int(
    os.environ.get("RY_COMMAND_DISPATCH_REQUEST_TIMEOUT_SECONDS") or 120
)
# 派单超时清扫器的扫描间隔（秒）
COMMAND_TASK_SWEEP_INTERVAL_SECONDS = int(
    os.environ.get("RY_COMMAND_TASK_SWEEP_INTERVAL_SECONDS") or 60
)

# lion_agent 的 aioe-cli 工具回调 AIOE OpenAPI 的内部地址（容器网络内可达）
AIOE_INTERNAL_URL = os.environ.get("RY_AIOE_INTERNAL_URL", "http://lion-mgmt:8000")

# 数字伙伴长期记忆后端（SPEC §8 阶段一 1A 第 2 步）。
# 这是**进程级全局开关，不是按租户灰度**——环境变量没有 tenant 维度，
# execute_coordinator 对所有租户统一构造 payload。若将来确需按租户灰度，
# 需另建 tenant-scoped 配置项并定义优先级/权限/缓存刷新，1A 不做。
#   cognee：保持迁移前行为（get_or_refresh_jwt + payload["cognee"]），回滚路径。
#   reme  ：发 payload["memory"]（SPEC §5.7 新契约）。
# 未知值 fail-closed：拒绝启动，不回落默认值。
MEMORY_BACKEND_COGNEE = "cognee"
MEMORY_BACKEND_REME = "reme"
MEMORY_BACKENDS = frozenset({MEMORY_BACKEND_COGNEE, MEMORY_BACKEND_REME})
MEMORY_BACKEND = _strict_env_choice("RY_MEMORY_BACKEND", MEMORY_BACKEND_REME, MEMORY_BACKENDS)

# 服务基础 URL（用于构造回调地址）
RY_SERVICE_URL = os.environ.get("RY_SERVICE_URL", "http://localhost:8000")

# Embedding 服务配置
RY_EMBEDDING_PROVIDER = os.environ.get(
    "RY_EMBEDDING_PROVIDER", "openai"
)  # openai | local
RY_EMBEDDING_MODEL = os.environ.get("RY_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-4B")
RY_EMBEDDING_API_KEY = os.environ.get("RY_EMBEDDING_API_KEY", "")
RY_EMBEDDING_API_BASE = os.environ.get(
    "RY_EMBEDDING_API_BASE", "https://api-inference.modelscope.cn/v1"
)

# Agent 默认配置
RY_AGENT_CONTEXT_WINDOW = int(os.environ.get("RY_AGENT_CONTEXT_WINDOW", "8000"))

# 自动化巡检AI配置
# 已弃用：运行时凭证改由 llm_model_config 表提供（见 services/llm_config/resolver.py）。
# 保留定义仅为兼容 .env 文件与 lion_memory 容器；lion_mgmt 业务代码不再读取。
RY_AI_MODEL = os.environ.get("RY_AI_MODEL", "aioe")
LLM_KEY = os.environ.get("LLM_KEY")
RY_AI_API_BASE = os.environ.get("RY_AI_API_BASE", "https://oapi.hongcloud.net/v1")
