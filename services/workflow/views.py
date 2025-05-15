#!/usr/bin/env python
# coding=utf-8
import asyncio
import os
import time
from collections import defaultdict
from datetime import datetime

import aiofile
import orjson
import pytz
from sanic import Blueprint, Request, Websocket
from sanic.exceptions import WebsocketClosed
from sanic.log import logger
from tortoise.expressions import Q
from websockets.exceptions import ConnectionClosed

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission, load_license
from libs.utils import gen_order_version, send_kafka_message
from models import Playbook, Scene, ToolAction, Tool, Runlog
from services.tool_management.views import tool_action_update, tool_action_playbook_delete
from settings import KAFKA_CONFIG, TIMED_TASK_TOPIC

workflow_bp = Blueprint('workflow', url_prefix='v1/playbook')
_all__ = ["workflow_bp"]


@workflow_bp.post("/new")
@requires_permission("workflow:c")
async def create_workflow(request):
    """
    新建剧本

    请求参数：
        name: 剧本名，必须参数
        remark: 备注，可选
        tags: 标签，可选
        flow_json: 流程图布局信息，可选
        scenes: 场景，列表格式，可选
    """
    total_up_value = 0
    # 判断剧本数量是否达到了创建上限
    total_count = await Playbook.filter(deleted=False, internal=False).count()
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)
    for item in lic["menuInfo"]["moduleList"]:
        if item["name"] == "剧本数量限制":
            if item["auth"] and int(item["expireTime"]) > int(time.time() * 1000):
                total_up_value = int(item["value"])

    if total_count >= total_up_value:
        raise CustomError(ErrorCode.PlaybookCreateLimit)

    # 判断name是否已经存在，如果存在的话要抛出异常
    if await Playbook.filter(name=request.json.get("name"), deleted=False).exists():
        raise CustomError(ErrorCode.PlaybookNameExists)

    playbook = await Playbook.create(
        name=request.json.get("name"),
        remark=request.json.get("remark", ""),
        tags=request.json.get("tags") or [],
        flow_json=request.json.get("flow_json") or {},
        status=0,
        version=gen_order_version(),
        creator=request.ctx.session['account_info'],
        updator=request.ctx.session['account_info']
    )
    if playbook.flow_json:
        action_ids = await statistics_data(playbook.flow_json)
        await tool_action_update({playbook.id: action_ids})

    if request.json.get('scenes'):
        scenes = await Scene.filter(name__in=request.json.get('scenes')).all()
        await playbook.scenes.add(*scenes)
    await playbook.fetch_related("scenes", "updator")

    return response_ok(playbook.json)


@workflow_bp.post("/list")
@requires_permission("workflow:r")
async def list_workflow(request):
    """
    获取剧本列表

    请求参数：
        page: 可选，分页的页码，默认1
        size: 可选，分页的大小，默认10
        keyword: 可选，搜索时使用，支持搜索名称或备注
        scene: 可选，搜索时使用，支持搜索场景
        status: 可选，搜索时使用，支持搜索状态，0:草稿 1:已发布
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    offset = (page - 1) * size
    limit = size
    # 分页查询Account，返回总数和数据
    condition = Q(deleted=False)
    if request.json.get("keyword"):
        # 模糊搜索name字段和remark字段
        keyword = request.json.get("keyword")
        condition = condition & (Q(name__icontains=keyword) | Q(remark__icontains=keyword))
    if request.json.get("scene"):
        scenes = await Scene.filter(name__in=request.json.get("scene")).all()
        condition = condition & Q(scenes__in=scenes)
    if request.json.get("status"):
        condition = condition & Q(status=request.json.get("status"))
    total = await Playbook.filter(condition).count()
    playbooks = await Playbook.filter(condition).offset(offset).limit(limit).all().prefetch_related("scenes", "creator", "updator")
    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [p.simple_json for p in playbooks]
    })


@workflow_bp.post("/detail")
@requires_permission("workflow:r")
async def detail_workflow(request):
    """
    获取剧本详情

    请求参数：
        id: 必填，剧本ID
    """
    playbook = await Playbook.get(id=request.json.get("id")).prefetch_related("creator", "updator", "scenes")
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)
    return response_ok(playbook.json)


@workflow_bp.post("/edit")
@requires_permission("workflow:u")
async def edit_workflow(request):
    """
    修改剧本，已发布的剧本是不允许修改的，如果修改已经发布的剧本，会自动生成一个新版本的草稿

    请求参数：
        id: 必填，剧本ID
        name: 可选，剧本名
        remark: 可选，备注
        start_app: 可选，开始节点
        end_app: 可选，结束节点
        if_list: 可选，判断节点
        audit_list: 可选，审批节点
        tags: 可选，标签
        flow_json: 可选，流程图布局信息
    """
    playbook = await Playbook.get(id=request.json.get("id"))
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)

    params = request.json
    has_change = False
    for p in params:
        # 敏感属性不允许修改
        if p in ['id', 'ctime', 'utime', 'deleted', 'creator', 'updator', 'status', 'version']:
            continue
        if p == "flow_json":
            if playbook.flow_json:
                action_ids = await statistics_data(playbook.flow_json)
                await tool_action_playbook_delete({playbook.id: action_ids})
            if params[p]:
                action_ids = await statistics_data(params[p])
                await tool_action_update({playbook.id: action_ids})
        if hasattr(playbook, p):
            has_change = True
            setattr(playbook, p, params[p])
    if has_change:
        playbook.updator = request.ctx.session['account_info']
        # 已发布的剧本是不允许修改的
        if playbook.status == 1:
            playbook.version = gen_order_version()
            await playbook.create()
        else:
            await playbook.save()
    return response_ok(
        {"id": playbook.id, "name": playbook.name, "version": playbook.version, "status": playbook.status})


@workflow_bp.post("/publish")
@requires_permission("workflow:p")
async def publish_workflow(request):
    """
    发布剧本

    请求参数：
        id: 必填，剧本ID
    """
    await Playbook.filter(id=request.json.get("id")).update(status=1, updator=request.ctx.session['account_info'],
                                                            version=gen_order_version())
    return response_ok("剧本发布成功")


@workflow_bp.post("/del")
@requires_permission("workflow:d")
async def delete_workflow(request):
    """
    删除剧本

    请求参数：
        id: 剧本ID
    """
    playbook = await Playbook.filter(id=request.json.get("id"), internal=False).first()
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)
    playbook.deleted = True
    await playbook.save()
    if playbook.flow_json:
        action_ids = await statistics_data(playbook.flow_json)
        await tool_action_playbook_delete({playbook.id: action_ids})
    return response_ok("剧本删除成功")


async def poll_database_and_notify(ws, run_id, formatted_time):
    updated_time = formatted_time
    try:
        while True:
            datas = await Runlog.filter(run_id=run_id, utime__gt=updated_time).all().prefetch_related("playbook")
            if datas:
                payload = []
                for data in datas:
                    payload.append(data.json)
                await ws.send(orjson.dumps({"data": payload}).decode())
                updated_time = datas[-1].utime
            await asyncio.sleep(5)
    except WebsocketClosed:
        logger.info("Websocket已关闭，停止轮询")
    except asyncio.CancelledError:
        logger.info("Websocket已取消，停止轮询")
    except ConnectionClosed:
        logger.info("Websocket连接已断开，停止轮询")
    except Exception as e:
        logger.error(f"轮询异常：{e}")


@workflow_bp.websocket('/run')
async def execute_action(request: Request, ws: Websocket):
    """
        执行接口，websocket通信

        请求参数：
            - type str 类型 flow代表执行剧本， action代表执行动作      放在路由中传输
            - playbook_input dict 剧本输入参数 当type为flow时必填      用websocket发送
            示例如下：
            {
                "flow_id": "73ecfba0-1aa4-4128-b99a-b97fb08a14be",      剧本id
                "flow_input": {
                    "l":"123456789",        剧本输入参数
                    "c":"123456789"         剧本输入参数
                }
            }
            - action_input dict 动作输入参数 当type为action时必填      用websocket发送
            示例如下：
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
    """
    auth_data = await asyncio.wait_for(ws.recv(), timeout=10)
    auth_json = orjson.loads(auth_data)
    user_id = auth_json['request_header']['id']

    tz = pytz.timezone("Asia/Shanghai")
    now = datetime.now(tz)
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " +0800"
    formatted_time = datetime.strptime(formatted_time, '%Y-%m-%d %H:%M:%S.%f %z')

    msg = {}
    type = request.args.get("type")
    msg["run_type"] = type
    msg["run_id"] = gen_order_version(length=6)
    raw = await ws.recv()
    params = orjson.loads(raw)
    params["executor"] = user_id
    msg["run_args"] = params

    await send_kafka_message(kafka_bootstrap_servers=KAFKA_CONFIG, topic=TIMED_TASK_TOPIC, message=msg)

    await poll_database_and_notify(ws, msg.get("run_id"), formatted_time)


@workflow_bp.post('/run/detail')
@requires_permission("workflow:r")
async def run_detail(request):
    """
    获取当前剧本执行状态和结果以及各节点的处置状态和详细信息

    请求参数：
        run_id: 必填，执行记录ID
    """
    run_id = request.json.get("run_id")
    run_logs = await Runlog.filter(run_id=run_id).all().prefetch_related("playbook")

    if not run_logs:
        raise CustomError(ErrorCode.NoExecutionRecord)

    return response_ok([r.json for r in run_logs])


@workflow_bp.post('/run/history')
@requires_permission("workflow:r")
async def run_history(request):
    """
    获取剧本执行历史

    请求参数：
        playbook_id: 必填，剧本ID
        page: 可选，页码，默认1
        size: 可选，每页组数，默认10
    """
    body = request.json
    playbook_id = body.get("playbook_id")
    page = int(body.get("page", 1))
    size = int(body.get("size", 10))

    if not playbook_id or page < 1 or size < 1:
        raise CustomError(ErrorCode.ParameterError)

    offset = (page - 1) * size
    limit = size

    all_run_ids = await Runlog.filter(playbook_id=playbook_id, deleted=False).order_by("-utime").values_list("run_id",
                                                                                                             "utime")
    seen = set()
    run_ids_query = []
    for run_id, utime in all_run_ids:
        if run_id not in seen:
            seen.add(run_id)
            run_ids_query.append(run_id)

    total = len(run_ids_query)

    run_logs = await Runlog.filter(run_id__in=run_ids_query).order_by("-utime").prefetch_related("playbook")

    grouped_data = defaultdict(list)
    status_data = defaultdict(list)
    for log in run_logs:
        grouped_data[log.run_id].append(log.json)
        status_data[log.run_id].append(log.status)

    data = []
    success = 0
    fail = 0
    for run_id in run_ids_query:
        if -1 not in status_data[run_id]:
            success += 1
            status = "success"
        else:
            fail += 1
            status = "fail"
        data.append(
            {
                "run_id": run_id,
                "status": status,
                "logs": grouped_data.get(run_id, [])
            })

    data = data[offset:offset + limit]
    if not data:
        return response_ok({
            "total": total,
            "page": page,
            "size": size,
            "data": []
        })

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "success": success,
        "fail": fail,
        "data": data
    })


@workflow_bp.post('/input')
@requires_permission("workflow:r")
async def input_data(request):
    """
    获取指定剧本的输入参数

    请求参数：
        id: 剧本ID
    """
    redis_client = request.ctx.cache_client
    playbook = await Playbook.get(id=request.json.get("id"))
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)

    nodes = playbook.flow_json["nodes"]

    input_id = nodes[0]["id"]

    input_list = []

    for node in nodes[1:]:
        if "input" not in node["properties"].keys():
            continue
        for key, value in node["properties"]["input"].items():
            if input_id in value:
                data = await redis_client.get(node["properties"]["app_name"] + "_" + node["properties"]["version"])
                if data:
                    data = orjson.loads(orjson.dumps(data))
                    for action in (data.get('actions') or []):
                        if node["properties"]["action"] in action.values():
                            for item in action["input"]:
                                if key == item["key"]:
                                    action["input"] = item
                                    break
                            input_list.append({value.split(".output.")[-1].rstrip("}"): action})
                else:
                    actions = await ToolAction.filter(tool_name=node["properties"]["app_name"],
                                                      version_number=node["properties"]["version"]).all()
                    for action in actions:
                        if node["properties"]["action"] == action.action["name"]:
                            for item in action.action["input"]:
                                if key == item["key"]:
                                    action.action["input"] = item
                                    break
                            input_list.append({value.split(".output.")[-1].rstrip("}"): action.action})
                    tool = await Tool.filter(name=node["properties"]["app_name"],
                                             version=node["properties"]["version"]).first()
                    if tool:
                        config_path = os.path.abspath(os.path.join(tool.path, f"config.json"))

                        async with aiofile.AIOFile(config_path, 'r', encoding='utf-8') as f:
                            content = await f.read()
                            # 将config数据存入redis
                            await redis_client.set(node["properties"]["app_name"] + "_" + node["properties"]["version"], content, ttl=2592000)

    return response_ok(input_list)


async def statistics_data(flow_json):
    actions_list = []
    for node in flow_json["nodes"]:
        if "app_name" in node["properties"] and "action" in node["properties"] and "version" in node["properties"]:
            tool_name = node["properties"]["app_name"]
            version_number = node["properties"]["version"]
            action_name = node["properties"]["action"]
            actions = await ToolAction.filter(tool_name=tool_name, version_number=version_number).all()
            for action in actions:
                if action_name == action.action["func"]:
                    actions_list.append(action.id)

    return actions_list
