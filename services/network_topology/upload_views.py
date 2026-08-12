"""Thin HTTP routes for topology chunked-upload start and chunk calls."""

from __future__ import annotations

import json as stdlib_json

import orjson
from sanic import Blueprint
from sanic import json as sanic_json
from sanic.request import Request
from sanic_ext import openapi

from core.response import response_ok
from libs.security.authorization import requires_permission
from services.network_topology import upload

upload_bp = Blueprint(
    "network_topology_upload",
    url_prefix="/network-topology/config-sync/upload",
)
__all__ = ["upload_bp"]

_MAX_START_BYTES = 1024 * 1024
_MAX_CHUNK_BYTES = 512 * 1024
_MAX_COMMIT_BYTES = 16 * 1024


class UploadRequestError(ValueError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__("Invalid upload request")


@upload_bp.post("/start")
@openapi.definition(summary="创建网络拓扑分片上传会话")
@requires_permission("network_topology:sync")
async def start_upload(request: Request):
    try:
        payload = _request_data(request, _MAX_START_BYTES)
        authority = _start_authority(request, payload)
        result = await upload.start_upload(authority, payload)
    except UploadRequestError as exc:
        return _request_error(exc.status)
    except upload.UploadError as exc:
        return _upload_error(exc)
    return response_ok(result)


@upload_bp.post("/chunk")
@openapi.definition(summary="上传一个完整网络拓扑 observation")
@requires_permission("network_topology:sync")
async def stage_chunk(request: Request):
    try:
        payload = _request_data(request, _MAX_CHUNK_BYTES)
        result = await upload.stage_chunk(_owner(request), payload)
    except UploadRequestError as exc:
        return _request_error(exc.status)
    except upload.UploadError as exc:
        return _upload_error(exc)
    return response_ok(result)


@upload_bp.post("/commit")
@openapi.definition(summary="提交网络拓扑分片上传会话")
@requires_permission("network_topology:sync")
async def commit_upload(request: Request):
    try:
        payload = upload.validated_commit_request(
            _request_data(request, _MAX_COMMIT_BYTES)
        )
        result = await upload.commit_upload_session(
            _owner(request),
            payload["upload_session_id"],
            expected_payload_hash=payload["client_expected_input_hash"],
        )
        data = upload.validated_commit_response(result)
    except UploadRequestError as exc:
        return _request_error(exc.status)
    except upload.UploadError as exc:
        return _upload_error(exc)
    except Exception:
        return _unavailable_error()
    return sanic_json({"code": 0, "data": data}, status=202, dumps=orjson.dumps)


def _start_authority(request: Request, payload: dict) -> upload.UploadAuthority:
    session = getattr(request.ctx, "session", None)
    try:
        tenant_id, subject = session["tenant_id"], str(session["sub"])
    except (KeyError, TypeError):
        raise UploadRequestError(403) from None
    envelope = payload.get("snapshot_envelope_without_observations", {})
    collector = envelope.get("collector", {}) if isinstance(envelope, dict) else {}
    return upload.UploadAuthority(
        tenant_id=tenant_id,
        topology_id=payload.get("id"),
        subject=subject,
        playbook_id=collector.get("playbook_id"),
        run_id=collector.get("run_id"),
    )


def _owner(request: Request) -> upload.UploadOwner:
    session = getattr(request.ctx, "session", None)
    try:
        return upload.UploadOwner(
            tenant_id=session["tenant_id"], subject=str(session["sub"])
        )
    except (KeyError, TypeError):
        raise UploadRequestError(403) from None


def _request_data(request: Request, maximum: int) -> dict:
    if getattr(request, "query_string", ""):
        raise UploadRequestError(400)
    if _header_values(request, "content-type") != ("application/json",):
        raise UploadRequestError(415)
    body = getattr(request, "body", None)
    if type(body) is not bytes or not body:
        raise UploadRequestError(400)
    if len(body) > maximum:
        raise UploadRequestError(413)
    return _decode_object(body)


def _decode_object(body: bytes) -> dict:
    try:
        value = stdlib_json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError):
        raise UploadRequestError(400) from None
    if type(value) is not dict:
        raise UploadRequestError(400)
    return value


def _unique_object(pairs) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise ValueError("Invalid JSON constant")


def _header_values(request: Request, name: str) -> tuple[object, ...]:
    headers = getattr(request, "headers", None)
    getall = getattr(headers, "getall", None)
    if callable(getall):
        try:
            values = getall(name)
        except (KeyError, TypeError):
            values = ()
        if values:
            return tuple(values)
    get = getattr(headers, "get", None)
    if not callable(get):
        return ()
    value = get(name)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return () if value is None else (value,)


def _upload_error(error: upload.UploadError):
    payload = {
        "code": 4000,
        "message": error.public_message,
        "data": {"error_code": error.code, "retryable": error.status >= 500},
    }
    return sanic_json(payload, status=error.status, dumps=orjson.dumps)


def _request_error(status: int):
    payload = {
        "code": 4000,
        "message": "上传请求不合法",
        "data": {"error_code": upload.SCHEMA_INVALID, "retryable": False},
    }
    return sanic_json(payload, status=status, dumps=orjson.dumps)


def _unavailable_error():
    error = upload.UploadError(upload.UPLOAD_UNAVAILABLE)
    return _upload_error(error)
