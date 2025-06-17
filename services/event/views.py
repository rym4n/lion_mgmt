# coding=utf-8
import time

from sanic import Blueprint
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import gen_random_code
from models import Events, Runlog, Room, Account, Role

event_bp = Blueprint('event', url_prefix='/event')
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

    events = await Events.filter(**filters).offset(offset).limit(limit).all().prefetch_related("room", "creator", "updator")
    total = await Events.filter(**filters).count()

    data = []

    for event in events:
        event_dict = {}
        role_list = []
        user_list = []
        if event.owner:
            role_ids = event.owner["role"]
            user_ids = event.owner["user"]

            role_objs = await Role.filter(id__in=role_ids)
            user_objs = await Account.filter(id__in=user_ids)

            role_list = [{r.id: r.name} for r in role_objs]
            user_list = [{u.id: {"display_name": u.display_name, "username": u.username}} for u in user_objs]

        event_dict.update(event.json)

        event_dict["owner"] = {"role": role_list , "user": user_list}

        data.append(event_dict)

    return response_ok({
        "events": data,
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
        owner: list 负责人 示例：
        {
            "role":["root"],
            "user":["test"]
        }
        event_summary: str 事件概要 (非必填)
        event_ingestion_id: str 事件接入id   (人工创建时不传此参数)
        log_data: str 日志数据 (非必填)    (人工创建时不传此参数)
    """
    event_name = request.json.get("event_name") + "_" + str(int(time.time() * 1000))
    event_process_status = "待处理"
    event_source = request.json.get("event_source")
    owner = (request.json.get("owner") or {})
    user = request.ctx.session['account_info']
    event_summary = (request.json.get("event_summary") or "")
    event_ingestion_id = (request.json.get("event_ingestion_id") or "")
    log_data = (request.json.get("log_data") or "")

    if await Events.filter(event_name=event_name, deleted=False).exists():
        raise CustomError(ErrorCode.EventNameExists)

    async with in_transaction():
        await Events.create(event_name=event_name, event_process_status=event_process_status,
                                   event_source=event_source, owner=owner,
                                   creator=user, event_summary=event_summary, updator=user,
                                   event_ingestion_id=event_ingestion_id, log_data=log_data)

    return response_ok("创建成功")


@event_bp.post('/detail')
@requires_permission('event:r')
async def get_event_detail(request):
    """
    获取事件详情（需要事件查询权限）

    请求参数：
        id: str 事件id
    """
    event_id = request.json.get("id")

    event = await Events.get(id=event_id, deleted=False).prefetch_related("room", "creator", "updator")
    if not event:
        raise CustomError(ErrorCode.NotFound)

    if not event.room:
        room_name = event.event_name + "_" + gen_random_code(6)
        if await Room.filter(name=room_name, deleted=False).exists():
            raise CustomError(ErrorCode.RoomNameExists)
        creator = request.ctx.session['account_info']
        room = await Room.create(name=room_name, creator=creator)
        event.room = room
        await event.save()
    user = await Account.get_or_none(id=creator.id)
    if user:
        await user.star_rooms.add(event.room)
    await event.room.users.add(creator)

    event_dict = {"created_name": request.ctx.session['account_info'].username}

    run_logs = await Runlog.filter(event_id=event_id, deleted=False).all().prefetch_related("flow")
    run_logs_grouped = {}
    for runlog in run_logs:
        run_logs_grouped.setdefault(runlog.run_id, []).append(runlog.json)
    event_dict["run_logs"] = run_logs_grouped

    event_dict["room_users"] = [user.username if user else "" for user in await event.room.users.all()]
    role_list = []
    user_list = []
    if event.owner:
        role_ids = event.owner["role"]
        user_ids = event.owner["user"]

        role_objs = await Role.filter(id__in=role_ids)
        user_objs = await Account.filter(id__in=user_ids)

        role_list = [{r.id: r.name} for r in role_objs]
        user_list = [{u.id: {"display_name": u.display_name, "username": u.username}} for u in user_objs]

    event_dict.update(event.json)

    event_dict["owner"] = {"role": role_list, "user": user_list}

    return response_ok(event_dict)


@event_bp.post('/update')
@requires_permission('event:u')
async def update_event(request):
    """
    更新事件（需要事件更新权限）

    请求参数：
        id: str 事件id
        event_name: str 事件名称    (非必填)
        owner: list 负责人          (非必填)
        示例：
        {
            "role":["root"],
            "user":["test"]
        }
        event_summary: str 事件概要 (非必填)
        event_process_status: str 事件处理状态 (非必填)
        room_user_id: str 作战室新增用户id (非必填)
    """
    params = request.json
    event_id = params.get("id")
    if not event_id:
        raise CustomError(ErrorCode.ParameterError)

    event = await Events.get(id=event_id, deleted=False).prefetch_related("room", "creator", "updator")
    if not event:
        raise CustomError(ErrorCode.NotFound)

    update_fields = []

    if name := params.get("event_name"):
        event.event_name = name
        update_fields.append("event_name")

    if summary := params.get("event_summary"):
        event.event_summary = summary
        update_fields.append("event_summary")

    if status := params.get("event_process_status"):
        event.event_process_status = status
        update_fields.append("event_process_status")

    if room_user_id := params.get("room_user_id"):
        is_member = await event.room.users.filter(id=room_user_id).exists()
        if not is_member:
            user = await Account.get_or_none(id=room_user_id)
            if user:
                await event.room.users.add(user)

    new_owner = params.get("owner")
    if new_owner is not None:
        if not isinstance(event.owner, dict) or not event.owner:
            event.owner = {"role": [], "user": []}
        owner_data = event.owner
        for o in new_owner.get("user", []):
            if o not in owner_data.get("user", []):
                owner_data.get("user", []).append(o)
        for o in new_owner.get("role", []):
            if o not in owner_data.get("role", []):
                owner_data.get("role", []).append(o)
        update_fields.append("owner")

    if update_fields:
        await event.save(update_fields=update_fields)

    return response_ok("更新成功")
