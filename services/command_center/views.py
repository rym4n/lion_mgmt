# coding=utf-8
from datetime import datetime, time

from sanic import Blueprint, Request
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.command_center import COMMAND_APPROVAL_STATUS_PENDING, CommandAgent, CommandApproval, CommandTask, CommandTaskEvent, CommandTaskRun
from settings import TZ

from .approval_service import decide_approval, get_approval, list_approvals, resume_approved_approval
from .manager import TERMINAL_TASK_STATUS, create_command_task, dispatch_task, has_task_content, list_tasks, update_task_progress
from .summary import aggregate_usage, build_task_summary, humanize_event, normalize_usage, zero_usage

command_bp = Blueprint("command_center", url_prefix="/command_center")
__all__ = ["command_bp"]

ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "integer", "description": "错误码"},
        "message": {"type": "string", "description": "错误信息"},
        "data": {"type": "object", "nullable": True, "description": "错误详情"},
    },
}

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "智能体ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "agent_key": {"type": "string", "description": "智能体逻辑标识"},
        "name": {"type": "string", "description": "智能体名称"},
        "description": {"type": "string", "nullable": True, "description": "智能体说明"},
        "capabilities": {
            "type": "array",
            "description": "能力标签列表，用于任务调度匹配",
            "items": {"type": "string"},
        },
        "status": {
            "type": "string",
            "description": "智能体状态",
            "enum": ["idle", "busy", "offline", "error"],
        },
        "load": {"type": "integer", "description": "当前任务负载"},
        "max_concurrent": {"type": "integer", "description": "最大并发任务数"},
        "current_task_id": {"type": "string", "nullable": True, "description": "当前主任务ID"},
        "heartbeat_at": {"type": "string", "nullable": True, "description": "最近心跳时间"},
        "metadata": {"type": "object", "description": "扩展元数据"},
        "runtime": {"type": "string", "description": "运行时类型，默认lion_agent"},
        "prompt_config": {"type": "object", "description": "提示词扩展配置"},
        "is_builtin": {"type": "boolean", "description": "是否为内置智能体"},
    },
}

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "任务ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "title": {"type": "string", "description": "任务标题"},
        "description": {"type": "string", "nullable": True, "description": "任务描述"},
        "source": {"type": "string", "description": "任务来源"},
        "priority": {"type": "integer", "description": "优先级，数值越小优先级越高"},
        "status": {
            "type": "string",
            "description": "任务状态",
            "enum": ["pending_dispatch", "dispatched", "running", "completed", "failed", "cancelled"],
        },
        "progress": {"type": "integer", "description": "任务进度，范围0-100", "minimum": 0, "maximum": 100},
        "required_capabilities": {
            "type": "array",
            "description": "任务所需能力标签",
            "items": {"type": "string"},
        },
        "payload": {"type": "object", "description": "任务载荷"},
        "requested_agent_id": {"type": "string", "nullable": True, "description": "用户指定的智能体ID或agent_key"},
        "assigned_agent_id": {"type": "string", "nullable": True, "description": "实际分配的智能体ID"},
        "assigned_agent_key": {"type": "string", "nullable": True, "description": "实际分配的智能体逻辑标识"},
        "dispatch_status": {
            "type": "string",
            "description": "调度状态",
            "enum": ["pending", "user_specified", "corrected", "auto_assigned", "no_candidate"],
        },
        "dispatch_reason": {"type": "string", "nullable": True, "description": "调度原因"},
        "run_id": {"type": "string", "nullable": True, "description": "后续执行系统run_id"},
        "room_id": {"type": "string", "nullable": True, "description": "关联作战室ID"},
        "creator_id": {"type": "string", "nullable": True, "description": "创建人ID"},
        "started_at": {"type": "string", "nullable": True, "description": "开始时间"},
        "finished_at": {"type": "string", "nullable": True, "description": "结束时间"},
    },
}

TASK_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "事件ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "task_id": {"type": "string", "description": "任务ID"},
        "event_type": {
            "type": "string",
            "description": "事件类型，如task_created、task_dispatched、dispatch_waiting、task_progress_updated",
        },
        "status": {"type": "string", "nullable": True, "description": "任务状态快照"},
        "progress": {"type": "integer", "nullable": True, "description": "进度快照"},
        "agent_key": {"type": "string", "nullable": True, "description": "相关智能体"},
        "message": {"type": "string", "nullable": True, "description": "事件说明"},
        "metadata": {"type": "object", "description": "事件元数据"},
        "operator_id": {"type": "string", "nullable": True, "description": "操作者ID"},
    },
}

APPROVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "审批ID"},
        "task_id": {"type": "string", "description": "任务ID"},
        "run_id": {"type": "string", "nullable": True, "description": "运行ID"},
        "employee_id": {"type": "string", "nullable": True, "description": "数字员工ID"},
        "tenant_id": {"type": "string", "description": "租户ID"},
        "status": {"type": "string", "description": "审批状态"},
        "trigger_type": {"type": "string", "description": "触发类型"},
        "risk_level": {"type": "string", "description": "风险等级"},
        "policy_snapshot": {"type": "object", "description": "策略快照"},
        "impacted_resources": {"type": "array", "description": "影响资源"},
        "requested_action": {"type": "object", "description": "请求动作"},
        "rollback_plan": {"type": "string", "nullable": True, "description": "回滚计划"},
        "requester_id": {"type": "string", "nullable": True, "description": "请求人"},
        "approver_id": {"type": "string", "nullable": True, "description": "审批人"},
        "decided_at": {"type": "string", "nullable": True, "description": "审批时间"},
        "decision_comment": {"type": "string", "nullable": True, "description": "审批意见"},
        "metadata": {"type": "object", "description": "扩展元数据"},
    },
}


def _json_body(schema: dict) -> dict:
    return {"application/json": {"schema": schema}}


def _response(description: str, data_schema: dict, errors: dict[str, str] | None = None) -> dict:
    response = {
        "200": {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "description": "业务状态码"},
                            "message": {"type": "string", "description": "响应信息"},
                            "data": data_schema,
                        },
                    }
                }
            },
        }
    }
    for status, error_description in (errors or {}).items():
        response[str(status)] = {
            "description": error_description,
            "content": {"application/json": {"schema": ERROR_RESPONSE_SCHEMA}},
        }
    return response


def _require_json(request: Request) -> dict:
    return request.json or {}


def _summarize_scope(value) -> dict:
    if not isinstance(value, dict):
        return {}
    summary = {}
    for key, item in value.items():
        if isinstance(item, list):
            summary[key] = {"kind": "list", "count": len(item)}
        elif isinstance(item, dict):
            summary[key] = {"kind": "object", "count": len(item)}
        else:
            summary[key] = {"kind": "value", "present": item is not None}
    return summary


def _summarize_impacted_resources(resources) -> list:
    if not isinstance(resources, list):
        return []
    summaries = []
    for resource in resources:
        if isinstance(resource, dict):
            scope = resource.get("scope") if isinstance(resource.get("scope"), dict) else resource
            summaries.append(
                {
                    "kind": resource.get("kind") or resource.get("type") or "resource",
                    "scope": _summarize_scope(scope),
                }
            )
        else:
            summaries.append({"kind": "resource", "scope": {}})
    return summaries


def _requested_action_summary(action) -> dict:
    if not isinstance(action, dict):
        return {}
    allowed = {
        "tool",
        "playbook",
        "action",
        "name",
        "risk_level",
    }
    return {key: action[key] for key in allowed if key in action}


def _task_event_metadata_summary(metadata) -> dict:
    if not isinstance(metadata, dict):
        return {}
    allowed = {"run_id", "approval_id", "runtime_event", "decision"}
    return {key: metadata[key] for key in allowed if metadata.get(key) is not None}


def _text_summary(value) -> dict:
    text = "" if value is None else str(value)
    return {"present": bool(text), "length": len(text)}


def _task_detail_json(task: CommandTask, actor: dict | None = None) -> dict:
    raw = task.json
    allowed = [
        "id",
        "ctime",
        "utime",
        "title",
        "description",
        "source",
        "priority",
        "status",
        "progress",
        "required_capabilities",
        "requested_agent_id",
        "assigned_agent_id",
        "assigned_agent_key",
        "dispatch_status",
        "dispatch_reason",
        "run_id",
        "room_id",
        "creator_id",
        "started_at",
        "finished_at",
    ]
    actor = actor or {}
    return {
        **{key: raw.get(key) for key in allowed},
        "payload_summary": _summarize_scope(raw.get("payload")),
        "result_summary": _text_summary(raw.get("result")),
        "assigned_agent_name": actor.get("assigned_agent_name"),
        "assigned_agent_role_name": actor.get("assigned_agent_role_name"),
        "assigned_agent_deleted": actor.get("assigned_agent_deleted", False),
        "creator_display_name": actor.get("creator_display_name"),
        "token_usage": actor.get("token_usage") or zero_usage(),
    }


async def _resolve_task_actors(tasks: list[CommandTask], tenant_id: str) -> dict[str, dict]:
    """批量解析任务的执行员工（含软删）与创建人名称，避免前端 join 丢失已删除员工。"""
    from models.digital_employee import DigitalEmployeeRole
    from models.user import Account

    agent_ids = {t.json.get("assigned_agent_id") for t in tasks if t.json.get("assigned_agent_id")}
    creator_ids = {t.json.get("creator_id") for t in tasks if t.json.get("creator_id")}

    agents_by_id: dict[str, CommandAgent] = {}
    if agent_ids:
        agents = await CommandAgent.filter(tenant_id=tenant_id, id__in=list(agent_ids)).all()
        agents_by_id = {agent.id: agent for agent in agents}

    role_names: dict[str, str] = {}
    role_keys = {a.role_key for a in agents_by_id.values() if a.role_key}
    if role_keys:
        roles = await DigitalEmployeeRole.filter(tenant_id=tenant_id, role_key__in=list(role_keys)).all()
        role_names = {role.role_key: role.name for role in roles}

    creators_by_id: dict[str, Account] = {}
    if creator_ids:
        accounts = await Account.filter(id__in=list(creator_ids)).all()
        creators_by_id = {account.id: account for account in accounts}

    # 批量聚合每个任务的 token 消耗，避免前端逐个查详情
    usage_by_task: dict[str, dict] = {}
    task_ids = [task.id for task in tasks]
    if task_ids:
        runs = await CommandTaskRun.filter(tenant_id=tenant_id, task_id__in=task_ids, deleted=False).all()
        runs_by_task: dict[str, list] = {}
        for run in runs:
            runs_by_task.setdefault(run.task_id, []).append(run)
        for task_id, task_runs in runs_by_task.items():
            usage_by_task[task_id] = aggregate_usage(task_runs)

    actors: dict[str, dict] = {}
    for task in tasks:
        raw = task.json
        agent = agents_by_id.get(raw.get("assigned_agent_id"))
        creator = creators_by_id.get(raw.get("creator_id"))
        actors[task.id] = {
            "assigned_agent_name": agent.name if agent else None,
            "assigned_agent_role_name": role_names.get(agent.role_key) if agent and agent.role_key else None,
            "assigned_agent_deleted": bool(agent.deleted) if agent else False,
            "creator_display_name": (creator.display_name or creator.username) if creator else None,
            "token_usage": usage_by_task.get(task.id, zero_usage()),
        }
    return actors


def _task_run_detail_json(run: CommandTaskRun) -> dict:
    manifest = run.manifest_snapshot if isinstance(run.manifest_snapshot, dict) else {}
    policy = run.policy_snapshot if isinstance(run.policy_snapshot, dict) else {}
    employee = manifest.get("employee") if isinstance(manifest.get("employee"), dict) else {}
    skills = manifest.get("skills")
    approval = policy.get("approval") if isinstance(policy.get("approval"), dict) else {}
    skill_rules = approval.get("skills") if isinstance(approval.get("skills"), dict) else {}
    authorization = policy.get("authorization") if isinstance(policy.get("authorization"), dict) else {}
    return {
        "id": run.id,
        "ctime": run.json["ctime"],
        "utime": run.json["utime"],
        "task_id": run.json["task_id"],
        "employee_id": run.json["employee_id"],
        "run_id": run.run_id,
        "runtime": run.runtime,
        "status": run.status,
        "heartbeat_at": run.json["heartbeat_at"],
        "workspace_etag": run.workspace_etag,
        "started_at": run.json["started_at"],
        "finished_at": run.json["finished_at"],
        "usage": normalize_usage(run.usage),
        "cost": run.cost if isinstance(run.cost, dict) else {},
        "error": run.error if isinstance(run.error, dict) else {},
        "manifest_summary": {
            "employee": {
                "id": employee.get("id"),
                "name": employee.get("name"),
                "agent_key": employee.get("agent_key"),
                "role_key": employee.get("role_key"),
            },
            "skills_count": len(skills) if isinstance(skills, list) else 0,
            "workspace_etag": manifest.get("workspace_etag") or run.workspace_etag,
        },
        "policy_summary": {
            "approval_default": approval.get("default"),
            "approval_skills": skill_rules,
            "authorization": {
                key: authorization.get(key)
                for key in ("source", "rbac_role_id")
                if authorization.get(key) is not None
            },
        },
        "tool_calls": _summarize_tool_calls(getattr(run, "tool_calls", None)),
        "resource_accesses": _summarize_resource_accesses(getattr(run, "resource_accesses", None)),
    }


def _summarize_tool_calls(tool_calls) -> list:
    """把 run 的工具调用记录整理成可读的「调用的动作/剧本」列表。"""
    if not isinstance(tool_calls, list):
        return []
    summaries = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        data = call.get("data") if isinstance(call.get("data"), dict) else {}
        summaries.append(
            {
                "name": call.get("tool_name") or data.get("action") or data.get("playbook") or data.get("name"),
                "status": call.get("status"),
                "kind": "playbook" if data.get("playbook") else ("action" if data.get("action") else "tool"),
            }
        )
    return summaries


def _summarize_resource_accesses(resource_accesses) -> list:
    if not isinstance(resource_accesses, list):
        return []
    summaries = []
    for access in resource_accesses:
        if isinstance(access, dict):
            summaries.append(
                {
                    "kind": access.get("kind") or access.get("type") or "resource",
                    "name": access.get("name") or access.get("target"),
                    "action": access.get("action") or access.get("operation"),
                }
            )
    return summaries


def _task_approval_detail_json(approval: CommandApproval) -> dict:
    metadata = approval.metadata if isinstance(approval.metadata, dict) else {}
    needs_info = metadata.get("needs_info") if isinstance(metadata.get("needs_info"), dict) else {}
    return {
        "id": approval.id,
        "ctime": approval.json["ctime"],
        "utime": approval.json["utime"],
        "task_id": approval.json["task_id"],
        "run_id": approval.json["run_id"],
        "employee_id": approval.json["employee_id"],
        "status": approval.status,
        "trigger_type": approval.trigger_type,
        "risk_level": approval.risk_level,
        "impacted_resources": _summarize_impacted_resources(approval.impacted_resources),
        "requested_action": _requested_action_summary(approval.requested_action),
        "rollback_plan": approval.rollback_plan,
        "requester_id": approval.requester_id,
        "approver_id": approval.approver_id,
        "decided_at": approval.json["decided_at"],
        "expires_at": approval.json["expires_at"],
        "decision_comment": approval.decision_comment,
        "needs_info": {
            key: needs_info.get(key)
            for key in ("requested", "comment", "requested_by", "requested_at")
            if needs_info.get(key) is not None
        },
        "available_decisions": ["approve", "read_only", "needs_info", "takeover", "reject"]
        if approval.status == COMMAND_APPROVAL_STATUS_PENDING
        else [],
    }


def _task_event_detail_json(event: CommandTaskEvent) -> dict:
    return {
        "id": event.id,
        "ctime": event.json["ctime"],
        "utime": event.json["utime"],
        "task_id": event.json["task_id"],
        "event_type": event.event_type,
        "phrase": humanize_event(event.event_type),
        "status": event.status,
        "progress": event.progress,
        "agent_key": event.agent_key,
        "message": event.message,
        "metadata_summary": _task_event_metadata_summary(event.metadata),
        "operator_id": event.operator_id,
    }


@command_bp.post("/agents/register")
@openapi.definition(
    summary="登记或更新指挥部智能体画像",
    description="登记新的指挥部智能体，或按tenant_id + agent_key更新已有智能体画像、能力标签、状态和心跳时间。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["agent_key"],
            "properties": {
                "agent_key": {"type": "string", "description": "智能体逻辑标识，同一租户内唯一"},
                "name": {"type": "string", "description": "智能体名称；未传时默认使用agent_key"},
                "description": {"type": "string", "description": "智能体说明"},
                "capabilities": {
                    "type": "array",
                    "description": "能力标签列表，用于任务required_capabilities匹配",
                    "items": {"type": "string"},
                },
                "status": {
                    "type": "string",
                    "description": "智能体状态",
                    "enum": ["idle", "busy", "offline", "error"],
                    "default": "idle",
                },
                "max_concurrent": {"type": "integer", "description": "最大并发任务数", "minimum": 1, "default": 1},
                "metadata": {"type": "object", "description": "扩展元数据"},
                "runtime": {"type": "string", "description": "运行时类型，默认lion_agent"},
                "prompt_config": {"type": "object", "description": "提示词扩展配置"},
                "is_builtin": {"type": "boolean", "description": "是否为内置智能体"},
            },
        }
    ),
    response=_response("登记或更新成功", AGENT_SCHEMA, {"400": "参数错误"}),
)
@requires_permission("command_center:u")
async def register_agent(request: Request):
    data = _require_json(request)
    agent_key = data.get("agent_key")
    name = data.get("name") or agent_key
    if not agent_key:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    agent, _ = await CommandAgent.get_or_create(
        tenant_id=tenant_id,
        agent_key=agent_key,
        defaults={
            "name": name,
            "description": data.get("description"),
            "capabilities": data.get("capabilities", []),
            "status": data.get("status", "idle"),
            "max_concurrent": data.get("max_concurrent", 1),
            "metadata": data.get("metadata", {}),
            "runtime": data.get("runtime", "lion_agent"),
            "prompt_config": data.get("prompt_config", {}),
            "is_builtin": data.get("is_builtin", False),
        },
    )
    for field in ["name", "description", "capabilities", "status", "max_concurrent", "metadata", "runtime", "prompt_config", "is_builtin"]:
        if field in data:
            setattr(agent, field, data[field])
    agent.deleted = False
    agent.heartbeat_at = datetime.now(TZ)
    await agent.save()
    return response_ok(agent.json)


@command_bp.post("/agents/list")
@openapi.definition(
    summary="查看智能体工作状态",
    description="按租户查询未删除的指挥部智能体，可按状态过滤，并按status、load、agent_key排序。需要command_center:r权限。",
    body=_json_body(
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "可选，按智能体状态过滤",
                    "enum": ["idle", "busy", "offline", "error"],
                }
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "agents": {"type": "array", "description": "智能体列表", "items": AGENT_SCHEMA},
            },
        },
    ),
)
@requires_permission("command_center:r")
async def list_agents(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    condition = {"tenant_id": tenant_id, "deleted": False}
    if data.get("status"):
        condition["status"] = data["status"]
    agents = await CommandAgent.filter(**condition).order_by("status", "load", "agent_key").all()
    return response_ok({"agents": [item.json for item in agents]})


@command_bp.post("/agents/status")
@openapi.definition(
    summary="更新智能体工作状态",
    description="按id或agent_key定位智能体，更新状态、负载、并发上限、当前任务或元数据，并刷新心跳时间。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "oneOf": [{"required": ["id"]}, {"required": ["agent_key"]}],
            "properties": {
                "id": {"type": "string", "description": "智能体ID，与agent_key二选一"},
                "agent_key": {"type": "string", "description": "智能体逻辑标识，与id二选一"},
                "status": {
                    "type": "string",
                    "description": "智能体状态",
                    "enum": ["idle", "busy", "offline", "error"],
                },
                "load": {"type": "integer", "description": "当前任务负载", "minimum": 0},
                "max_concurrent": {"type": "integer", "description": "最大并发任务数", "minimum": 1},
                "current_task_id": {"type": "string", "nullable": True, "description": "当前主任务ID"},
                "metadata": {"type": "object", "description": "扩展元数据"},
                "runtime": {"type": "string", "description": "运行时类型"},
                "prompt_config": {"type": "object", "description": "提示词扩展配置"},
                "is_builtin": {"type": "boolean", "description": "是否为内置智能体"},
            },
        }
    ),
    response=_response("更新成功", AGENT_SCHEMA, {"400": "参数错误", "404": "智能体不存在"}),
)
@requires_permission("command_center:u")
async def update_agent_status(request: Request):
    data = _require_json(request)
    agent_id = data.get("id") or data.get("agent_key")
    if not agent_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    if len(agent_id) == 36:
        agent = await CommandAgent.get_or_none(id=agent_id, tenant_id=tenant_id, deleted=False)
    else:
        agent = await CommandAgent.get_or_none(agent_key=agent_id, tenant_id=tenant_id, deleted=False)
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)

    for field in ["status", "load", "max_concurrent", "current_task_id", "metadata", "runtime", "prompt_config", "is_builtin"]:
        if field in data:
            setattr(agent, field, data[field])
    agent.heartbeat_at = datetime.now(TZ)
    await agent.save()
    return response_ok(agent.json)


@command_bp.post("/tasks/create")
@openapi.definition(
    summary="创建指挥部任务并触发调度",
    description="创建指挥部任务后立即返回创建结果，并在后台按required_capabilities触发调度；如指定agent_id但能力或状态不匹配，会自动纠正到可用智能体。需要command_center:c权限。",
    body=_json_body(
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题；不传时按任务内容自动生成"},
                "description": {"type": "string", "description": "任务内容"},
                "source": {"type": "string", "description": "任务来源", "default": "manual"},
                "priority": {"type": "integer", "description": "优先级，数值越小优先级越高", "default": 3},
                "required_capabilities": {
                    "type": "array",
                    "description": "任务所需能力标签；候选智能体必须完整覆盖",
                    "items": {"type": "string"},
                },
                "payload": {"type": "object", "description": "任务载荷，后续执行系统可读取"},
                "agent_id": {"type": "string", "description": "可选，用户指定的智能体ID或agent_key"},
                "requested_agent_id": {"type": "string", "description": "可选，用户指定的智能体ID或agent_key"},
                "room_id": {"type": "string", "description": "可选，关联作战室ID"},
            },
        }
    ),
    response=_response("创建成功", TASK_SCHEMA, {"400": "参数错误"}),
)
@requires_permission("command_center:c")
async def create_task(request: Request):
    data = _require_json(request)
    if not has_task_content(data):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    creator = request.ctx.session["account_info"]
    task = await create_command_task(tenant_id, creator, data)
    return response_ok(task.json)


@command_bp.post("/tasks/list")
@openapi.definition(
    summary="查看任务列表与进展",
    description="分页查询指挥部任务，可按任务状态、实际分配智能体和关键字过滤。需要command_center:r权限。",
    body=_json_body(
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "可选，按任务状态过滤",
                    "enum": ["pending_dispatch", "dispatched", "running", "completed", "failed", "cancelled"],
                },
                "agent_key": {"type": "string", "description": "可选，按实际分配的智能体agent_key过滤"},
                "keyword": {"type": "string", "description": "可选，按任务标题或描述模糊搜索"},
                "page": {"type": "integer", "description": "页码", "minimum": 1, "default": 1},
                "size": {"type": "integer", "description": "每页数量", "minimum": 1, "default": 20},
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "total": {"type": "integer", "description": "总数"},
                "page": {"type": "integer", "description": "当前页码"},
                "size": {"type": "integer", "description": "每页数量"},
                "tasks": {"type": "array", "description": "任务列表", "items": TASK_SCHEMA},
            },
        },
    ),
)
@requires_permission("command_center:r")
async def query_tasks(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    tasks, total = await list_tasks(tenant_id, data)
    actors = await _resolve_task_actors(tasks, tenant_id)
    return response_ok(
        {
            "total": total,
            "page": data.get("page", 1),
            "size": data.get("size", 20),
            "tasks": [_task_detail_json(task, actors.get(task.id)) for task in tasks],
        }
    )


@command_bp.post("/tasks/detail")
@openapi.definition(
    summary="查看任务详情和事件流",
    description="查询单个任务详情，并按创建时间返回任务事件流，用于展示调度历史和进度变化。需要command_center:r权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "task": TASK_SCHEMA,
                "events": {"type": "array", "description": "任务事件流", "items": TASK_EVENT_SCHEMA},
            },
        },
        {"400": "参数错误", "404": "任务不存在"},
    ),
)
@requires_permission("command_center:r")
async def task_detail(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    events = await CommandTaskEvent.filter(task_id=task.id, tenant_id=tenant_id, deleted=False).order_by("ctime").all()
    runs = await CommandTaskRun.filter(task_id=task.id, tenant_id=tenant_id, deleted=False).order_by("-ctime").all()
    approvals = await CommandApproval.filter(task_id=task.id, tenant_id=tenant_id, deleted=False).order_by("-ctime").all()
    actors = await _resolve_task_actors([task], tenant_id)
    return response_ok(
        {
            "task": _task_detail_json(task, actors.get(task.id)),
            "summary": build_task_summary(task, runs, approvals),
            "result": task.result,
            "events": [_task_event_detail_json(event) for event in events],
            "runs": [_task_run_detail_json(run) for run in runs],
            "approvals": [_task_approval_detail_json(approval) for approval in approvals],
        }
    )


@command_bp.post("/tasks/stats")
@openapi.definition(
    summary="查看资源消耗和风险概览",
    description="聚合当前租户今日任务 token 消耗、运行中任务、待审批与失败任务数量。需要command_center:r权限。",
    body=_json_body({"type": "object", "properties": {}}),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "token_usage": {"type": "object", "description": "今日 token 消耗聚合"},
                "tasks": {"type": "object", "description": "任务状态计数"},
                "risks": {"type": "object", "description": "风险计数"},
            },
        },
    ),
)
@requires_permission("command_center:r")
async def task_stats(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    today_start = datetime.combine(datetime.now(TZ).date(), time.min, tzinfo=TZ)
    runs = await CommandTaskRun.filter(tenant_id=tenant_id, deleted=False, ctime__gte=today_start).all()
    running = await CommandTask.filter(tenant_id=tenant_id, deleted=False, status__in=["running", "dispatched", "pending_approval"]).count()
    pending_approvals = await CommandApproval.filter(tenant_id=tenant_id, deleted=False, status=COMMAND_APPROVAL_STATUS_PENDING).count()
    failed_tasks = await CommandTask.filter(tenant_id=tenant_id, deleted=False, status="failed").count()
    return response_ok(
        {
            "token_usage": aggregate_usage(runs),
            "tasks": {"running": running},
            "risks": {"pending_approvals": pending_approvals, "failed_tasks": failed_tasks},
        }
    )


@command_bp.post("/tasks/progress")
@openapi.definition(
    summary="更新任务进度或状态",
    description="更新任务进度、状态或后续执行系统run_id，并记录任务事件；任务完成、失败或取消时会释放智能体负载。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
                "progress": {"type": "integer", "description": "任务进度，系统会限制在0-100", "minimum": 0, "maximum": 100},
                "status": {
                    "type": "string",
                    "description": "任务状态",
                    "enum": ["pending_dispatch", "dispatched", "running", "completed", "failed", "cancelled"],
                },
                "run_id": {"type": "string", "description": "后续执行系统run_id"},
                "message": {"type": "string", "description": "进度事件说明"},
                "metadata": {"type": "object", "description": "进度事件元数据"},
            },
        }
    ),
    response=_response("更新成功", TASK_SCHEMA, {"400": "参数错误", "404": "任务不存在"}),
)
@requires_permission("command_center:u")
async def task_progress(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    operator = request.ctx.session["account_info"]
    task = await update_task_progress(task, data, operator.id)
    return response_ok(task.json)


@command_bp.post("/tasks/dispatch")
@openapi.definition(
    summary="重新触发任务调度",
    description="对已有任务重新执行调度；可传agent_id更新用户指定智能体，调度器仍会按能力、状态和负载校验并自动纠正。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
                "agent_id": {"type": "string", "description": "可选，新的用户指定智能体ID或agent_key"},
            },
        }
    ),
    response=_response("调度完成", TASK_SCHEMA, {"400": "参数错误", "404": "任务不存在"}),
)
@requires_permission("command_center:u")
async def retry_dispatch(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    if "agent_id" in data:
        task.requested_agent_id = data.get("agent_id")
        await task.save(update_fields=["requested_agent_id"])
    operator = request.ctx.session["account_info"]
    task = await dispatch_task(task, operator.id)
    return response_ok(task.json)


@command_bp.post("/tasks/cancel")
@openapi.definition(
    summary="取消任务",
    description="取消尚未结束的任务；会释放已分配数字员工的负载并记录事件。已结束的任务不可取消。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
                "reason": {"type": "string", "description": "取消原因，可选"},
            },
        }
    ),
    response=_response("取消成功", TASK_SCHEMA, {"400": "参数错误", "404": "任务不存在", "409": "任务已结束"}),
)
@requires_permission("command_center:u")
async def cancel_task(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    if task.status in TERMINAL_TASK_STATUS:
        raise CustomError(ErrorCode.COMMAND_TASK_ALREADY_TERMINAL)
    operator = request.ctx.session["account_info"]
    reason = (data.get("reason") or "").strip() or "用户取消任务"
    task = await update_task_progress(
        task,
        {"status": "cancelled", "message": reason, "metadata": {"cancelled_by": operator.id}},
        operator.id,
    )
    return response_ok(task.json)


@command_bp.post("/tasks/delete")
@openapi.definition(
    summary="删除任务",
    description="软删除已结束的任务（完成/失败/取消/已接管）。未结束的任务需先取消再删除。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
            },
        }
    ),
    response=_response("删除成功", {"type": "object", "properties": {"id": {"type": "string"}}}, {"400": "参数错误", "404": "任务不存在", "409": "任务尚未结束"}),
)
@requires_permission("command_center:u")
async def delete_task(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    if task.status not in TERMINAL_TASK_STATUS:
        raise CustomError(ErrorCode.COMMAND_TASK_NOT_TERMINAL)
    task.deleted = True
    await task.save(update_fields=["deleted", "utime"])
    return response_ok({"id": task.id})


@command_bp.post("/approvals/list")
@requires_permission("command_center:r")
async def query_approvals(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    approvals, total = await list_approvals(tenant_id, data)
    return response_ok(
        {
            "total": total,
            "page": data.get("page", 1),
            "size": data.get("size", 20),
            "approvals": [_task_approval_detail_json(approval) for approval in approvals],
        }
    )


@command_bp.post("/approvals/detail")
@requires_permission("command_center:r")
async def approval_detail(request: Request):
    data = _require_json(request)
    approval_id = data.get("id")
    if not approval_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    approval = await get_approval(request.ctx.session.get("tenant_id"), approval_id)
    return response_ok(_task_approval_detail_json(approval))


@command_bp.post("/approvals/decision")
@requires_permission("command_center:u")
async def approval_decision(request: Request):
    data = _require_json(request)
    approval_id = data.get("id")
    decision = data.get("decision")
    if not approval_id or not decision:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    account = request.ctx.session["account_info"]
    approval = await decide_approval(
        request.ctx.session.get("tenant_id"),
        approval_id,
        decision,
        account.id,
        data.get("comment"),
    )
    return response_ok(_task_approval_detail_json(approval))


@command_bp.post("/approvals/resume")
@requires_permission("command_center:u")
async def approval_resume(request: Request):
    data = _require_json(request)
    approval_id = data.get("id")
    if not approval_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    account = request.ctx.session["account_info"]
    result = await resume_approved_approval(request.ctx.session.get("tenant_id"), approval_id, operator_id=account.id)
    return response_ok(result)
