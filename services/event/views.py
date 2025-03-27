# coding=utf-8

from sanic import Blueprint
from tortoise.transactions import in_transaction

from core.response import response_ok
from libs.security.authorization import requires_permission
from models import Events

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

    events = await Events.filter(**filters).offset(offset).limit(limit).values()
    total = await Events.filter(**filters).count()

    return response_ok({
        "events": events,
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
        event_process_status: str 事件处理状态
        event_source: str 事件来源
        event_level: str 事件级别
        owner: list 负责人
        created_id: str 创建人ID
        event_summary: str 事件概要
        event_ingestion_id: str 事件接入id
        log_data: s 日志数据
    """
    event_name = request.json.get("event_name")
    event_process_status = request.json.get("event_process_status")
    event_source = request.json.get("event_source")
    event_level = request.json.get("event_level")
    owner = request.json.get("owner")
    created_id = request.json.get("created_id")
    event_summary = request.json.get("event_summary")
    event_ingestion_id = request.json.get("event_ingestion_id")
    log_data = request.json.get("log_data")

    async with in_transaction():
        role = await Events.create(event_name=event_name, event_process_status=event_process_status,
                                   event_source=event_source, event_level=event_level, owner=owner,
                                   created_id=created_id, event_summary=event_summary, event_ingestion_id=event_ingestion_id,log_data=log_data)

    return response_ok(role.json)
