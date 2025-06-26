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

# 安全相关
SECRET = os.environ.get("RY_SECRET_KEY", '5tdPY^zTX6I$akvNtLXey3y#gFEG@^46f!iLUdSy$Y')

SESSION_SAMESITE = None
SESSION_SECURE = True
SESSION_HTTPONLY = True
SESSION_DOMAIN = None
SESSION_ENCODING_ALGORITHM = 'HS256'
MAX_CHALLENGE_ATTEMPTS = 3
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
WEBSOCKET_PING_INTERVAL = 20
WEBSOCKET_PING_TIMEOUT = 20

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
                "host": "lion-pg",
                "port": 5432,
                "user": os.environ.get("RY_DB_USER"),
                "password": os.environ.get("RY_DB_PASS"),
                "database": "lion_mgmt",
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

MENU_DICT = {
    "role": {
        "path": "/System",
        "meta": {
            "requireRoot": True,
            "icon": "material-symbols-light:settings",
            "title": "系统设置",
            "rank": 99
        },
        "children": [
            {
                "path": "/System/Role/Role",
                "name": "Role",
                "component": "views/System/Role/Role.vue",
                "meta": {
                    "icon": "mdi:account-group-outline",
                    "title": "角色管理"
                }
            }
        ]
    },
    "user": {
        "path": "/System",
        "meta": {
            "requireRoot": True,
            "icon": "material-symbols-light:settings",
            "title": "系统设置",
            "rank": 99
        },
        "children": [
            {
                "path": "/System/User/User",
                "name": "User",
                "component": "views/System/User/User.vue",
                "meta": {
                    "icon": "bxs:user",
                    "title": "账号管理"
                }
            }
        ]
    },
    "event": {
        "path": "/EventModule",
        "component": "layout/index.vue",
        "meta": {
            "icon": "ep:document",
            "title": "事件管理",
            "rank": 20
        },
        "children": [
            {
                "path": "/EventModule/eventList",
                "name": "eventList",
                "component": "views/eventModule/eventList/index.vue",
                "meta": {
                    "icon": "material-symbols:event-list-outline",
                    "title": "事件列表"
                }
            }
        ]
    },
    "ingestion": {
        "path": "/EventModule",
        "component": "layout/index.vue",
        "meta": {
            "icon": "ep:document",
            "title": "事件管理",
            "rank": 20
        },
        "children": [
            {
                "path": "/EventModule/eventIngestion",
                "name": "eventIngestion",
                "component": "views/eventModule/event_ingestion/index.vue",
                "meta": {
                    "icon": "tabler:timeline-event",
                    "title": "事件接入"
                }
            }
        ]
    },
    "var": {
        "path": "/var",
        "component": "layout/index.vue",
        "redirect": "/var/index",
        "meta": {
            "icon": "codicon:variable-group",
            "title": "全局变量",
            "rank": 70
        },
        "children": [
            {
                "path": "/var/index",
                "name": "varIndex",
                "component": "views/var/index.vue",
                "meta": {
                    "title": "全局变量"
                }
            }
        ]
    },
    "approve": {
        "path": "/approve",
        "component": "layout/index.vue",
        "redirect": "/approve/index",
        "meta": {
            "icon": "carbon:customer-service",
            "title": "人工任务",
            "rank": 60
        },
        "children": [
            {
                "path": "/approve/index",
                "name": "approveIndex",
                "component": "views/approve/index.vue",
                "meta": {
                    "title": "人工任务"
                }
            }
        ]
    },
    "tool": {
        "path": "/toolManagement",
        "component": "layout/index.vue",
        "redirect": "/toolManagement/index",
        "meta": {
            "icon": "icon-park-solid:all-application",
            "title": "应用市场",
            "rank": 30
        },
        "children": [
            {
                "path": "/toolManagement/index",
                "name": "index",
                "component": "views/toolManagement/index.vue",
                "meta": {
                    "title": "应用市场"
                }
            }
        ]
    },
    "workflow": {
        "path": "/playbook",
        "component": "layout/index.vue",
        "redirect": "/playbook/index",
        "meta": {
            "icon": "mdi:script-text-outline",
            "title": "剧本管理",
            "rank": 40
        },
        "children": [
            {
                "path": "/playbook/index",
                "name": "playbookIndex",
                "component": "views/playbook/index.vue",
                "meta": {
                    "title": "剧本管理"
                }
            },
            {
                "path": "/playbook/index/:flow_id/:version_id/:status",
                "name": "playbookEdit",
                "component": "views/playbook/playbookEdit/index.vue",
                "meta": {
                    "showLink": False,
                    "activePath": "/playbook/index",
                    "title": "新建剧本"
                }
            },
        ]
    },
    "system": {
        "path": "/System",
        "meta": {
            "requireRoot": True,
            "icon": "material-symbols-light:settings",
            "title": "系统设置",
            "rank": 99
        },
        "children": [
            {
                "path": "/System/Status/Status",
                "name": "Status",
                "component": "views/System/Status/Status.vue",
                "meta": {
                    "icon": "mdi:server-outline",
                    "title": "系统状态"
                }
            },
            {
                "path": "/System/Set/Set",
                "name": "Set",
                "component": "views/System/Set/Set.vue",
                "meta": {
                    "icon": "mingcute:settings-6-line",
                    "title": "个性化设置"
                }
            }
        ]
    },
    "scene": {
        "path": "/playbook",
        "component": "layout/index.vue",
        "redirect": "/playbook/index",
        "meta": {
            "icon": "mdi:script-text-outline",
            "title": "剧本管理",
            "rank": 40
        },
        "children": [
            {
                "path": "/playbook/components/sceneView",
                "name": "playbookSceneView",
                "component": "views/playbook/components/sceneView.vue",
                "meta": {
                    "title": "场景管理",
                    "showLink": False,
                    "activePath": "/playbook/index"
                }
            }
        ]
    },
    "timed": {
        "path": "/timerTasks",
        "component": "layout/index.vue",
        "redirect": "/timerTasks/index",
        "meta": {
            "icon": "mdi:clock-outline",
            "title": "定时任务",
            "rank": 50
        },
        "children": [
            {
                "path": "/timerTasks/index",
                "name": "timerTasksIndex",
                "component": "views/timerTasks/index.vue",
                "meta": {
                    "title": "定时任务"
                }
            }
        ]
    },
    "lic": {
        "path": "/System",
        "meta": {
            "requireRoot": True,
            "icon": "material-symbols-light:settings",
            "title": "系统设置",
            "rank": 99
        },
        "children": [
            {
                "path": "/System/license/index",
                "name": "license",
                "component": "views/System/license/index.vue",
                "meta": {
                    "icon": "mdi:license",
                    "title": "产品许可"
                }
            }
        ]
    },
    "warroom": {
        "path": "/warroom",
        "component": "layout/index.vue",
        "redirect": "/warroom/index",
        "meta": {
            "icon": "icon-park-outline:computer",
            "title": "作战室",
            "rank": 10
        },
        "children": [
            {
                "path": "/warroom/index/:roomId?",
                "name": "warroom",
                "component": "views/warroom/WarRoom.vue",
                "meta": {
                    "title": "作战室",
                    "activePath": "/warroom/index/:roomId?"
                },
                "props": True
            }
        ]
    },
    # "dashboard": {
    #     "path": "/",
    #     "component": "layout/index.vue",
    #     "redirect": "/welcome",
    #     "meta": {
    #         "icon": "clarity:dashboard-line",
    #         "title": "仪表盘",
    #         "rank": 0
    #     },
    #     "children": [
    #         {
    #             "path": "/welcome",
    #             "name": "Welcome",
    #             "component": "views/welcome/index.vue",
    #             "meta": {
    #                 "title": "仪表盘",
    #                 "showLink": True
    #             }
    #         }
    #     ]
    # },
    "audit": {
        "path": "/System",
        "meta": {
            "requireRoot": True,
            "icon": "material-symbols-light:settings",
            "title": "系统设置",
            "rank": 99
        },
        "children": [
            {
                "path": "/System/Audit/Audit",
                "name": "Audit",
                "component": "views/System/Audit/Audit.vue",
                "meta": {
                    "icon": "ix:log",
                    "title": "审计日志"
                }
            }
        ]
    }
}
