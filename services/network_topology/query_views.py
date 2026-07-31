"""HTTP adapters for scoped topology queries."""

from __future__ import annotations

from sanic.request import Request

import settings
from core.response import response_ok
from services.network_topology.contracts import ContractError
from services.network_topology.overflow import OverflowTokenError
from services.network_topology.query_service import (
    QueryAuthority,
    QueryError,
    evidence_query,
    scoped_view,
    search_graph,
)
from services.network_topology.view_helpers import (
    _actor_id,
    _err,
    _request_data,
    _tenant_id,
)


async def view_topology_scope(request: Request):
    if not settings.TOPOLOGY_V2_VIEW_ENABLED:
        return _err("v2 拓扑查看入口未启用", status=503)
    try:
        data = _request_data(request)
        authority = _authority(request, data)
        result = await scoped_view(authority, data)
    except (ContractError, OverflowTokenError, ValueError) as exc:
        return _err(str(exc))
    except QueryError as exc:
        return _err(exc.message, status=exc.status)
    return response_ok(result)


async def search_topology(request: Request):
    if not settings.TOPOLOGY_V2_VIEW_ENABLED:
        return _err("v2 拓扑查看入口未启用", status=503)
    try:
        data = _request_data(request)
        result = await search_graph(_authority(request, data), data)
    except (QueryError, ValueError) as exc:
        return _query_error(exc)
    return response_ok(result)


async def get_topology_evidence(request: Request):
    if not settings.TOPOLOGY_V2_VIEW_ENABLED:
        return _err("v2 拓扑查看入口未启用", status=503)
    try:
        data = _request_data(request)
        result = await evidence_query(_authority(request, data), data)
    except (QueryError, ValueError) as exc:
        return _query_error(exc)
    return response_ok(result)


def _authority(request, data):
    topology_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(topology_id, str) or not topology_id:
        raise QueryError("拓扑ID不能为空")
    return QueryAuthority(_tenant_id(request), topology_id, _actor_id(request))


def _query_error(error):
    if isinstance(error, QueryError):
        return _err(error.message, status=error.status)
    return _err(str(error))
