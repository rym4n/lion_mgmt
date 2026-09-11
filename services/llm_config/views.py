# coding=utf-8
from __future__ import annotations

from sanic import Blueprint, Request
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.maintenance import DEFAULT_TENANT_ID
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from services.llm_config.manager import (
    VALID_PURPOSES,
    create_config,
    delete_config,
    list_catalog,
    list_configs,
    set_purpose,
    update_config,
)
from services.llm_config.tester import test_connectivity
from services.workflow.views import check_ai_permission

llm_config_bp = Blueprint("llm_config", url_prefix="/llm-config")


def _tenant(request: Request) -> str:
    """管理接口恒用 default 租户（SPEC §5.6）。

    非 default 租户的会话一律拒绝：这些接口返回 base_url / api_key（脱敏后仍
    暴露前后缀）等管理信息，普通租户不该看到。租户用户要选模型走
    只读的 /llm-config/catalog。

    只读 session，绝不接受请求体传入的 tenant_id——那会开出一个新的越权面
    （对照 docs/lion_mgmt白盒审计结果.md 的 Vuln 3）。
    """
    session_tenant = str(request.ctx.session.get("tenant_id") or "")
    if session_tenant != DEFAULT_TENANT_ID:
        raise CustomError(ErrorCode.FORBIDDEN)
    return DEFAULT_TENANT_ID


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


@llm_config_bp.post("/catalog")
@openapi.definition(summary="模型目录（只读）", description="任意租户可见，不鉴权")
async def catalog_view(request: Request):
    """只读目录，供普通租户选择模型（P7 消费）。

    不调 _tenant()，不检查权限：模型目录是平台基础设施，
    任意会话都要能读（对照 check_ai_permission() 的注释）。
    返回白名单字段，不泄露 base_url / api_key。
    """
    return response_ok(await list_catalog())


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
            dimensions=params.get("dimensions"),
            context_size=params.get("context_size", 128000),
            trigger_ratio=params.get("trigger_ratio", 0.8),
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
            enabled=params.get("enabled"),
            dimensions=params.get("dimensions"),
            context_size=params.get("context_size"),
            trigger_ratio=params.get("trigger_ratio"),
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
@openapi.definition(summary="测试模型连通性", description="需要 ai_config:c 权限；按模型用途测试对应接口")
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
    purpose = params.get("purpose")
    if purpose is not None and purpose not in VALID_PURPOSES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(
        await test_connectivity(
            params["base_url"], api_key, params["model_name"], purpose=purpose
        )
    )
