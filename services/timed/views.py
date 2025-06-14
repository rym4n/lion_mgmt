import datetime
import re
from collections import defaultdict

import pytz
from sanic import Blueprint
from sanic.log import logger
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from models import Timed, Playbook, ToolAction, Runlog, PlaybookVersion
from settings import KAFKA_SERVERS, TASK_TOPIC

timed_bp = Blueprint('timed', version=1, url_prefix='/timed')
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
                "resource": "cs"                 资源名称
                "node_name":"自定义扫描",    节点名称，定时任务模块中取动作描述
                "action":"custom_scan",     动作名称 英文标识
                "action_name":"自定义扫描",    动作名称 中文名称
                "retry_times":1,             重试次数
                "timeout":300,               超时时间,单位秒
            }
       - status int 执行类型  必填  0为单次执行，1为周期执行
       - tactics str 执行策略  必填   如果为单次执行，则传此类型参数2025-12-12 00:00:00，如果为周期执行，则传*/5 * * * *类型参数
       - end_time str 结束时间  为周期执行时填写，格式为2025-12-12 00:00:00
    """
    data = request.json
    name = data.get("name")
    operate_type = int(data.get("type"))
    playbook_id = data.get("playbook_id") or ""
    playbook_input = data.get("playbook_input") or {}
    action_id = data.get("action_id") or ""
    action_input = data.get("action_input") or {}
    status = int(data.get("status"))
    tactics = data.get("tactics")
    end_time = data.get("end_time") or ""
    if any(x is None for x in [name, operate_type, status, tactics]):
        raise CustomError(ErrorCode.ParameterError)
    if status not in [0, 1] or operate_type not in [0, 1]:
        raise CustomError(ErrorCode.ParameterError)
    if operate_type == 0 and not playbook_id:
        raise CustomError(ErrorCode.ParameterError)
    if operate_type == 1 and not action_id:
        raise CustomError(ErrorCode.ParameterError)

    if await Timed.filter(name=request.json.get("name"), deleted=False).exists():
        raise CustomError(ErrorCode.TimedNameExists)

    msg = {}
    playbook = None
    action = None
    playbook_version = None
    timer_rule = {}
    if operate_type == 0:
        playbook = await Playbook.get_or_none(id=playbook_id, deleted=False)
        playbook_version = await PlaybookVersion.get_or_none(id=playbook_input.get("flow_id"))
        if not playbook:
            raise CustomError(ErrorCode.PlaybookNotFound)
        msg["run_type"] = "flow"
        playbook_input["executor"] = request.ctx.session.get("account_info").id
        if any(key not in playbook_input.keys() for key in ["flow_id", "flow_input", "executor"]):
            raise CustomError(ErrorCode.PlaybookInputError)
        msg["run_args"] = playbook_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            if not is_valid_crontab(tactics):
                logger.info(f"定时任务{name}的执行策略{tactics}不符合crontab格式，创建失败")
                raise CustomError(ErrorCode.CronExpressionError)
            timer_rule["crontab"] = tactics
    elif operate_type == 1:
        action = await ToolAction.get_or_none(id=action_id, deleted=False)
        if not action:
            raise CustomError(ErrorCode.ActionNotFoundError)
        action_input["executor"] = request.ctx.session.get("account_info").id
        if any(key not in action_input for key in
               ["app_name", "name", "app_ver", "main", "action_input", "resource", "node_name", "action_name", "action",
                "executor", "retry_times", "timeout"]):
            raise CustomError(ErrorCode.ActionInputError)
        msg["run_type"] = "action"
        msg["run_args"] = action_input
        if status == 0:
            msg["timer"] = "once"
            timer_rule["run_date"] = tactics
        elif status == 1:
            msg["timer"] = "cron"
            timer_rule["end_date"] = end_time
            if not is_valid_crontab(tactics):
                logger.info(f"定时任务{name}的执行策略{tactics}不符合crontab格式，创建失败")
                raise CustomError(ErrorCode.CronExpressionError)
            timer_rule["crontab"] = tactics
    timed = await Timed.create(name=name, type=operate_type, playbook=playbook, playbook_input=playbook_input,
                               action=action, playbook_version=playbook_version.version if playbook_version else None,
                               action_input=action_input, status=status, tactics=tactics, end_time=end_time, enable=1,
                               creator=request.ctx.session['account_info'], updator=request.ctx.session['account_info'])
    msg["timer_id"] = timed.id
    msg["timer_rule"] = timer_rule
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)
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
    all_items = await Timed.filter(deleted=False).prefetch_related("action", "playbook", "creator", "updator")

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
        raise CustomError(ErrorCode.ParameterError)

    timed = await Timed.get(id=timed_id, deleted=False).prefetch_related("action", "playbook", "creator",
                                                                         "updator")

    run_type = int(data.get("type", timed.type))
    if run_type not in [RUN_TYPE_FLOW, RUN_TYPE_ACTION]:
        raise CustomError(ErrorCode.ParameterError)

    enable = data.get("enable", timed.enable)
    status = data.get("status", timed.status)
    tactics = data.get("tactics", timed.tactics)
    end_time = data.get("end_time", timed.end_time)

    msg = {"run_type": "flow" if run_type == RUN_TYPE_FLOW else "action"}

    if run_type == RUN_TYPE_FLOW:
        playbook_id = data.get("playbook_id", timed.playbook.id)
        playbook_input = data.get("playbook_input", timed.playbook_input)

        if not playbook_id or not playbook_input:
            raise CustomError(ErrorCode.ParameterError)

        timed.playbook = await Playbook.get_or_none(id=playbook_id, deleted=False)
        playbook_version = await PlaybookVersion.get_or_none(id=playbook_input.get("flow_id"))
        timed.playbook_version = playbook_version.version if playbook_version else None

        playbook_input["executor"] = request.ctx.session.get("account_info").id

        if any(key not in playbook_input.keys() for key in ["flow_id", "flow_input", "executor"]):
            raise CustomError(ErrorCode.PlaybookInputError)

        timed.playbook_input = playbook_input
        timed.action = None
        timed.action_input = {}
        msg["run_args"] = playbook_input

    else:
        if not timed.action and not data.get("action_id"):
            raise CustomError(ErrorCode.ParameterError)

        action_id = data.get("action_id", timed.action.id if timed.action else None)
        action_input = data.get("action_input", timed.action_input)

        timed.action = await ToolAction.get_or_none(id=action_id, deleted=False)

        action_input["executor"] = request.ctx.session.get("account_info").id
        if any(key not in action_input for key in
               ["app_name", "name", "app_ver", "main", "action_input", "resource", "node_name", "action_name", "action",
                "executor", "retry_times", "timeout"]):
            raise CustomError(ErrorCode.ActionInputError)

        timed.action_input = action_input
        timed.playbook = None
        timed.playbook_version = None
        timed.playbook_input = {}
        msg["run_args"] = action_input

    if enable == DISABLED:
        msg["timer"] = "remove"
        msg["timer_rule"] = {}
    else:
        if status == EXEC_TYPE_CRON:
            if not is_valid_crontab(tactics):
                logger.info(f"定时任务{timed.name}的执行策略{tactics}不符合crontab格式，更新失败")
                raise CustomError(ErrorCode.CronExpressionError)
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
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)
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

    timed = await Timed.get_or_none(id=timed_id, deleted=False)
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
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    timed.deleted = True
    timed.updator = request.ctx.session.get("account_info")
    await timed.save()
    return response_ok("定时任务删除成功")


@timed_bp.post("/runlog")
@requires_permission("timed:r")
async def get_run_log(request):
    """
    获取定时任务执行日志 (需要定时任务查看权限)
    请求参数：
        - id str 定时任务id  必填
        - page int 页码  必填   默认1
        - size int 页大小  必填   默认10
        - start_time str 开始时间  非必填   格式为2025-12-12 00:00:00
        - end_time str 结束时间  非必填   格式为2025-12-12 00:00:00
        - status int 状态  非必填   0为成功，1为失败
    """
    timed_id = request.json.get("id")
    page = int(request.json.get("page") or 1)
    size = int(request.json.get("size") or 10)
    start_time = request.json.get("start_time")
    end_time = request.json.get("end_time")
    status = request.json.get("status")

    if not timed_id:
        raise CustomError(ErrorCode.ParameterError)

    condition = Q(timer_id=timed_id, deleted=False)

    if start_time:
        start_time = datetime.datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        start_time = pytz.timezone("Asia/Shanghai").localize(start_time)
        condition &= Q(start_time__gte=start_time)
    if end_time:
        end_time = datetime.datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
        end_time = pytz.timezone("Asia/Shanghai").localize(end_time)
        condition &= Q(start_time__lte=end_time)
    if status in [0, 1]:
        condition &= Q(status=int(status))

    total = await Runlog.filter(condition).order_by('run_id').distinct().values_list('run_id', flat=True)

    run_ids = await Runlog.filter(condition).distinct().order_by('run_id').offset((page - 1) * size).limit(size).values_list('run_id', flat=True)

    if run_ids:
        all_logs = await Runlog.filter(run_id__in=run_ids, deleted=False).all().prefetch_related("event_id", "timer_id", "playbook_version")
        logs_by_run_id = defaultdict(list)
        for log in all_logs:
            logs_by_run_id[log.run_id].append(log.json)

        run_logs = [logs_by_run_id[run_id] for run_id in run_ids]
    else:
        run_logs = []

    await timer_success_rate(timed_id)

    return response_ok({
        "page": page,
        "size": size,
        "total": len(total),
        "data": run_logs
    })


def is_valid_crontab(expr):
    parts = expr.strip().split()
    if len(parts) != 5:
        return False

    ranges = [
        (0, 59), (0, 23), (1, 31), (1, 12), (0, 7)
    ]

    pattern = r'^\*(\/\d+)?$|^\d+(-\d+)?(\/\d+)?$'
    segment_re = re.compile(pattern)

    for i, part in enumerate(parts):
        min_val, max_val = ranges[i]
        for segment in part.split(','):
            if not segment_re.match(segment):
                return False

            if '/' in segment:
                base, step_str = segment.split('/', 1)
                if not step_str.isdigit() or (step := int(step_str)) < 1:
                    return False
                segment = base

            if '-' in segment:
                start, end = map(int, segment.split('-'))
                if not (min_val <= start <= end <= max_val):
                    return False
            elif segment != '*':
                if not segment.isdigit() or not (min_val <= (num := int(segment)) <= max_val):
                    return False

    return True


async def timer_success_rate(timed_id):
    timed = await Timed.get_or_none(id=timed_id, deleted=False)
    beijing_tz = pytz.timezone("Asia/Shanghai")
    now = datetime.datetime.now(beijing_tz)

    start_of_day = beijing_tz.localize(
        datetime.datetime.combine(now.date(), datetime.time.min)
    )
    end_of_day = start_of_day + datetime.timedelta(days=1)

    quantity = await Runlog.filter(timer_id=timed_id, start_time__gte=start_of_day, start_time__lte=end_of_day,
                                   deleted=False).count()

    if quantity > 10:
        success_quantity = await Runlog.filter(timer_id=timed_id, start_time__gte=start_of_day,
                                               start_time__lte=end_of_day, deleted=False, status=0).count()
        timed.success_rate = str(round(success_quantity / quantity, 4) * 100) + "%"
    else:
        recent_logs = await Runlog.filter(timer_id=timed_id, deleted=False).limit(10)
        success_quantity = sum(1 for log in recent_logs if log.status == 0)
        timed.success_rate = str(round(success_quantity / len(recent_logs), 4) * 100) + "%"
    runlog = await Runlog.filter(timer_id=timed_id, deleted=False).first()
    timed.result = "success" if runlog.status == 0 else "fail"
    await timed.save()
