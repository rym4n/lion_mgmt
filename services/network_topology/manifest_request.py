"""Strict HTTP request parsing and public errors for topology manifests."""

import json as stdlib_json

import orjson
from sanic import json as sanic_json
from sanic.request import Request

from services.network_topology import analysis_runs
from services.network_topology import manifest as topology_manifest


_MAX_MANIFEST_REQUEST_BYTES = 16 * 1024


class _ManifestRequestError(ValueError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__("Invalid manifest request")


def _manifest_pairs(pairs) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str):
    raise ValueError("Invalid JSON constant")


def _manifest_header_values(request: Request, name: str) -> tuple[object, ...]:
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


def _manifest_request_data(request: Request) -> dict:
    if getattr(request, "query_string", ""):
        raise _ManifestRequestError(400)
    if _manifest_header_values(request, "content-type") != ("application/json",):
        raise _ManifestRequestError(415)
    body = getattr(request, "body", None)
    if type(body) is not bytes or not body:
        raise _ManifestRequestError(400)
    if len(body) > _MAX_MANIFEST_REQUEST_BYTES:
        raise _ManifestRequestError(413)
    try:
        data = stdlib_json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_manifest_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError):
        raise _ManifestRequestError(400) from None
    if type(data) is not dict:
        raise _ManifestRequestError(400)
    return data


def _manifest_error_response(
    error: topology_manifest.ManifestError, *, status: int | None = None
):
    response_status = error.status if status is None else status
    payload = {
        "code": 4000,
        "message": error.public_message,
        "data": {
            "error_code": error.code,
            "retryable": response_status >= 500,
        },
    }
    return sanic_json(payload, status=response_status, dumps=orjson.dumps)


def _analysis_error_response(error: analysis_runs.AnalysisRunError):
    payload = {
        "code": 4000,
        "message": error.public_message,
        "data": {"error_code": error.code, "retryable": error.retryable},
    }
    return sanic_json(payload, status=error.status, dumps=orjson.dumps)
