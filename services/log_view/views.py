# coding=utf-8

import httpx
from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok, response_fail
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import query_vlog
from settings import VL_HOST

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
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "LogsQL查询语句"},
                    "limit": {"type": "integer", "description": "返回结果数量限制", "default": 1000},
                    "start": {"type": "string", "description": "查询开始时间(RFC3339格式或Unix时间戳)"},
                    "end": {"type": "string", "description": "查询结束时间(RFC3339格式或Unix时间戳)"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "查询成功",
            "content": {
                "application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer", "example": 0}, "data": {"type": "object", "description": "VictoriaLogs查询结果"}}}}
            },
        }
    },
)
@requires_permission("logs:r")
async def get_logs_by_sql(request):
    query = request.json.get("query")
    if not query:
        raise CustomError(ErrorCode.ParameterError)

    limit = request.json.get("limit", 1000)
    project_id = request.json.get("project_id", 1000)
    start = request.json.get("start")
    end = request.json.get("end")

    result = await query_vlog(query, limit, project_id, start, end)
    return response_ok(result) if result is not None else response_fail(ErrorCode.VictoriaLogsError)


@logs_bp.route("/indexes", methods=["POST"])
@openapi.definition(
    summary="获取日志流列表",
    description="获取VictoriaLogs中所有可用的日志流(streams)列表",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"code": {"type": "integer", "example": 0}, "data": {"type": "array", "items": {"type": "string"}, "description": "日志流列表"}}}
                }
            },
        }
    },
)
@requires_permission("logs:r")
async def get_index_list(request):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # VictoriaLogs获取streams接口: /select/logsql/streams
            resp = await client.get(f"{VL_HOST}/select/logsql/streams")
            resp.raise_for_status()
            data = resp.json()

            # 提取stream名称列表
            streams = []
            if isinstance(data, dict) and "streams" in data:
                streams = [stream.get("_stream", "") for stream in data["streams"] if stream.get("_stream")]
            elif isinstance(data, list):
                streams = [item.get("_stream", "") for item in data if item.get("_stream")]

            # 过滤以logs-开头的流(保持与原有逻辑一致)
            result = [s for s in streams if s.startswith("logs-")]
            return response_ok(list(set(result)))
        except Exception as e:
            logger.warning(f"VictoriaLogs get streams error: {e}")
            return response_ok([])


@logs_bp.route("/fields", methods=["POST"])
@openapi.definition(
    summary="获取日志流字段列表",
    description="获取指定日志流的字段列表及其数据类型",
    body={"application/json": {"schema": {"type": "object", "required": ["index"], "properties": {"index": {"type": "string", "description": "日志流名称"}}}}},
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"code": {"type": "integer", "example": 0}, "data": {"type": "object", "description": "字段信息，键为字段名，值为字段类型"}}}
                }
            },
        }
    },
)
@requires_permission("logs:r")
async def get_field_list(request):
    stream_name = request.json.get("index")
    if not stream_name:
        raise CustomError(ErrorCode.ParameterError)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # VictoriaLogs获取字段信息接口: /select/logsql/field_names
            params = {"query": f"_stream:{stream_name}"}
            resp = await client.get(f"{VL_HOST}/select/logsql/field_names", params=params)
            resp.raise_for_status()
            data = resp.json()

            # 构建字段信息字典
            # VictoriaLogs返回的是字段名列表，类型信息需要通过查询推断或统一设置
            fields = {}
            if isinstance(data, dict) and "fields" in data:
                # 假设返回格式为 {"fields": ["field1", "field2", ...]}
                for field in data["fields"]:
                    fields[field] = "text"  # VictoriaLogs默认为文本类型
            elif isinstance(data, list):
                # 假设返回格式为 ["field1", "field2", ...]
                for field in data:
                    fields[field] = "text"

            return response_ok({stream_name: fields})
        except Exception as e:
            logger.warning(f"VictoriaLogs get fields error: {e}")
            return response_ok({stream_name: {}})
