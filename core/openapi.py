# coding=utf-8
"""Normalize legacy response declarations for the current Sanic Ext OpenAPI builder."""

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from sanic_ext.extensions.openapi.builders import (
    OperationBuilder,
    OperationStore,
    SpecificationBuilder,
)
from sanic_ext.extensions.openapi.definitions import Response

SUCCESS_RESPONSE_SCHEMA_NAME = "ApiSuccessResponse"
ERROR_RESPONSE_SCHEMA_NAME = "ApiErrorResponse"

SUCCESS_RESPONSE_SCHEMA = {
    "type": "object",
    "description": "AIOE API 统一成功响应",
    "required": ["code", "data"],
    "properties": {
        "code": {
            "type": "integer",
            "description": "业务状态码，0 表示成功",
            "example": 0,
        },
        "data": {
            "description": "响应数据，具体结构由接口定义",
        },
    },
}

ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "description": "AIOE API 统一错误响应",
    "required": ["code", "message"],
    "properties": {
        "code": {
            "type": "integer",
            "description": "业务错误码",
        },
        "message": {
            "type": "string",
            "description": "错误信息",
        },
    },
}

_SUCCESS_RESPONSE_CONTENT = {
    "application/json": {
        "schema": {
            "$ref": f"#/components/schemas/{SUCCESS_RESPONSE_SCHEMA_NAME}",
        }
    }
}

_ERROR_RESPONSE_CONTENT = {
    "application/json": {
        "schema": {
            "$ref": f"#/components/schemas/{ERROR_RESPONSE_SCHEMA_NAME}",
        }
    }
}


def _status_code(value: Any) -> str | None:
    status = str(value)
    if status == "default":
        return status
    if status.isdigit() and 100 <= int(status) <= 599:
        return status
    return None


def _legacy_response_payload(media_type: Any) -> dict[str, Any] | None:
    fields = getattr(media_type, "fields", None)
    if not isinstance(fields, dict):
        return None

    payload = fields.get("schema")
    if hasattr(payload, "serialize"):
        payload = payload.serialize()
    if not isinstance(payload, dict):
        return None
    if payload and all(isinstance(key, str) and "/" in key for key in payload):
        return {"content": payload}
    if "description" not in payload and "content" not in payload:
        return None
    return payload


def _normalize_legacy_responses(operation: OperationBuilder) -> bool:
    legacy_response = operation.responses.get("default")
    if len(operation.responses) != 1 or legacy_response is None:
        return False

    legacy_content = legacy_response.fields.get("content")
    if not isinstance(legacy_content, dict) or not legacy_content:
        return False

    normalized = []
    for raw_status, media_type in legacy_content.items():
        status = _status_code(raw_status)
        payload = _legacy_response_payload(media_type)
        if status is None or payload is None:
            return False
        normalized.append((status, payload))

    operation.responses.clear()
    for status, payload in normalized:
        description = payload.get("description") or "响应"
        content = payload.get("content")
        if content is None:
            operation.responses[status] = Response(description=description)
        else:
            operation.response(status, content, description)
    return True


def _stringify_response_statuses(operation: OperationBuilder) -> None:
    for raw_status, response in list(operation.responses.items()):
        status = _status_code(raw_status)
        if status is None or status == raw_status:
            continue
        operation.responses.pop(raw_status)
        operation.responses.setdefault(status, response)


def ensure_openapi_operation_responses(operation: OperationBuilder) -> None:
    """Preserve precise responses or attach the shared AIOE response contract."""

    if operation._exclude:
        return
    if _normalize_legacy_responses(operation):
        return
    if operation.responses:
        _stringify_response_statuses(operation)
        return

    operation.response(
        "200",
        deepcopy(_SUCCESS_RESPONSE_CONTENT),
        "请求成功；data 的具体结构取决于接口",
    )
    operation.response(
        "default",
        deepcopy(_ERROR_RESPONSE_CONTENT),
        "请求失败；HTTP 状态码和业务错误码由具体错误决定",
    )


def configure_openapi_responses(
    operations: Iterable[OperationBuilder] | None = None,
) -> None:
    """Register shared schemas and normalize all documented operations."""

    specification = SpecificationBuilder()
    if not specification.has_component("schemas", SUCCESS_RESPONSE_SCHEMA_NAME):
        specification.add_component(
            "schemas",
            SUCCESS_RESPONSE_SCHEMA_NAME,
            deepcopy(SUCCESS_RESPONSE_SCHEMA),
        )
    if not specification.has_component("schemas", ERROR_RESPONSE_SCHEMA_NAME):
        specification.add_component(
            "schemas",
            ERROR_RESPONSE_SCHEMA_NAME,
            deepcopy(ERROR_RESPONSE_SCHEMA),
        )

    operation_values = (
        operations if operations is not None else OperationStore().values()
    )
    for operation in operation_values:
        ensure_openapi_operation_responses(operation)
