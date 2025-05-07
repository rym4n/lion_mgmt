from sanic import Blueprint

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import send_kafka_message
from models import Timed, Playbook, ToolAction
from settings import KAFKA_CONFIG, TIMED_TASK_TOPIC

timed_bp = Blueprint('timed', url_prefix='v1/timed')
_all__ = ["timed_bp"]

# 枚举类型
RUN_TYPE_FLOW = 0
RUN_TYPE_ACTION = 1
EXEC_TYPE_ONCE = 0
EXEC_TYPE_CRON = 1
DISABLED = 0
ENABLED = 1


def build_timer_rule(status, tactics, end_time):
    if status == EXEC_TYPE_ONCE:
        return {"timer": "once", "timer_rule": {"run_date": tactics}}
    else:
        return {"timer": "cron", "timer_rule": {"end_date": end_time, "crontab": tactics}}


@timed_bp.post("/add")
@requires_permission("timed:c")
async def add_timed(request):
    """
    添加定时任务 (需要定时任务创建权限)

    请求参数：
       - name  str 定时任务名称  必填
       - type int 执行操作类型  必填  0为执行剧本， 1为执行动作
       - playbook_id str 剧本id 当type为0时必填
       - playbook_input dict 剧本输入参数 当type为0时必填,示例如下：
            {
                "flow_id": "73ecfba0-1aa4-4128-b99a-b97fb08a14be",      剧本id
                "flow_input": {
                    "l":"123456789",        剧本输入参数
                    "c":"123456789"         剧本输入参数
                }
            }
       - action_id str 动作id 当type为1时必填
       - action_input dict 动作输入参数 当type为1时必填,示例如下：
            {
                "app_name":"huorong_edr",         应用名称，英文标识
                "name": "中文名称",               应用中文名称
                "app_ver":"v1746495758839",      应用版本号
                "main":"huorong_edr.py",          主程序文件名  获取工具版本时会传
                "action_input":{
                    "clients":"abcd",             动作输入参数
                    "groups":"123456789"          动作输入参数
                },
                "node_name":"自定义扫描",    节点名称，定时任务模块中取动作描述
                "action":"custom_scan",     动作名称 英文标识
                "action_name":"自定义扫描",    动作名称 中文名称
                "retry_times":1,             重试次数
                "timeout":300,               超时时间,单位秒
            }
       - status int 执行类型  必填  0为单次执行，1为周期执行
       - tactics str 执行策略  必填   如果为单次执行，则传此类型参数2025-12-12 00:00:00，如果为周期执行，则传*/5 * * * *类型参数
       - end_time int 结束时间  为周期执行时填写，格式为2025-12-12 00:00:00
    """
    data = request.json
    name = data.get("name")
    type = int(data.get("type"))
    playbook_id = data.get("playbook_id") or ""
    playbook_input = data.get("playbook_input") or {}
    action_id = data.get("action_id") or ""
    action_input = data.get("action_input") or {}
    status = int(data.get("status"))
    tactics = data.get("tactics")
    end_time = data.get("end_time") or ""
    if any(x is None for x in [name, type, status, tactics]):
        raise CustomError(ErrorCode.ParameterError)
    if status not in [0, 1] or type not in [0, 1]:
        raise CustomError(ErrorCode.ParameterError)
    if type == 0 and not playbook_id:
        raise CustomError(ErrorCode.ParameterError)
    if type == 1 and not action_id:
        raise CustomError(ErrorCode.ParameterError)

    if await Timed.filter(name=request.json.get("name"), deleted=False).exists():
        raise CustomError(ErrorCode.TimedNameExists)

    msg = {}
    playbook = None
    action = None
    timer_rule = {}
    if type == 0:
        playbook = await Playbook.filter(id=playbook_id, deleted=False).first()
        if not playbook:
            raise CustomError(ErrorCode.PlaybookNotFound)
        msg["run_type"] = "flow"
        playbook_input["executor"] = request.ctx.session.get("account_info").id
        msg["run_args"] = playbook_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            timer_rule["crontab"] = tactics
    elif type == 1:
        action = await ToolAction.filter(id=action_id, deleted=False).first()
        if not action:
            raise CustomError(ErrorCode.ActionNotFoundError)
        action_input["executor"] = request.ctx.session.get("account_info").id
        msg["run_type"] = "action"
        msg["run_args"] = action_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            timer_rule["crontab"] = tactics
    timed = await Timed.create(name=name, type=type, playbook=playbook, playbook_input=playbook_input, action=action,
                               action_input=action_input, status=status, tactics=tactics, end_time=end_time, enable=1,
                               creator=request.ctx.session['account_info'], updator=request.ctx.session['account_info'])
    msg["timer_id"] = timed.id
    msg["timer_rule"] = timer_rule
    await send_kafka_message(kafka_bootstrap_servers=KAFKA_CONFIG, topic=TIMED_TASK_TOPIC, message=msg)
    return response_ok(timed.json)


@timed_bp.post("/all")
@requires_permission("timed:r")
async def get_all(request):
    """
    获取所有定时任务列表 (需要定时任务查看权限)

    请求参数：
        - page int 页码  必填   默认1
        - size int 页大小  必填   默认10
        - keyword str 搜索关键字  可选 目前支持任务名称，动作名称，剧本名称搜索
    """
    page = int(request.json.get("page") or 1)
    size = int(request.json.get("size") or 10)
    keyword = request.json.get("keyword", "").strip()

    # 获取全部（后续在 Python 层过滤）
    all_items = await Timed.filter(deleted=False).select_related("action", "playbook", "creator", "updator")

    if keyword:
        keyword = keyword.lower()

        def match(item):
            name_match = keyword in (item.name or "").lower()

            action_desc = ""
            if item.action and isinstance(item.action.action, dict):
                action_desc = item.action.action.get("name", "")
            action_match = keyword in action_desc.lower()

            playbook_name = item.playbook.name if item.playbook else ""
            playbook_match = keyword in playbook_name.lower()

            return name_match or action_match or playbook_match

        filtered_items = list(filter(match, all_items))
    else:
        filtered_items = all_items

    total = len(filtered_items)
    start = (page - 1) * size
    end = start + size
    paged_items = filtered_items[start:end]

    return response_ok({
        "page": page,
        "size": size,
        "total": total,
        "items": [item.json for item in paged_items]
    })



@timed_bp.post("/update")
@requires_permission("timed:u")
async def update_timed(request):
    """
    更新定时任务 (需要定时任务更新权限)

    请求参数：
       - id str 定时任务id  必填
       - type int 执行操作类型  非必填  0为执行剧本， 1为执行动作
       - playbook_id str 剧本id 当type为0时必填
       - playbook_input dict 剧本输入参数 当type为0时必填
       - action_id str 动作id 当type为1时必填
       - action_input dict 动作输入参数 当type为1时必填
       - status int 执行类型  非必填  0为单次执行，1为周期执行
       - tactics str 执行策略  非必填   如果为单次执行，则传此类型参数2025-12-12 00:00:00，如果为周期执行，则传*/5 * * * *类型参数
       - enable int 是否启用  非必填  0为禁用，1为启用
       - end_time int 结束时间  为周期执行时填写，格式为2025-12-12 00:00:00
    """
    data = request.json
    timed_id = data.get("id")
    if not timed_id:
        raise CustomError(ErrorCode.ParameterError, "缺少定时任务 ID")

    timed = await Timed.filter(id=timed_id, deleted=False).select_related("action", "playbook", "creator", "updator").first()
    if not timed:
        raise CustomError(ErrorCode.TimedNotFound)

    run_type = data.get("type", timed.type)
    if run_type not in [RUN_TYPE_FLOW, RUN_TYPE_ACTION]:
        raise CustomError(ErrorCode.ParameterError, "type 类型错误")

    enable = data.get("enable", timed.enable)
    status = data.get("status", timed.status)
    tactics = data.get("tactics", timed.tactics)
    end_time = data.get("end_time", timed.end_time)

    msg = {"run_type": "flow" if run_type == RUN_TYPE_FLOW else "action"}

    if run_type == RUN_TYPE_FLOW:
        playbook_id = data.get("playbook_id") or (timed.playbook_input or {}).get("flow_id")
        playbook_input = data.get("playbook_input", timed.playbook_input)

        if not playbook_id or not playbook_input:
            raise CustomError(ErrorCode.ParameterError, "缺少剧本信息")

        timed.playbook = await Playbook.filter(id=playbook_id, deleted=False).first()
        timed.playbook_input = playbook_input
        timed.action = None
        timed.action_input = {}
        msg["run_args"] = playbook_input

    else:
        if not timed.action and not data.get("action_id"):
            raise CustomError(ErrorCode.ParameterError, "缺少动作信息")

        action_id = data.get("action_id", timed.action.id if timed.action else None)
        action_input = data.get("action_input", timed.action_input)

        timed.action = await ToolAction.filter(id=action_id, deleted=False).first()
        timed.action_input = action_input
        timed.playbook = None
        timed.playbook_input = {}
        msg["run_args"] = action_input

    if enable == DISABLED:
        msg["timer"] = "remove"
        msg["timer_rule"] = {}
    else:
        timer_info = build_timer_rule(status, tactics, end_time)
        msg.update(timer_info)
    msg["timer_id"] = timed.id

    timed.enable = enable
    timed.status = status
    timed.tactics = tactics
    timed.end_time = end_time
    timed.type = run_type
    timed.updator = request.ctx.session.get("account_info")

    await timed.save()
    await send_kafka_message(
        kafka_bootstrap_servers=KAFKA_CONFIG, topic=TIMED_TASK_TOPIC, message=msg
    )
    return response_ok(timed.json)


@timed_bp.post("/delete")
@requires_permission("timed:d")
async def delete_timed(request):
    """
    删除定时任务 (需要定时任务删除权限)

    请求参数：
       - id str 定时任务id  必填
    """
    timed_id = request.json.get("id")
    if not timed_id:
        raise CustomError(ErrorCode.ParameterError)

    timed = await Timed.filter(id=timed_id, deleted=False).first()
    if not timed:
        raise CustomError(ErrorCode.TimedNotFound)
    msg = {}
    if timed.type == RUN_TYPE_FLOW:
        msg["run_type"] = "flow"
        msg["run_args"] = timed.playbook_input
    else:
        msg["run_type"] = "action"
        msg["run_args"] = timed.action_input
    msg["timer"] = "remove"
    msg["timer_id"] = timed.id
    msg["timer_rule"] = {}
    await send_kafka_message(kafka_bootstrap_servers=KAFKA_CONFIG, topic=TIMED_TASK_TOPIC, message=msg)

    timed.deleted = True
    timed.updator = request.ctx.session.get("account_info")
    await timed.save()
    return response_ok("定时任务删除成功")