# coding=utf-8

from sanic_ext.extensions.openapi.builders import (
    OperationBuilder,
    OperationStore,
    SpecificationBuilder,
)

from core.openapi import (
    ERROR_RESPONSE_SCHEMA_NAME,
    SUCCESS_RESPONSE_SCHEMA_NAME,
    configure_openapi_responses,
    ensure_openapi_operation_responses,
)


def _legacy_operation(response: dict) -> OperationBuilder:
    operation = OperationBuilder()
    operation.response("default", response)
    return operation


def test_legacy_status_response_is_normalized():
    operation = _legacy_operation(
        {
            "200": {
                "description": "查询成功",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"code": {"type": "integer"}},
                        }
                    }
                },
            },
            "400": {
                "description": "参数错误",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                        }
                    }
                },
            },
        }
    )

    ensure_openapi_operation_responses(operation)

    responses = operation.build().serialize()["responses"]
    assert set(responses) == {"200", "400"}
    assert responses["200"]["description"] == "查询成功"
    assert responses["200"]["content"]["application/json"]["schema"]["type"] == "object"
    assert responses["400"]["description"] == "参数错误"


def test_legacy_stream_and_redirect_responses_keep_their_media_contracts():
    stream_operation = _legacy_operation(
        {
            "200": {
                "description": "执行成功",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        }
    )
    redirect_operation = _legacy_operation({"302": {"description": "重定向到登录页面"}})

    ensure_openapi_operation_responses(stream_operation)
    ensure_openapi_operation_responses(redirect_operation)

    stream_response = stream_operation.build().serialize()["responses"]["200"]
    redirect_response = redirect_operation.build().serialize()["responses"]["302"]
    assert set(stream_response["content"]) == {"text/event-stream"}
    assert stream_response["content"]["text/event-stream"]["schema"] == {
        "type": "string"
    }
    assert redirect_response == {"description": "重定向到登录页面"}


def test_legacy_direct_media_type_response_is_normalized():
    operation = _legacy_operation(
        {
            "200": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"data": {"type": "boolean"}},
                    }
                }
            }
        }
    )

    ensure_openapi_operation_responses(operation)

    response = operation.build().serialize()["responses"]["200"]
    assert response["content"]["application/json"]["schema"]["properties"] == {
        "data": {"type": "boolean"}
    }


def test_missing_response_gets_shared_success_and_error_contracts():
    operation = OperationBuilder()

    configure_openapi_responses([operation])

    responses = operation.build().serialize()["responses"]
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{SUCCESS_RESPONSE_SCHEMA_NAME}"
    }
    assert responses["default"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{ERROR_RESPONSE_SCHEMA_NAME}"
    }
    specification = SpecificationBuilder()
    assert specification.has_component("schemas", SUCCESS_RESPONSE_SCHEMA_NAME)
    assert specification.has_component("schemas", ERROR_RESPONSE_SCHEMA_NAME)


def test_correct_response_is_preserved_and_configuration_is_idempotent():
    operation = OperationBuilder()
    operation.response(
        204,
        {"application/json": {"schema": {"type": "object"}}},
        "无需返回内容",
    )
    expected_response = operation.build().serialize()["responses"][204]

    configure_openapi_responses([operation])
    configure_openapi_responses([operation])

    assert operation.build().serialize()["responses"] == {"204": expected_response}


def test_imported_operations_have_valid_response_media_types():
    import services.app_factory  # noqa: F401

    configure_openapi_responses()
    operations = {
        f"{handler.__module__}.{handler.__name__}": operation
        for handler, operation in OperationStore().items()
    }

    assert all(
        operation.responses or operation._exclude for operation in operations.values()
    )
    for operation in operations.values():
        for response in operation.responses.values():
            assert not any(
                str(media_type).isdigit()
                for media_type in (response.fields.get("content") or {})
            )

    approval_response = (
        operations["services.approvement.views.all_approvement"]
        .build()
        .serialize()["responses"]["200"]
    )
    approval_data_schema = approval_response["content"]["application/json"]["schema"][
        "properties"
    ]["data"]
    assert approval_data_schema["properties"]["total"]["type"] == "integer"

    stream_response = (
        operations["services.workflow.views.execute_action"]
        .build()
        .serialize()["responses"]["200"]
    )
    assert set(stream_response["content"]) == {"text/event-stream"}
