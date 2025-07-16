# coding=utf-8

import os

# 项目根路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 日志文件路径
LOG_PATH = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_PATH, exist_ok=True)

# 文件上传的保存路径
UPLOAD_FOLDER = './data/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# vector配置目录
VECTOR_CFG = "./data/ship"
os.makedirs(VECTOR_CFG, exist_ok=True)

# 子进程配置文件输出目录
SUPERVISOR_PATH = "./data/action"
os.makedirs(SUPERVISOR_PATH, exist_ok=True)

# 存储上传文件的目录
TOOLS_DIR = "./data/tools"
os.makedirs(TOOLS_DIR, exist_ok=True)

# 存储许可文件的目录
LICENSE_DIR = "./data/license"
os.makedirs(LICENSE_DIR, exist_ok=True)

if os.environ.get("RY_ENV") == 'DEV':
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(BASE_DIR, 'settings/.env'))
    except:
        pass

# 应用资源配置加密密钥
RY_RES_KEY = os.environ.get("RY_RES_KEY")

# supervisord地址
RY_JOB_SERVER = os.environ.get("RY_JOB_SERVER")

# 系统背景图片名称配置
BG_NAME = "bg-voGX3LAo"

# kafka配置
KAFKA_SERVERS = os.environ.get("RY_KAFKA_SERVERS")

# 定时任务发送主题
TASK_TOPIC = os.environ.get("RY_TASK_TOPIC")
RUNLOG_TOPIC = os.environ.get("RY_RUNLOG_TOPIC")
WR_TOPIC = os.environ.get("RY_WR_TOPIC")

# ES配置
ES_HOST = os.environ.get("ELASTIC_HOST")
ES_USER = os.environ.get("ELASTIC_USER")
ES_PASS = os.environ.get("ELASTIC_PASSWORD")

# 安全相关
SECRET = os.environ.get("RY_SECRET_KEY", '5tdPY^zTX6I$akvNtLXey3y#gFEG@^46f!iLUdSy$Y')

SESSION_SAMESITE = None
SESSION_SECURE = True
SESSION_HTTPONLY = True
SESSION_DOMAIN = None
SESSION_ENCODING_ALGORITHM = 'HS256'
MAX_CHALLENGE_ATTEMPTS = 5
AUTHENTICATION_SESSION_EXPIRATION = 60 * 5
AUTHENTICATION_REFRESH_EXPIRATION = 60 * 60 * 8

# 健康检查相关
HEALTH = True
HEALTH_ENDPOINT = True
HEALTH_URL_PREFIX = '/health'

# 跨域相关
ENABLE_CORS: bool = os.environ.get("RY_CORS", True)
CORS_SUPPORTS_CREDENTIALS: bool = os.environ.get("RY_CORS_CREDENTIAL", True)

# Websocket
WEBSOCKET_MAX_SIZE = 2 ** 20  # 消息最大1MB
WEBSOCKET_PING_INTERVAL = 60
WEBSOCKET_PING_TIMEOUT = 75

# 日志配置
BASE_LOGGING = {
    'version': 1,
    'loggers': {
        '': {
            'level': 'INFO',
            'handlers': ['console', 'info_file', 'error_file'],
            'propagate': False
        },
        'sanic': {
            'level': 'INFO',
            'handlers': ['console', 'info_file', 'error_file'],
            'propagate': False
        },
    },
    'formatters': {
        'default': {
            'format': '%(asctime)s [%(filename)s:%(lineno)d:%(funcName)s] [%(levelname)s] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'default',
        },
        'info_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_PATH, 'info.log'),
            'maxBytes': 100 * 1024 * 1024,
            'backupCount': 3,
            'encoding': 'utf8',
            'level': 'INFO',
            'formatter': 'default',
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_PATH, 'error.log'),
            'maxBytes': 100 * 1024 * 1024,
            'backupCount': 3,
            'encoding': 'utf8',
            'level': 'ERROR',
            'formatter': 'default',
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
                "maxsize": 30,
                # "minsize": 1, # 最小连接数
                # 关闭池中非活动连接的秒数。通过 0 则禁用此机制。
                "max_inactive_connection_lifetime": 60
            }
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
    'default': {
        'cache': "aiocache.SimpleMemoryCache",
        'serializer': {
            'class': "aiocache.serializers.PickleSerializer"
        }
    },
    'redis_mgmt': {
        'cache': "aiocache.RedisCache",
        'serializer': {
            'class': "aiocache.serializers.PickleSerializer"
        },
        'namespace': 'lion_mgmt',
        'timeout': 2,
        'endpoint': os.environ.get('RY_REDIS_HOST'),
        'port': os.environ.get('RY_REDIS_PORT'),
        'db': os.environ.get('RY_REDIS_DB'),
        'password': os.environ.get('RY_REDIS_PASS', ''),
        'pool_max_size': 50
    },
    'redis_chat': {
        'cache': "aiocache.RedisCache",
        'serializer': {
            'class': "aiocache.serializers.MsgPackSerializer"
        },
        'timeout': 2,
        'endpoint': os.environ.get('RY_REDIS_HOST'),
        'port': os.environ.get('RY_REDIS_PORT'),
        'db': os.environ.get('RY_CHAT_DB'),
        'password': os.environ.get('RY_REDIS_PASS', ''),
        'pool_max_size': 50
    },
    'redis_action': {
        'cache': "aiocache.RedisCache",
        'serializer': {
            'class': "aiocache.serializers.MsgPackSerializer"
        },
        'namespace': 'lion_mgmt',
        'timeout': 2,
        'endpoint': os.environ.get('RY_REDIS_HOST'),
        'port': os.environ.get('RY_REDIS_PORT'),
        'db': os.environ.get('RY_ACTION_CACHE'),
        'password': os.environ.get('RY_REDIS_PASS', ''),
        'pool_max_size': 2
    }
}
