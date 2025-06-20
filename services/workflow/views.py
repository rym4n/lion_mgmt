#!/usr/bin/env python
# coding=utf-8
import asyncio
import os
import time
from collections import defaultdict

import aiofile
import orjson
from sanic import Blueprint
from tortoise.expressions import Q
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission, load_license
from libs.utils import gen_order_version
from models import Playbook, Scene, ToolAction, Tool, Runlog, Tag, PlaybookVersion, PlaybookPermissions
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

    async with in_transaction():
        playbook = await Playbook.create(
            name=data.get("name"),
            remark=data.get("remark", "")
        )
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
        status: 可选，搜索时使用，支持搜索状态，0:草稿 1:已发布
        tag: 可选，搜索时使用，支持搜索标签 str
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
        if scenes:
            condition = condition & Q(scenes__in=scenes)
    status = request.json.get("status")
    if status:
        condition = condition & Q(status=status)
    if request.json.get("tag"):
        tags = await Tag.filter(name__in=request.json.get("tag")).all()
        if tags:
            condition = condition & Q(tags__in=tags)

    playbooks = await Playbook.filter(condition).annotate(
        versions=Count("playbook_versions")
    ).offset(offset).limit(limit).order_by("-utime").prefetch_related("scenes", "tags")

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
                                          change_desc=None, status=0,
                                          creator=request.ctx.session['account_info'],
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
@requires_permission("workflow:p")
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
        'account_info'],
                                                                             version=gen_order_version(),
                                                                             change_desc=request.json.get(
                                                                                 "change_desc"))
    return response_ok("剧本发布成功")


@workflow_bp.post("/del")
@requires_permission("workflow:d")
async def delete_workflow(request):
    """
    删除剧本

    请求参数：
        id: 剧本ID
    """
    playbook = await Playbook.get(id=request.json.get("id"), internal=False)
    playbook.deleted = True
    await playbook.save()
    return response_ok("剧本删除成功")


@workflow_bp.post("/del-version")
@requires_permission("workflow:d")
async def delete_workflow_version(request):
    """
    删除剧本版本

    请求参数：
        id: 剧本版本ID
    """
    pv = await PlaybookVersion.get(id=request.json.get("id"))
    await pv.delete()
    if pv.flow_json:
        action_ids = await statistics_data(pv.flow_json)
        await tool_action_playbook_delete({pv.id: action_ids})
    return response_ok("删除成功")


@workflow_bp.post("/run")
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

    message = request.json
    if message.get("type") in ('flow', 'action'):
        run_id = gen_order_version(length=6)
        message["executor"] = request.ctx.session['account_info'].id
        msg = {"run_type": message['type'], "run_id": run_id, "run_args": message}
        await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

        for i in range(60):
            logs = await Runlog.filter(run_id=run_id).all()
            if logs:
                await resp.send(log.json for log in logs)
                if any(log.node_type == "end-node" for log in logs):
                    break
            await asyncio.sleep(1)

    if resp:
        await resp.eof()

    return resp


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
        nodes = playbook.flow_json["nodes"]
        input_id = nodes[0]["id"]

        for node in nodes[1:]:
            if "input" not in node["properties"].keys():
                continue
            for key, value in node["properties"]["input"].items():
                if input_id in value:
                    data = await request.ctx.cache_client.get(
                        node["properties"]["app_name"] + "_" + node["properties"]["version"])
                    if data:
                        if isinstance(data, (str, bytes)):
                            data = orjson.loads(data)
                        for action in (data.get('actions') or []):
                            if node["properties"]["action"] in action.values():
                                for item in action["input"]:
                                    if key == item["key"]:
                                        action["input"] = item
                                        break
                                input_list.append({value.split(".output.")[-1].rstrip("})"): action})
                    else:
                        actions = await ToolAction.filter(tool_name=node["properties"]["app_name"],
                                                          version_number=node["properties"]["version"]).all()
                        for action in actions:
                            if node["properties"]["action"] == action.action["name"]:
                                for item in action.action["input"]:
                                    if key == item["key"]:
                                        action.action["input"] = item
                                        break
                                input_list.append({value.split(".output.")[-1].rstrip("})"): action.action})
                        tool = await Tool.filter(name=node["properties"]["app_name"],
                                                 version=node["properties"]["version"]).first()
                        if tool:
                            config_path = os.path.abspath(os.path.join(tool.path, f"config.json"))

                            async with aiofile.AIOFile(config_path, 'r', encoding='utf-8') as f:
                                content = await f.read()
                                # 将config数据存入redis
                                await request.ctx.cache_client.set(
                                    node["properties"]["app_name"] + "_" + node["properties"]["version"], content,
                                    ttl=2592000)
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
async def add_permission(request):
    """
    新增剧本权限（需要剧本更新权限）

    query参数:
        - playbook_id: 剧本id    str
        - users_id: 用户ID列表    list
        - roles_id: 角色ID列表    list
    """
    playbook_id = request.json.get("playbook_id")
    users_id = list(set(request.json.get("users_id", [])))
    roles_id = list(set(request.json.get("roles_id", [])))
    if not playbook_id:
        raise CustomError(ErrorCode.ParameterError)
    if not isinstance(users_id, list) or not isinstance(roles_id, list):
        raise CustomError(ErrorCode.ParameterError)

    await PlaybookPermissions.update_or_create(pb_id=playbook_id, defaults={'users_id': users_id, 'roles_id': roles_id})

    return response_ok("success")


@workflow_bp.post("/permissions/select")
@requires_permission("workflow:r")
async def select_permission_all(request):
    """
    获取剧本权限（需要剧本查看权限）

    query参数:
        - playbook_id: 剧本id    str
    """
    playbook_id = request.json.get("playbook_id")
    if not playbook_id:
        raise CustomError(ErrorCode.ParameterError)

    permission = await PlaybookPermissions.get_or_none(pb_id=playbook_id)

    return response_ok(permission.json if permission else {})


@workflow_bp.post("/permissions/judge")
@requires_permission("workflow:r")
async def judge_permission(request):
    """
    权限判断接口 (需要剧本查看权限)

    请求体参数:
        - playbook_id: 剧本id    str
    """
    playbook_id = request.json.get("playbook_id")
    if not playbook_id:
        raise CustomError(ErrorCode.ParameterError)

    user = request.ctx.session.get('account_info')
    if not user:
        raise CustomError(ErrorCode.AccountNotFound)

    user_id = str(user.id)
    role_ids = {str(role.id) for role in user.roles}

    permission = await PlaybookPermissions.get_or_none(pb_id=playbook_id)

    if not permission:
        return response_ok(False)

    if user_id in permission.users_id or role_ids.intersection(permission.roles_id) or permission.users_id == [
        "*"] or permission.roles_id == ["*"]:
        return response_ok(True)

    return response_ok(False)
