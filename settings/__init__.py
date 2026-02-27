# coding=utf-8

import os

import pytz

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

if os.environ.get("RY_ENV") == "DEV":
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(BASE_DIR, "settings/.env"))
    except:
        pass

TZ = pytz.timezone(os.environ.get("TZ", "Asia/Shanghai"))

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
AUTHENTICATION_SESSION_EXPIRATION = 60 * 5
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
}

# 健康检查相关
HEALTH = True
HEALTH_ENDPOINT = True
HEALTH_URL_PREFIX = "/health"

# 跨域相关
ENABLE_CORS: bool = os.environ.get("RY_CORS", True)
CORS_SUPPORTS_CREDENTIALS: bool = os.environ.get("RY_CORS_CREDENTIAL", True)

# Websocket
WEBSOCKET_MAX_SIZE = 2**20  # 消息最大1MB
WEBSOCKET_PING_INTERVAL = 45
RESPONSE_TIMEOUT = 600
REQUEST_TIMEOUT = 600
REQUEST_MAX_SIZE = 20 * 1024 * 1024  # 20MB
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
        "serializer": {"class": "aiocache.serializers.MsgPackSerializer", "encoding": None},
        "timeout": 2,
        "endpoint": os.environ.get("RY_REDIS_HOST"),
        "port": os.environ.get("RY_REDIS_PORT"),
        "db": os.environ.get("RY_CHAT_DB"),
        "password": os.environ.get("RY_REDIS_PASS", ""),
        "pool_max_size": 100,
    },
    "redis_action": {
        "cache": "aiocache.RedisCache",
        "serializer": {"class": "aiocache.serializers.MsgPackSerializer", "encoding": None},
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

# Embedding 服务配置
RY_EMBEDDING_PROVIDER = os.environ.get("RY_EMBEDDING_PROVIDER", "openai")  # openai | local
RY_EMBEDDING_MODEL = os.environ.get("RY_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-4B")
RY_EMBEDDING_API_KEY = os.environ.get("RY_EMBEDDING_API_KEY", "")
RY_EMBEDDING_API_BASE = os.environ.get("RY_EMBEDDING_API_BASE", "https://api-inference.modelscope.cn/v1")

# Agent 默认配置
RY_AGENT_DEFAULT_LLM = os.environ.get("RY_AGENT_MODEL", "openai-proxy/gpt-4o")
RY_AGENT_CONTEXT_WINDOW = int(os.environ.get("RY_AGENT_CONTEXT_WINDOW", "8000"))
