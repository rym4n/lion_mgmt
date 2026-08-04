# coding=utf-8
import re
from datetime import datetime, timezone

from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_fail, response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import delete_vlog, get_vlog_delete_task_status, query_vlog
from models.event import EventsIngestion

logs_bp = Blueprint("logs", url_prefix="/logs")
_all__ = ["logs_bp"]

DEFAULT_LOG_QUERY_LIMIT = 100
MAX_LOG_QUERY_LIMIT = 1000
_LIMITING_PIPE_RE = re.compile(r"(?:^|\|)\s*(?:limit|first|last)\b", re.IGNORECASE)


def _parse_log_limit(value, *, default=DEFAULT_LOG_QUERY_LIMIT):
    if value is None or isinstance(value, bool):
        return default

    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default

    if limit <= 0:
        return default
    return min(limit, MAX_LOG_QUERY_LIMIT)


def _query_has_explicit_limit(query):
    return bool(_LIMITING_PIPE_RE.search(query or ""))


def _resolve_log_query_limit(query, requested_limit):
    if requested_limit is not None:
        return _parse_log_limit(requested_limit)
    if _query_has_explicit_limit(query):
        return MAX_LOG_QUERY_LIMIT
    return DEFAULT_LOG_QUERY_LIMIT


def _parse_log_query_time(value):
    if not isinstance(value, str) or not value.strip():
        raise CustomError(ErrorCode.PARAM_ERROR)

    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CustomError(ErrorCode.PARAM_ERROR) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@logs_bp.route("/query", methods=["POST"])
@openapi.definition(
    summary="查询VictoriaLogs日志数据",
    description="通过LogsQL语句查询VictoriaLogs中的日志数据",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["query", "project_id"],
                "properties": {
                    "query": {"type": "string", "description": "LogsQL查询语句"},
                    "project_id": {"type": "integer", "description": "项目ID"},
                    "start": {
                        "type": "string",
                        "description": "查询开始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end": {
                        "type": "string",
                        "description": "查询结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"返回日志条数，默认{DEFAULT_LOG_QUERY_LIMIT}，最大{MAX_LOG_QUERY_LIMIT}",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "查询成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "description": "VictoriaLogs查询结果",
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("logs:r")
async def get_logs_by_sql(request):
    param = request.json
    query = param.get("query")
    project_id = param.get("project_id")

    if not query or not project_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    start = param.get("start")
    end = param.get("end")

    start_iso = None
    end_iso = None
    if start is not None:
        start_iso = _parse_log_query_time(start)
    if end is not None:
        end_iso = _parse_log_query_time(end)

    limit = _resolve_log_query_limit(query, param.get("limit"))
    result = await query_vlog(query, project_id, start_iso, end_iso, limit=limit)
    return response_ok(result) if result is not None else response_fail(ErrorCode.VICTORIA_LOGS_ERROR)


@logs_bp.route("/indexes", methods=["POST"])
@openapi.definition(
    summary="获取项目列表",
    description="获取当前租户下的所有日志项目列表",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {
                                            "type": "integer",
                                            "description": "项目ID",
                                        },
                                        "name": {
                                            "type": "string",
                                            "description": "项目名称",
                                        },
                                    },
                                },
                                "description": "项目列表",
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("logs:r")
async def get_index_list(request):
    tenant_id = request.ctx.session.get("tenant_id")
    try:
        # 获取所有已配置的项目
        projects = await EventsIngestion.filter(
            project_id__isnull=False,
            tenant_id=tenant_id,
        ).values("project_id", "event_ingestion_name", "deleted")

        result = [
            {
                "id": p["project_id"],
                "name": p["event_ingestion_name"] or f"Project {p['project_id']}",
                "deleted": p["deleted"],
            }
            for p in projects
        ]
        return response_ok(result)
    except Exception as e:
        logger.error(f"Get project list error: {e}")
        return response_ok([])


@logs_bp.route("/delete", methods=["POST"])
@openapi.definition(
    summary="删除日志项目的存量日志",
    description="为当前租户下指定日志项目创建 VictoriaLogs 异步删除任务",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["project_id"],
                "properties": {
                    "project_id": {"type": "integer", "description": "日志项目ID"}
                },
            }
        }
    },
)
@requires_permission("logs:d")
async def delete_project_logs(request):
    params = request.json or {}
    project_id = params.get("project_id")
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    project_exists = await EventsIngestion.filter(
        project_id=project_id,
        tenant_id=tenant_id,
    ).exists()
    if not project_exists:
        raise CustomError(ErrorCode.PARAM_ERROR)

    result = await delete_vlog(project_id)
    if result is None:
        return response_fail(ErrorCode.VICTORIA_LOGS_DELETE_ERROR)
    return response_ok(result)


@logs_bp.route("/delete/status", methods=["POST"])
@openapi.definition(
    summary="查询日志删除任务状态",
    description="查询当前租户指定 VictoriaLogs 删除任务是否仍在执行",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["project_id", "task_id"],
                "properties": {
                    "project_id": {"type": "integer", "description": "日志项目ID"},
                    "task_id": {"type": "string", "description": "日志删除任务ID"},
                },
            }
        }
    },
)
@requires_permission("logs:d")
async def get_delete_project_logs_status(request):
    params = request.json or {}
    project_id = params.get("project_id")
    task_id = params.get("task_id")
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or project_id <= 0
        or not isinstance(task_id, str)
        or not task_id.strip()
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    project_exists = await EventsIngestion.filter(
        project_id=project_id,
        tenant_id=tenant_id,
    ).exists()
    if not project_exists:
        raise CustomError(ErrorCode.PARAM_ERROR)

    active = await get_vlog_delete_task_status(task_id.strip(), project_id)
    if active is None:
        return response_fail(ErrorCode.VICTORIA_LOGS_DELETE_ERROR)
    return response_ok(
        {
            "task_id": task_id.strip(),
            "active": active,
            "status": "running" if active else "completed",
        }
    )


@logs_bp.route("/fields", methods=["POST"])
@openapi.definition(
    summary="获取日志字段列表",
    description="获取指定项目的日志字段列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["project_id"],
                "properties": {"project_id": {"type": "integer", "description": "项目ID"}},
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "description": "字段信息，键为字段名，值为字段类型",
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("logs:r")
async def get_field_list(request):
    project_id = request.json.get("project_id")
    if not project_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    try:
        # 由于VictoriaLogs可能没有直接获取字段列表的接口，这里通过查询最近的日志来推断字段
        # 查询最近的50条日志，合并所有字段
        logs = await query_vlog(query="*", project_id=project_id, limit=50)

        fields = {}
        if logs:
            for log in logs:
                if isinstance(log, dict):
                    for key in log.keys():
                        # 过滤掉内部字段（可选，这里先保留所有）
                        fields[key] = "text"

        return response_ok(fields)
    except Exception as e:
        logger.warning(f"VictoriaLogs get fields error: {e}")
        return response_ok({})
