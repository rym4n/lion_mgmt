# coding=utf-8
from datetime import datetime
import json

from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.embedding import get_embedding
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Account, Room
from models.agent import ArchivalKnowledge, UserPreference
from services.agent.gateway import AgentGateway
from settings import TZ

secops_bp = Blueprint("secops_expert", url_prefix="/secops-expert")
__all__ = ["secops_bp"]


async def _build_user_info(user: Account, tenant_id: str) -> dict:
    """构建用户信息 (用于 Agent 识别)"""
    roles = await user.roles.filter(tenant_id=tenant_id).all()
    role_names = [r.name for r in roles]

    # 聚合权限
    permissions = set()
    for role in roles:
        if role.permissions:
            permissions.update(role.permissions)

    # 获取用户偏好
    pref = await UserPreference.get_or_none(account_id=user.id)

    return {
        "id": user.id,
        "display_name": user.display_name,
        "role": role_names[0] if role_names else "User",
        "permissions": list(permissions),
        "skills": pref.skill_tags if pref else [],
        "preference": {"notify": pref.notify_channel if pref else "in_app", "report_lang": pref.report_language if pref else "zh-CN"} if pref else {},
    }


async def _get_warroom_members(warroom: Room, tenant_id: str) -> list[dict]:
    """获取作战室成员列表"""
    members = await warroom.users.all()
    result = []
    for member in members:
        result.append(await _build_user_info(member, tenant_id))
    return result


async def _generate_embedding(text: str) -> list[float]:
    """生成文本 Embedding"""
    return await get_embedding(text)


# ============ 对话接口 ============


@secops_bp.post("/chat")
@openapi.definition(
    summary="与 SecOpsExpert 对话",
    description="在作战室中与 SecOpsExpert 智能体对话",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "warroom_id": {"type": "string", "description": "作战室 ID"},
                    "content": {"type": "string", "description": "消息内容"},
                },
                "required": ["warroom_id", "content"],
            }
        }
    },
)
@requires_permission("warroom:r")
async def chat(request):
    """
    发送消息到 SecOpsExpert
    返回: Agent 回复 + 更新后的 L1 状态
    """
    warroom_id = request.json.get("warroom_id")
    content = request.json.get("content")

    if not warroom_id or not content:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    user = request.ctx.session["account_info"]

    # 获取用户角色和权限
    user_info = await _build_user_info(user, tenant_id)

    gateway: AgentGateway = request.app.ctx.agent_gateway

    # 确保 Agent 存在
    warroom = await Room.get_or_none(id=warroom_id, tenant_id=tenant_id, deleted=False)
    if not warroom:
        raise CustomError(ErrorCode.NOT_FOUND)

    members = await _get_warroom_members(warroom, tenant_id)
    await gateway.get_or_create_agent(warroom_id, tenant_id, members)

    # 发送消息
    result = await gateway.send_message(warroom_id=warroom_id, user=user_info, content=content)

    return response_ok(result)


@secops_bp.post("/chat/stream")
@openapi.definition(summary="与 SecOpsExpert 对话 (流式)", description="流式返回 Agent 回复，适合长回复场景")
@requires_permission("warroom:r")
async def chat_stream(request):
    """
    流式对话接口
    返回格式: SSE (Server-Sent Events)
    """
    warroom_id = request.json.get("warroom_id")
    content = request.json.get("content")

    if not warroom_id or not content:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    user = request.ctx.session["account_info"]
    user_info = await _build_user_info(user, tenant_id)

    gateway: AgentGateway = request.app.ctx.agent_gateway

    # 确保 Agent 存在
    warroom = await Room.get_or_none(id=warroom_id, tenant_id=tenant_id, deleted=False)
    if not warroom:
        raise CustomError(ErrorCode.NOT_FOUND)

    members = await _get_warroom_members(warroom, tenant_id)
    await gateway.get_or_create_agent(warroom_id, tenant_id, members)

    # 创建 SSE 响应
    response = await request.respond(content_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})  # 禁用 Nginx 缓冲

    try:
        # 流式发送消息
        async for chunk in gateway.send_message_stream(warroom_id=warroom_id, user=user_info, content=content):
            event_type = chunk["type"]
            data = chunk["data"]

            # SSE 格式
            await response.send(f"event: {event_type}\n")
            await response.send(f"data: {json.dumps(data, ensure_ascii=False)}\n\n")

        # 发送结束事件
        await response.send("event: done\ndata: {}\n\n")

    except Exception as e:
        logger.error(f"Stream error: {e}")
        await response.send(f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n")

    return response


@secops_bp.post("/state")
@openapi.definition(summary="获取 Agent L1 状态", description="获取当前 Agent 的 Core Memory 状态 (用于前端渲染)")
@requires_permission("warroom:r")
async def get_state(request):
    """
    获取 Agent 当前 L1 状态 (不发送消息)
    用于: 前端轮询刷新、页面初始化
    """
    warroom_id = request.json.get("warroom_id")
    # tenant_id = request.ctx.session.get("tenant_id") # Unused

    gateway: AgentGateway = request.app.ctx.agent_gateway

    agent_id = await gateway._get_agent_id(warroom_id)
    if not agent_id:
        return response_ok({"status": "no_agent", "l1_state": None})

    l1_state = await gateway.get_agent_l1_state(agent_id)

    return response_ok({"status": "active", "l1_state": l1_state})


# ============ 生命周期接口 ============


@secops_bp.post("/init")
@openapi.definition(summary="初始化作战室 Agent", description="手动初始化作战室的 SecOpsExpert Agent (通常自动触发)")
@requires_permission("warroom:c")
async def init_agent(request):
    """
    手动初始化 Agent (通常由 Warroom 创建时自动触发)
    """
    warroom_id = request.json.get("warroom_id")
    tenant_id = request.ctx.session.get("tenant_id")

    warroom = await Room.get_or_none(id=warroom_id, tenant_id=tenant_id, deleted=False)
    if not warroom:
        raise CustomError(ErrorCode.NOT_FOUND)

    gateway: AgentGateway = request.app.ctx.agent_gateway
    members = await _get_warroom_members(warroom, tenant_id)

    agent_id = await gateway.get_or_create_agent(warroom_id, tenant_id, members)

    return response_ok({"agent_id": agent_id, "status": "initialized"})


@secops_bp.post("/archive")
@openapi.definition(summary="归档作战室 Agent", description="触发 Agent 反思并归档 (通常由作战室归档时自动触发)")
@requires_permission("warroom:u")
async def archive_agent(request):
    """
    归档 Agent - 触发反思 + 销毁
    """
    warroom_id = request.json.get("warroom_id")
    skip_reflection = request.json.get("skip_reflection", False)

    gateway: AgentGateway = request.app.ctx.agent_gateway

    result = await gateway.archive_agent(warroom_id=warroom_id, trigger_reflection=not skip_reflection)

    return response_ok(result)


# ============ 知识库管理接口 ============


@secops_bp.post("/knowledge/list")
@openapi.definition(summary="查询 L5 知识库", description="分页查询租户的知识库内容")
@requires_permission("secops:r")
async def list_knowledge(request):
    """查询知识库 (管理用途)"""
    tenant_id = request.ctx.session.get("tenant_id")
    page = request.json.get("page", 1)
    size = request.json.get("size", 20)
    knowledge_type = request.json.get("knowledge_type")
    review_status = request.json.get("review_status")

    filters = {"tenant_id": tenant_id, "deleted": False}
    if knowledge_type:
        filters["knowledge_type"] = knowledge_type
    if review_status:
        filters["review_status"] = review_status

    offset = (page - 1) * size
    items = await ArchivalKnowledge.filter(**filters).offset(offset).limit(size).all()
    total = await ArchivalKnowledge.filter(**filters).count()

    # Simple JSON serialization for items
    data = []
    for item in items:
        # Avoid including large embedding vector in list view if not needed
        item_dict = {
            "id": item.id,
            "content": item.content[:200] + "..." if len(item.content) > 200 else item.content,
            "knowledge_type": item.knowledge_type,
            "attack_type": item.attack_type,
            "tags": item.tags,
            "review_status": item.review_status,
            "ctime": str(item.ctime),
        }
        data.append(item_dict)

    return response_ok({"data": data, "total": total, "page": page, "size": size})


@secops_bp.post("/knowledge/review")
@openapi.definition(summary="审核知识条目", description="审核 Agent 提交的案例学习")
@requires_permission("secops:u")
async def review_knowledge(request):
    """审核知识条目"""
    knowledge_id = request.json.get("id")
    action = request.json.get("action")  # approve / reject

    if action not in ("approve", "reject"):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    user = request.ctx.session["account_info"]

    knowledge = await ArchivalKnowledge.get_or_none(id=knowledge_id, tenant_id=tenant_id, deleted=False)
    if not knowledge:
        raise CustomError(ErrorCode.NOT_FOUND)

    knowledge.review_status = "approved" if action == "approve" else "rejected"
    knowledge.reviewed_by = user
    knowledge.reviewed_at = datetime.now(TZ)
    await knowledge.save()

    return response_ok({"status": knowledge.review_status})


@secops_bp.post("/knowledge/upload")
@openapi.definition(summary="上传 SOP 文档", description="管理员上传 SOP/知识文档到 L5")
@requires_permission("secops:c")
async def upload_knowledge(request):
    """上传 SOP 文档"""
    content = request.json.get("content")
    knowledge_type = request.json.get("knowledge_type", "sop")
    tags = request.json.get("tags", [])

    if not content:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    user = request.ctx.session["account_info"]

    # 生成 Embedding
    embedding = await _generate_embedding(content)

    knowledge = await ArchivalKnowledge.create(
        tenant_id=tenant_id, content=content, embedding=embedding, knowledge_type=knowledge_type, tags=tags, submitted_by=user.id, review_status="approved"  # 管理员上传直接通过
    )

    return response_ok({"id": knowledge.id})


# ============ 用户偏好接口 ============


@secops_bp.post("/preference/get")
@requires_permission("user:r")
async def get_preference(request):
    """获取当前用户的 SecOps 偏好"""
    user = request.ctx.session["account_info"]

    pref = await UserPreference.get_or_none(account_id=user.id)
    if not pref:
        return response_ok(None)

    # Simple serialization
    return response_ok(
        {
            "notify_channel": pref.notify_channel,
            "notify_config": pref.notify_config,
            "report_language": pref.report_language,
            "response_verbosity": pref.response_verbosity,
            "skill_tags": pref.skill_tags,
        }
    )


@secops_bp.post("/preference/update")
@requires_permission("user:u")
async def update_preference(request):
    """更新用户偏好"""
    user = request.ctx.session["account_info"]
    tenant_id = request.ctx.session.get("tenant_id")

    pref, created = await UserPreference.get_or_create(account_id=user.id, defaults={"tenant_id": tenant_id})

    update_fields = []
    for field in ["notify_channel", "notify_config", "report_language", "response_verbosity", "skill_tags"]:
        if field in request.json:
            setattr(pref, field, request.json[field])
            update_fields.append(field)

    if update_fields:
        await pref.save(update_fields=update_fields)

    return response_ok(
        {
            "notify_channel": pref.notify_channel,
            "notify_config": pref.notify_config,
            "report_language": pref.report_language,
            "response_verbosity": pref.response_verbosity,
            "skill_tags": pref.skill_tags,
        }
    )
