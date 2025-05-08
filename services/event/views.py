# coding=utf-8

from sanic import Blueprint
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Events, Runlog

event_bp = Blueprint('event', url_prefix='v1/event')
_all__ = ["event_bp"]


@event_bp.post('/list')
@requires_permission('event:r')
async def get_all_events(request):
    """
    获取指定页数的事件（支持条件筛选）(需要事件查询权限)

    请求参数：
        page: int 页数
        size: int 每页大小
        event_name: str 事件名称（支持模糊搜索）
        event_process_status: str 事件处理状态（精确匹配）
        event_source: str 事件来源（精确匹配）
        event_level: str 事件级别（精确匹配）
    """

    page = int(request.json.get('page', 1))
    size = int(request.json.get('size', 10))
    offset = (page - 1) * size
    limit = size

    filters = {}
    if request.json.get("event_name"):
        filters["event_name__icontains"] = request.json.get("event_name")
    if request.json.get("event_process_status"):
        filters["event_process_status"] = request.json.get("event_process_status")
    if request.json.get("event_source"):
        filters["event_source"] = request.json.get("event_source")
    if request.json.get("event_level"):
        filters["event_level"] = request.json.get("event_level")

    events = await Events.filter(**filters).offset(offset).limit(limit).all()
    total = await Events.filter(**filters).count()

    return response_ok({
        "events": [event.json for event in events],
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size
    })


@event_bp.post('/create')
@requires_permission('event:c')
async def create_event(request):
    """
    创建事件（需要事件创建权限）

    请求参数：
        event_name: str 事件名称
        event_source: str 事件来源   (人工创建时默认传人工)
        event_level: str 事件级别
        owner: list 负责人
        event_summary: str 事件概要 (非必填)
        event_ingestion_id: str 事件接入id   (人工创建时不传此参数)
        log_data: str 日志数据 (非必填)
    """
    event_name = request.json.get("event_name")
    event_process_status = "待处理"
    event_source = request.json.get("event_source")
    event_level = request.json.get("event_level")
    owner = (request.json.get("owner") or [])
    user = request.ctx.session['account_info']
    created_id = user.id
    event_summary = (request.json.get("event_summary") or "")
    event_ingestion_id = (request.json.get("event_ingestion_id") or "")
    log_data = (request.json.get("log_data") or "")

    async with in_transaction():
        role = await Events.create(event_name=event_name, event_process_status=event_process_status,
                                   event_source=event_source, event_level=event_level, owner=owner,
                                   created_id=created_id, event_summary=event_summary,
                                   event_ingestion_id=event_ingestion_id, log_data=log_data)

    return response_ok(role.json)


@event_bp.post('/detail')
@requires_permission('event:r')
async def get_event_detail(request):
    """
    获取事件详情（需要事件查询权限）

    请求参数：
        id: str 事件id
    """
    event_id = request.json.get("id")

    event = await Events.get(id=event_id, deleted=False)
    if not event:
        raise CustomError(ErrorCode.NotFound)

    event_dict = event.json
    event_dict["created_name"] = request.ctx.session['account_info'].username

    event_dict["run_logs"] = []
    for run_id in event_dict["run_id"]:
        run_logs = await Runlog.filter(run_id=run_id, deleted=False).all().prefetch_related("playbook")
        event_dict["run_logs"].append([runlog.json for runlog in run_logs])

    return response_ok(event_dict)


@event_bp.post('/bind')
@requires_permission('event:u')
async def bind_event_to_run(request):
    """
    绑定运行记录到事件（需要事件更新权限）

    请求参数：
        event_id: str 事件id
        run_id: str 运行记录id
    """
    event_id = request.json.get("event_id")
    run_id = request.json.get("run_id")

    event = await Events.get(id=event_id, deleted=False)
    if not event:
        raise CustomError(ErrorCode.NotFound)

    if not isinstance(event.run_id, list):
        event.run_id = []

    if run_id not in event.run_id:
        event.run_id.append(run_id)
        await event.save(update_fields=["run_id"])

    return response_ok("绑定成功")

