# coding=utf-8

from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok, response_fail
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.security.authorization import requires_permission
from libs.utils import query_vlog
from models.event import EventsIngestion

logs_bp = Blueprint("logs", url_prefix="/logs")
_all__ = ["logs_bp"]


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
                        "description": "查询开始时间(RFC3339格式或Unix时间戳)",
                    },
                    "end": {
                        "type": "string",
                        "description": "查询结束时间(RFC3339格式或Unix时间戳)",
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
        raise CustomError(ErrorCode.ParameterError)

    start = param.get("start")
    end = param.get("end")

    result = await query_vlog(query, project_id, start, end, limit=1000)
    return response_ok(result) if result is not None else response_fail(ErrorCode.VictoriaLogsError)


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
    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")
    try:
        # 获取所有已配置的项目
        projects = await EventsIngestion.filter(project_id__isnull=False, tenant_id=tenant_id).values("project_id", "event_ingestion_name")

        result = [
            {
                "id": p["project_id"],
                "name": p["event_ingestion_name"] or f"Project {p['project_id']}",
            }
            for p in projects
        ]
        return response_ok(result)
    except Exception as e:
        logger.error(f"Get project list error: {e}")
        return response_ok([])


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
        raise CustomError(ErrorCode.ParameterError)

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
