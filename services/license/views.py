# coding=utf-8

import datetime
import hashlib
import os
import time

from sanic import Blueprint
from sanic import response
from sanic.request import Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.security.authorization import requires_permission, load_license
from libs.utils import verify_license
from models import Tool, Playbook, Account
from settings import LICENSE_DIR

license_bp = Blueprint('license', url_prefix='/license')
_all__ = ["license_bp"]

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
            }
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


@license_bp.post("/verify")
@requires_permission("lic:c")
async def verify_license_api(request: Request):
    """
    验证license文件

    请求参数：
        - file: license文件
    """
    if "file" not in request.files:
        return response.json({"success": False, "message": "缺少必要字段"}, status=400)

    license_file = request.files.get("file")
    file_content = license_file.body
    verify_license(file_content)
    new_filename = hashlib.md5(file_content).hexdigest() + ".lic"
    new_file_path = os.path.join(LICENSE_DIR, new_filename)

    for filename in os.listdir(LICENSE_DIR):
        if filename.endswith(".lic"):
            old_file_path = os.path.join(LICENSE_DIR, filename)
            if os.path.isfile(old_file_path):
                os.remove(old_file_path)

    with open(new_file_path, "wb") as f:
        f.write(file_content)

    request.app.m.restart(zero_downtime=True, all_workers=True)

    return response_ok("上传成功，正在加载配置文件，请稍后！")


@license_bp.post("/get")
async def get_license(request: Request):
    """
    根据权限加载页面

    请求参数
    """
    payload = await token_decode(request)
    user = await Account.get(id=payload["sub"]).prefetch_related("roles")
    if not user:
        raise CustomError(ErrorCode.AccountNotFound)

    menu_dict = {}
    for role in user.roles:
        if role.permissions:
            if "*:*" in role.permissions:
                role.permissions = MENU_DICT.keys()
            for permission in role.permissions:
                if permission.split(":")[0] == "dashboard":
                    continue
                if MENU_DICT[permission.split(":")[0]]["path"] not in menu_dict.keys():
                    menu_dict[MENU_DICT[permission.split(":")[0]]["path"]] = MENU_DICT[permission.split(":")[0]]
                else:
                    for children in MENU_DICT[permission.split(":")[0]]["children"]:
                        menu_dict[MENU_DICT[permission.split(":")[0]]["path"]]["children"].append(children)

    return response_ok(list(menu_dict.values()))


@license_bp.post("/show")
@requires_permission("lic:r")
async def get_all_license(request: Request):
    """
    获取所有license文件信息用于页面展示
    """
    consume_info = {
        "应用集成": await Tool.filter(deleted=False).count(),
        "剧本编排": await Playbook.filter(deleted=False).count()
    }

    menu_dict = dict()
    lic = load_license()

    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)

    # 基础字段赋值
    menu_dict["name"] = lic["company"]
    menu_dict["version"] = lic["version"]
    menu_dict["createTime"] = fmt_time(lic.get("createTime"))
    menu_dict["expireTime"] = fmt_time(lic.get("expireTime"))
    menu_dict["lic_status"] = True if int(lic.get("expireTime")) > int(time.time() * 1000) else False
    menu_dict["menuInfo"] = {}

    # 获取菜单信息
    group_list = lic.get("grouplist", {})

    for key, item in group_list.items():
        val = None
        if key in ["剧本编排", "应用集成"]:
            if int(item.get("value", 0)) == 99999:
                val = "无限"
            else:
                val = str(consume_info.get(key, 0)) + "/" + str(item.get("value", 0))
        menu_entry = menu_dict["menuInfo"].setdefault(key, {})
        menu_entry["value"] = val
        menu_entry["auth"] = True if int(item.get("expireTime")) > int(time.time() * 1000) else False
        menu_entry["description"] = item.get("description")
        menu_entry["expireTime"] = fmt_time(int(item.get("expireTime")))

    return response_ok(menu_dict)


def fmt_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(int(timestamp) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
