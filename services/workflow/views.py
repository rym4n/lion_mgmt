#!/usr/bin/env python
# coding=utf-8
import ujson
from sanic import Blueprint, Request, Websocket
from sanic.log import logger
from tortoise.expressions import Q
from websockets.exceptions import ConnectionClosed

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Playbook, Scene

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
        start_app: 开始节点，可选
        end_app: 结束节点，可选
        if_list: 判断节点，可选
        audit_list: 审批节点，可选
        tags: 标签，可选
        flow_json: 流程图布局信息，可选
        flow_data: 各节点详细信息，可选
        scenes: 场景，列表格式，可选
    """
    playbook = await Playbook.create(
        name=request.json.get("name"),
        remark=request.json.get("remark", ""),
        start_app=request.json.get("start_app"),
        end_app=request.json.get("end_app"),
        if_list=request.json.get("if_list"),
        audit_list=request.json.get("audit_list"),
        tags=request.json.get("tags"),
        flow_json=request.json.get("flow_json") or {},
        flow_data=request.json.get("flow_data") or {},
        creator=request.ctx.session['account_info'],
        updator=request.ctx.session['account_info']
    )
    if request.json.get('scenes'):
        scenes = await Scene.filter(name__in=request.json.get('scenes')).all()
        await playbook.scenes.add(*scenes)
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
    playbooks = await Playbook.filter(condition).offset(offset).limit(limit).all()
    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [playbook.json for playbook in playbooks]
    })


@workflow_bp.post("/edit")
@requires_permission("workflow:u")
async def edit_workflow(request):
    """
    修改剧本

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
        flow_data: 可选，各节点详细信息
    """
    playbook = await Playbook.get(id=request.json.get("id"))
    if not playbook:
        raise CustomError(ErrorCode.PlaybookNotFound)

    params = request.json
    need_update = False
    for p in params:
        if hasattr(playbook, p):
            need_update = True
            setattr(playbook, p, params[p])
    if need_update:
        playbook.updator = request.ctx.session['account_info']
        await playbook.save()
    return response_ok(playbook.json)


@workflow_bp.post("/del")
@requires_permission("workflow:d")
async def delete_workflow(request):
    """
    删除剧本

    请求参数）
        id: 剧本ID
    """
    await Playbook.filter(id=request.json.get("id")).update(deleted=True)
    return response_ok("剧本删除成功")


@workflow_bp.websocket('/run')
async def execute_action(request: Request, ws: Websocket):
    async for msg in ws:
        try:
            data = ujson.loads(msg)
            await ws.send(data.get('action', ''))
        except ConnectionClosed:
            logger.info("ws closed.")
            return
        except Exception as e:
            logger.error(e)
