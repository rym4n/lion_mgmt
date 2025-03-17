# coding=utf-8

import os

# 项目根路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 日志文件路径
LOG_PATH = os.path.join(BASE_DIR, 'logs')

if not os.path.exists(LOG_PATH):
    os.makedirs(LOG_PATH)

if os.environ.get("RY_ENV") == 'DEV':
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, 'settings/.env'))

# 安全相关
SECRET = os.environ.get("RY_SECRET_KEY", '5tdPY^zTX6I$akvNtLXey3y#gFEG@^46f!iLUdSy$Y')

OAUTH_CLIENT: str = ''
OAUTH_SECRET: str = ''
OAUTH_REDIRECT: str = ''
SESSION_SAMESITE = None
SESSION_SECURE = True
SESSION_HTTPONLY = True
SESSION_DOMAIN = None
SESSION_ENCODING_ALGORITHM = 'HS256'
MAX_CHALLENGE_ATTEMPTS = 3
AUTHENTICATION_SESSION_EXPIRATION = 60 * 30
AUTHENTICATION_REFRESH_EXPIRATION = 60 * 60 * 8

# 跨域相关
ENABLE_CORS: bool = os.environ.get("RY_CORS", True)
CORS_SUPPORTS_CREDENTIALS: bool = os.environ.get("RY_CORS_CREDENTIAL", True)

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
            'backupCount': 1,
            'encoding': 'utf8',
            'level': 'INFO',
            'formatter': 'default',
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_PATH, 'error.log'),
            'maxBytes': 100 * 1024 * 1024,
            'backupCount': 1,
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
                "host": os.environ.get("RY_DB_HOST"),
                "port": os.environ.get("RY_DB_PORT"),
                "user": os.environ.get("RY_DB_USER"),
                "password": os.environ.get("RY_DB_PASS"),
                "database": os.environ.get("RY_DB_NAME"),
                "maxsize": os.environ.get("RY_DB_POOL") or 50,  # 最大连接数
                # "minsize": 1, # 最小连接数
                # 关闭池中非活动连接的秒数。通过 0 则禁用此机制。
                "max_inactive_connection_lifetime": 60 * 60
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
            'class': "aiocache.serializers.StringSerializer"
        }
    },
    'redis': {
        'cache': "aiocache.RedisCache",
        'serializer': {
            'class': "aiocache.serializers.StringSerializer"
        },
        'namespace': 'lion',
        'timeout': 2,
        'endpoint': os.environ.get('RY_REDIS_HOST'),
        'port': os.environ.get('RY_REDIS_PORT'),
        'db': os.environ.get('RY_REDIS_DB'),
        'password': os.environ.get('RY_REDIS_PASS', ''),
        'pool_max_size': 50
    }
}
