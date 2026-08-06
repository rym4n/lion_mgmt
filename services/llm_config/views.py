# coding=utf-8
from __future__ import annotations

from sanic import Blueprint, Request
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from services.llm_config.manager import (
    create_config,
    delete_config,
    list_configs,
    set_purpose,
    update_config,
)
from services.llm_config.tester import test_connectivity
from services.workflow.views import check_ai_permission

llm_config_bp = Blueprint("llm_config", url_prefix="/llm-config")


def _tenant(request: Request) -> str:
    return request.ctx.session.get("tenant_id")


def _json(request: Request) -> dict:
    return request.json or {}


def _require(params: dict, *keys: str) -> None:
    for key in keys:
        if not params.get(key):
            raise CustomError(ErrorCode.PARAM_ERROR)


@llm_config_bp.post("/list")
@openapi.definition(summary="查询模型配置列表", description="需要 ai_config:r 权限")
@requires_permission("ai_config:r")
async def list_view(request: Request):
    check_ai_permission()
    params = _json(request)
    return response_ok(await list_configs(_tenant(request), params.get("name")))


@llm_config_bp.post("/create")
@openapi.definition(summary="创建模型配置", description="需要 ai_config:c 权限")
@requires_permission("ai_config:c")
async def create_view(request: Request):
    check_ai_permission()
    params = _json(request)
    _require(params, "name", "base_url", "api_key", "model_name")
    return response_ok(
        await create_config(
            _tenant(request),
            name=params["name"],
            base_url=params["base_url"],
            api_key=params["api_key"],
            model_name=params["model_name"],
            purpose=params.get("purpose"),
            remark=params.get("remark"),
        )
    )


@llm_config_bp.post("/update")
@openapi.definition(summary="更新模型配置", description="需要 ai_config:u 权限；api_key 留空表示不修改")
@requires_permission("ai_config:u")
async def update_view(request: Request):
    check_ai_permission()
    params = _json(request)
    _require(params, "id")
    return response_ok(
        await update_config(
            _tenant(request),
            params["id"],
            name=params.get("name"),
            base_url=params.get("base_url"),
            api_key=params.get("api_key"),
            model_name=params.get("model_name"),
            remark=params.get("remark"),
            enabled=params.get("enabled"),
        )
    )


@llm_config_bp.post("/delete")
@openapi.definition(summary="删除模型配置", description="需要 ai_config:d 权限；返回受影响员工数")
@requires_permission("ai_config:d")
async def delete_view(request: Request):
    check_ai_permission()
    params = _json(request)
    _require(params, "id")
    affected = await delete_config(_tenant(request), params["id"])
    return response_ok({"affected_employees": affected})


@llm_config_bp.post("/set-purpose")
@openapi.definition(summary="设置模型配置用途", description="需要 ai_config:u 权限；chat 为全局默认，lite 为轻量档")
@requires_permission("ai_config:u")
async def set_purpose_view(request: Request):
    check_ai_permission()
    params = _json(request)
    _require(params, "id")
    return response_ok(await set_purpose(_tenant(request), params["id"], params.get("purpose")))


@llm_config_bp.post("/test")
@openapi.definition(summary="测试模型连通性", description="需要 ai_config:c 权限；用表单当前值发一个最小 chat 请求")
@requires_permission("ai_config:c")
async def test_view(request: Request):
    check_ai_permission()
    params = _json(request)
    _require(params, "base_url", "model_name")
    api_key = params.get("api_key")
    if not api_key and params.get("id"):
        # 编辑场景下表单未重填 key，取库里已存的密文解密值
        from models import LlmModelConfig

        record = await LlmModelConfig.get_or_none(
            id=params["id"], tenant_id=_tenant(request), deleted=False
        )
        api_key = record.api_key if record else None
    _require({"api_key": api_key}, "api_key")
    return response_ok(await test_connectivity(params["base_url"], api_key, params["model_name"]))
