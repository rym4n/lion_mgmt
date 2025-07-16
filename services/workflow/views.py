#!/usr/bin/env python
# coding=utf-8
import asyncio
import datetime
import re
import time
from collections import defaultdict

import orjson
import pytz
from sanic import Blueprint
from tortoise.expressions import Q
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission, load_license, playbook_permission, \
    playbook_version_permission, check_playbook_permission
from libs.utils import gen_order_version
from models import Playbook, Scene, ToolAction, Runlog, Tag, PlaybookVersion, Role, PlaybookPermissions, \
    EventRelatedScript, Timed
from services.tools.views import tool_action_update, tool_action_playbook_delete
from settings import KAFKA_SERVERS, TASK_TOPIC

workflow_bp = Blueprint('workflow', url_prefix='/playbook')
_all__ = ["workflow_bp"]


@workflow_bp.post("/new")
@requires_permission("workflow:c")
async def create_workflow(request):
    """
    新建剧本

    请求参数：
        name: 剧本名，必须参数
        remark: 备注，可选
        tags: 标签，列表格式，例如 ["aaa","bbb"]，可选
        scenes: 场景，列表格式，例如 ["aaa","bbb"]，可选
        flow_json_list: 流程图具体内容, 列表格式, 列表的每个元素是一个独立的剧本json, 可选
    """
    data = request.json
    if not data.get("name"):
        raise CustomError(ErrorCode.ParameterError)

    total_up_value = 0
    # 判断剧本数量是否达到了创建上限
    total_count = await Playbook.filter(deleted=False).count()
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)
    for key, item in lic["grouplist"].items():
        if key == "剧本编排":
            if int(item["expireTime"]) > int(time.time() * 1000):
                total_up_value = int(item["value"])

    if total_up_value != 99999 and total_count >= total_up_value:
        raise CustomError(ErrorCode.PlaybookCreateLimit)

    # 判断name是否已经存在，如果存在的话要抛出异常
    if await Playbook.filter(name=data.get("name"), deleted=False).exists():
        raise CustomError(ErrorCode.PlaybookNameExists)

    flow_json_list = data.get("flow_json_list") or []
    creator = request.ctx.session['account_info']
    admin = await Role.get(id="111111")
    async with in_transaction():
        playbook = await Playbook.create(
            name=data.get("name"),
            remark=data.get("remark", ""),
            creator=creator
        )
        await PlaybookPermissions.create(pb=playbook, users=creator, roles=None, permissions=["r", "u", "d", "e", "p"])
        await PlaybookPermissions.create(pb=playbook, users=None, roles=admin, permissions=["r", "u", "d", "e", "p"])
        if data.get('tags'):
            tags = await Tag.filter(name__in=data.get('tags')).all()
            await playbook.tags.add(*tags)
        if data.get('scenes'):
            scenes = await Scene.filter(name__in=data.get('scenes')).all()
            await playbook.scenes.add(*scenes)

        await playbook.fetch_related('tags', 'scenes')

        for flow_json in flow_json_list:
            pv = await PlaybookVersion.create(
                playbook=playbook,
                version=gen_order_version(),
                status=0,
                change_desc=data.get("change_desc", ''),
                flow_json=flow_json,
                creator=request.ctx.session['account_info'],
                updator=request.ctx.session['account_info']
            )

            action_ids = await statistics_data(flow_json)
            await tool_action_update({pv.id: action_ids})

    return response_ok('success')


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
        tag: 可选，搜索时使用，支持搜索标签 str
    """
    json_params = request.json
    page = int(json_params.get("page", 1))
    size = int(json_params.get("size", 10))
    offset = (page - 1) * size
    limit = size

    user = request.ctx.session.get('account_info')
    user_id = str(user.id)
    user_roles = set(str(role.id) for role in user.roles)
    permissions = await PlaybookPermissions.filter(
        Q(users__id=user_id) | Q(roles__id__in=user_roles),
        permissions__contains=['r']
    ).values_list('pb_id', flat=True)
    pb_ids = set(permissions)

    # 分页查询Account，返回总数和数据
    condition = Q(deleted=False, id__in=pb_ids)
    if json_params.get("keyword"):
        # 模糊搜索name字段和remark字段
        keyword = json_params.get("keyword")
        condition = condition & (Q(name__icontains=keyword) | Q(remark__icontains=keyword))
    if json_params.get("scene"):
        scenes = await Scene.filter(name__in=json_params.get("scene")).all()
        if scenes:
            condition = condition & Q(scenes__in=scenes)

    if json_params.get("tag"):
        tags = await Tag.filter(name__in=json_params.get("tag")).all()
        if tags:
            condition = condition & Q(tags__in=tags)

    playbooks = await Playbook.filter(condition).annotate(
        versions=Count("playbook_versions")
    ).order_by("-utime").offset(offset).limit(limit).prefetch_related("scenes", "tags")

    total = await Playbook.filter(condition).count()

    data = [{
        **p.json,
        "versions": p.versions  # 直接使用 annotate 的结果
    } for p in playbooks]

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": data
    })


@workflow_bp.post("/versions")
@requires_permission("workflow:r")
@playbook_permission("r")
async def list_workflow_versions(request):
    """
    获取剧本版本列表

    请求参数：
        id: 必填，剧本id
    """
    pv = await PlaybookVersion.filter(playbook__id=request.json.get("id"), deleted=False).all().prefetch_related(
        "playbook", "creator", "updator")
    return response_ok([p.json for p in pv])


# @workflow_bp.post("/detail")
# @requires_permission("workflow:r")
# async def detail_workflow(request):
#     """
#     获取剧本详情
#
#     请求参数：
#         id: 必填，剧本特定版本的ID
#     """
#     playbook = await PlaybookVersion.get(id=request.json.get("id")).prefetch_related("creator", "updator", "playbook")
#     if not playbook:
#         raise CustomError(ErrorCode.PlaybookNotFound)
#     return response_ok(playbook.json)


@workflow_bp.post("/edit")
@requires_permission("workflow:u")
@playbook_permission("u")
async def edit_workflow(request):
    """
    修改剧本的基本信息

    请求参数：
        id: 必填，剧本ID
        name: 可选，剧本名
        remark: 可选，备注
        scenes: 可选，关联场景名称，列表格式
        tags: 可选，标签名称，列表格式
    """
    params = request.json
    playbook = await Playbook.get(id=params.get("id")).prefetch_related("scenes", "tags")
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)

    playbook.name = params.get("name")
    playbook.remark = params.get("remark", '')
    if params.get("scenes"):
        scenes = await Scene.filter(name__in=params.get("scenes"))
        await playbook.scenes.clear()
        await playbook.scenes.add(*scenes)
    if params.get("tags"):
        tags = await Tag.filter(name__in=params.get("tags"))
        await playbook.tags.clear()
        await playbook.tags.add(*tags)
    playbook.updator = request.ctx.session['account_info']
    await playbook.save()
    return response_ok(playbook.json)


@workflow_bp.post("/edit-flow")
@requires_permission("workflow:u")
@playbook_version_permission("u")
async def edit_workflow_flow(request):
    """
    修改剧本流程图内容,修改已发布的剧本会自动生成一个新的版本

    请求参数:
        id: 必填，剧本版本ID
        flow_json: 必填，流程图布局信息
    """
    if not request.json.get("id") or not request.json.get("flow_json"):
        raise CustomError(ErrorCode.ParameterError)

    pv = await PlaybookVersion.get(id=request.json.get("id")).prefetch_related("playbook", "creator", "updator")
    old_data = pv.flow_json
    pv.flow_json = request.json.get("flow_json")
    pv.updator = request.ctx.session['account_info']
    # 已发布的剧本是不允许修改的
    if pv.status == 1:
        pv = await PlaybookVersion.create(playbook=pv.playbook, version=gen_order_version(), flow_json=pv.flow_json,
                                          change_desc=None, status=0, creator=request.ctx.session['account_info'],
                                          updator=request.ctx.session['account_info'])
    else:
        await pv.save()

    if old_data:
        action_ids = await statistics_data(old_data)
        await tool_action_playbook_delete({pv.id: action_ids})

    action_ids = await statistics_data(pv.flow_json)
    await tool_action_update({pv.id: action_ids})

    return response_ok(pv.json)


@workflow_bp.post("/publish")
@requires_permission("workflow:u")
@playbook_version_permission("u")
async def publish_workflow(request):
    """
    发布剧本

    请求参数：
        id: 必填，剧本版本ID
        change_desc: 必填, 版本变更说明
    """
    if not request.json.get("id") or not request.json.get("change_desc"):
        raise CustomError(ErrorCode.ParameterError)
    await PlaybookVersion.filter(id=request.json.get("id"), status=0).update(status=1, updator=request.ctx.session[
        'account_info'], version=gen_order_version(), change_desc=request.json.get("change_desc"))
    return response_ok("剧本发布成功")


@workflow_bp.post("/del")
@requires_permission("workflow:d")
@playbook_permission("d")
async def delete_workflow(request):
    """
    删除剧本

    请求参数：
        id: 剧本ID
    """
    playbook = await Playbook.get(id=request.json.get("id"), internal=False)
    pvs = await PlaybookVersion.filter(playbook=playbook, deleted=False).all()
    pv_ids = [pv.id for pv in pvs]
    pv_versions = [pv.version for pv in pvs]
    condition = Q()
    for pv_id in pv_ids:
        condition |= Q(script_id__contains=[pv_id])

    if await EventRelatedScript.filter(condition).exists():
        raise CustomError(ErrorCode.PlaybookUsedByEvent)
    if await Timed.filter(playbook=playbook, playbook_version__in=pv_versions).exists():
        raise CustomError(ErrorCode.PlaybookUsedByTimer)

    await playbook.delete()

    return response_ok("剧本删除成功")


@workflow_bp.post("/del-version")
@requires_permission("workflow:d")
@playbook_version_permission("d")
async def delete_workflow_version(request):
    """
    删除剧本版本

    请求参数：
        id: 剧本版本ID
    """
    pv = await PlaybookVersion.get(id=request.json.get("id"))
    if await EventRelatedScript.filter(script_id__contains=[pv.id]).exists():
        raise CustomError(ErrorCode.PlaybookUsedByEvent)
    if await Timed.filter(playbook_id=pv.playbook_id, playbook_version=pv.version).exists():
        raise CustomError(ErrorCode.PlaybookUsedByTimer)

    if pv.flow_json:
        action_ids = await statistics_data(pv.flow_json)
        await tool_action_playbook_delete({pv.id: action_ids})
    await pv.delete()
    return response_ok("删除成功")


@workflow_bp.post("/debug")
@requires_permission("workflow:r")
async def execute_action(request):
    """
        执行接口

        请求参数：
            - playbook_input dict 剧本输入参数 当type为flow时必填      用websocket发送
            示例如下：
            {
                "type": "flow",
                "flow_id": "73ecfba0-1aa4-4128-b99a-b97fb08a14be",      剧本id
                "flow_input": {
                    "l":"123456789",        剧本输入参数
                    "c":"123456789"         剧本输入参数
                }
            }
            - action_input dict 动作输入参数 当type为action时必填      用websocket发送
            示例如下：
            {
                "type": "action",
                "app_name":"huorong_edr",         应用名称，英文标识
                "name": "中文名称",               应用中文名称
                "app_ver":"v1746495758839",      应用版本号
                "main":"huorong_edr.py",          主程序文件名  获取工具版本时会传
                "action_input":{
                    "clients":"abcd",             动作输入参数
                    "groups":"123456789"          动作输入参数
                },
                "resource": "abc",               资源名称
                "node_name":"自定义扫描",    节点名称，定时任务模块中取动作描述
                "action":"custom_scan",     动作名称 英文标识
                "action_name":"自定义扫描",    动作名称 中文名称
                "retry_times":1,             重试次数
                "timeout":300,               超时时间,单位秒
            }
    """
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
    beijing_tz = pytz.timezone("Asia/Shanghai")
    utime = datetime.datetime.now(beijing_tz)
    message = request.json
    if message.get("type") in ('flow', 'action'):
        if message.get("type") == "flow":
            pv = await PlaybookVersion.get_or_none(id=message.get("flow_id"))
            playbook_id = pv.playbook_id
            has_access = await check_playbook_permission(request=request, user=request.ctx.session['account_info'],
                                                         playbook_id=playbook_id, required_permissions=["e"])
            if not has_access:
                await resp.send(f"data: 缺少剧本执行权限，执行失败\n\n")
                await resp.eof()
                return
        run_id = gen_order_version(length=6)
        message["executor"] = request.ctx.session['account_info'].id
        msg = {"run_type": message['type'], "run_id": run_id, "run_args": message}
        await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)
        sent_id = []
        for i in range(60):
            logs = await Runlog.filter(run_id=run_id, utime__gte=utime).all()
            if logs:
                utime = logs[0].utime
                for log in logs:
                    if log.id not in sent_id:
                        sent_id.append(log.id)
                        await resp.send(f"data: {orjson.dumps(log.json).decode()}\n\n")
                if any(log.node_type == "end-node" for log in logs):
                    break
            await asyncio.sleep(1)

    if resp:
        await resp.eof()

    return resp


@workflow_bp.post('/run/history')
@requires_permission("workflow:r")
@playbook_permission("r")
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

    run_logs = await Runlog.filter(run_id__in=run_ids_query).order_by("-utime").prefetch_related("flow")

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
@playbook_version_permission("r")
async def input_data(request):
    """
    获取指定剧本的输入参数

    请求参数：
        id: 剧本版本ID
    """
    input_list = await get_playbook_input(request, request.json.get("id"))

    return response_ok(input_list)


async def get_playbook_input(request, playbook_id):
    playbook = await PlaybookVersion.get_or_none(id=playbook_id)
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)

    input_list = []

    if playbook.flow_json:
        nodes = playbook.flow_json.get("nodes", [])
        if not nodes or "properties" not in nodes[0] or "start_params" not in nodes[0]["properties"]:
            return input_list

        input_id = nodes[0]["id"]
        start_params = nodes[0]["properties"]["start_params"]

        param_map = {param["name"]: {"type": param["type"], "description": param["desc"]} for param in start_params}

        pattern = rf"{re.escape(input_id)}\.output\.([a-zA-Z0-9_]+)(?=[\)}}])"

        matches = re.findall(pattern, str(playbook.flow_json))
        unique_matches = list(set(matches))  # 去重
        for param_key in unique_matches:
            if param_key in param_map:
                input_list.append({
                    param_key: {
                        "input": param_map[param_key]
                    }
                })

    return input_list


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


@workflow_bp.post("/tag/new")
@requires_permission("workflow:c")
async def new_tag(request):
    """
    新建标签（需要剧本新增权限）

    请求参数：
        - name: 标签名称    str
    """
    name = request.json.get("name", "").strip()
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if await Tag.get_or_none(name=name, type=0):
        raise CustomError(ErrorCode.TagExistError)

    await Tag.create(name=name, type=0)

    return response_ok("标签新建成功")


@workflow_bp.post("/tag/list")
@requires_permission("workflow:r")
async def tag_list(request):
    """
    获取标签列表（需要剧本查看权限）

    请求参数：
        无
    """
    tags = await Tag.filter(type=0).all()
    return response_ok([tag.name for tag in tags])


@workflow_bp.post("/tag/remove")
@requires_permission("workflow:d")
async def remove_tag(request):
    """
    删除标签（需要剧本删除权限）

    请求参数：
        - name: 标签名称    str
    """
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    tag = await Tag.get(name=name, type=0)
    await tag.delete()
    return response_ok("标签删除成功")


@workflow_bp.post("/permissions/add")
@requires_permission("workflow:u")
@playbook_permission("p")
async def add_permission(request):
    """
    新增剧本权限（需要剧本更新权限）
        (r:查, u:改, d:删, e:执行, p:授权)
    query参数:
        - playbook_id: 剧本id str
        - users : 用户权限 {"id":["r","u","d","e","p"]}
        - roles : 角色权限 {"id":["r","u","d","e","p"]}
    """
    playbook_id = request.json.get("playbook_id")
    users = request.json.get("users", [])
    roles = request.json.get("roles", [])

    if not playbook_id:
        raise CustomError(ErrorCode.ParameterError)

    # 处理用户权限
    for uid, perms in users.items():
        await PlaybookPermissions.update_or_create(
            pb_id=playbook_id,
            users_id=uid,
            defaults={"permissions": perms, "roles_id": None}
        )
        await request.ctx.cache_client.set(
            (playbook_id, uid),
            perms,
            ttl=2592000
        )

    # 处理角色权限
    for rid, perms in roles.items():
        if rid != "111111":
            await PlaybookPermissions.update_or_create(
                pb_id=playbook_id,
                roles_id=rid,
                defaults={"permissions": perms, "users_id": None}
            )
        await request.ctx.cache_client.set(
            (playbook_id, rid),
            perms,
            ttl=2592000
        )

    return response_ok("权限更新成功")


@workflow_bp.post("/permissions/select")
@requires_permission("workflow:r")
@playbook_permission("r")
async def select_permission_all(request):
    """
    获取剧本权限（需要剧本查看权限）

    query参数:
        - playbook_id: 剧本id    str
    """
    playbook_id = request.json.get("playbook_id")
    if not playbook_id:
        raise CustomError(ErrorCode.ParameterError)

    pps = await PlaybookPermissions.filter(pb_id=playbook_id).all().prefetch_related("users", "roles")

    return response_ok([pp.json for pp in pps])
