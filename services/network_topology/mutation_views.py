"""HTTP adapters for topology profile, override, and layout mutations."""

from __future__ import annotations

from sanic.request import Request

import settings
from core.response import response_ok
from services.network_topology.contracts import ContractError, validate_contract
from services.network_topology.layouts import (
    LayoutAuthority,
    LayoutError,
    save_scope_layout,
)
from services.network_topology.query_service import (
    QueryAuthority,
    QueryError,
    save_override,
    save_profile,
)
from services.network_topology.view_helpers import (
    _actor_id,
    _err,
    _request_data,
    _tenant_id,
)

LAYOUT_REQUEST = "aioe.network-topology.api/layout-request/v2"
LAYOUT_RESPONSE = "aioe.network-topology.api/layout-response/v2"


async def save_topology_layout(request: Request):
    if not settings.TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED:
        return _err("v2 拓扑展示写入未启用", status=503)
    try:
        data = _request_data(request)
        validate_contract(data, LAYOUT_REQUEST)
        authority = LayoutAuthority(
            _tenant_id(request), data["id"], _actor_id(request)
        )
        saved = await save_scope_layout(authority, data)
        result = _layout_result(saved)
        validate_contract({"code": 0, "data": result}, LAYOUT_RESPONSE)
    except ContractError as exc:
        return _err(str(exc))
    except LayoutError as exc:
        return _err(exc.public_message, status=exc.status)
    except ValueError as exc:
        return _err(str(exc))
    return response_ok(result)


async def save_topology_profile(request: Request):
    try:
        data = _request_data(request)
        result = await save_profile(_authority(request, data), data)
    except ContractError as exc:
        return _err(str(exc))
    except (QueryError, ValueError) as exc:
        return _query_error(exc)
    return response_ok(result)


async def save_topology_override(request: Request):
    if not settings.TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED:
        return _err("v2 拓扑展示写入未启用", status=503)
    try:
        data = _request_data(request)
        result = await save_override(_authority(request, data), data)
    except (QueryError, ValueError) as exc:
        return _query_error(exc)
    return response_ok(result)


def is_v2_layout_request(data) -> bool:
    return isinstance(data, dict) and (
        "scope_id" in data or "expected_scope_layout_revision" in data
    )


def _layout_result(saved):
    return {
        "scope_layout_revision": saved.scope_layout_revision,
        "layout_set_revision": saved.layout_set_revision,
        "graph_revision": saved.graph_revision,
    }


def _authority(request, data):
    topology_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(topology_id, str) or not topology_id:
        raise QueryError("拓扑ID不能为空")
    return QueryAuthority(_tenant_id(request), topology_id, _actor_id(request))


def _query_error(error):
    if isinstance(error, QueryError):
        return _err(error.message, status=error.status)
    return _err(str(error))
