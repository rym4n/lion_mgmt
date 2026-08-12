"""HTTP adapter for explicit legacy-to-v2 upgrade intents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sanic.request import Request

import settings
from core.response import response_ok
from services.network_topology.contracts import ContractError, validate_contract
from services.network_topology.legacy import (
    UPGRADE_INTENT_INVALID,
    UPGRADE_LEGACY_STATE_INVALID,
    UPGRADE_NOT_REQUIRED,
    UPGRADE_PERMISSION_DENIED,
    UPGRADE_REQUEST_INVALID,
    UPGRADE_REVISION_CONFLICT,
    UPGRADE_TOPOLOGY_NOT_FOUND,
    LegacyUpgradeError,
    UpgradeAuthority,
    UpgradeIntentRequest,
    create_upgrade_intent,
)
from services.network_topology.view_helpers import (
    _actor_id,
    _err,
    _request_data,
    _tenant_id,
)

UPGRADE_REQUEST = "aioe.network-topology.api/upgrade-request/v1"
UPGRADE_RESPONSE = "aioe.network-topology.api/upgrade-response/v1"
UPGRADE_PERMISSION = "network_topology:u"
UPGRADE_INTENT_TTL = timedelta(minutes=30)


async def upgrade_to_v2(request: Request, *, repository=None, clock=None):
    if not settings.TOPOLOGY_V2_INGRESS_ENABLED:
        return _err("v2 拓扑同步入口未启用", status=503)
    now = _read_now(clock)
    try:
        data = _request_data(request)
        validate_contract(data, UPGRADE_REQUEST)
        authority = UpgradeAuthority(
            _tenant_id(request), _actor_id(request), frozenset({UPGRADE_PERMISSION})
        )
        intent_request = UpgradeIntentRequest(
            data["id"], data["expected_legacy_revision"], now + UPGRADE_INTENT_TTL
        )
        intent = await create_upgrade_intent(
            authority, intent_request, repository, clock=lambda: now
        )
        result = _intent_result(intent)
        validate_contract({"code": 0, "data": result}, UPGRADE_RESPONSE)
    except (ContractError, ValueError) as exc:
        return _err(str(exc))
    except LegacyUpgradeError as exc:
        return _legacy_error(exc)
    return response_ok(result)


def _read_now(clock):
    value = clock() if clock else datetime.now(UTC).replace(microsecond=0)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("升级时钟无效")
    return value.astimezone(UTC)


def _intent_result(intent):
    return {
        "upgrade_intent_id": str(intent.id),
        "status": intent.status,
        "expected_legacy_revision": intent.expected_legacy_revision,
        "expires_at": _rfc3339(intent.expires_at),
    }


def _rfc3339(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("升级意图过期时间无效")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _legacy_error(error):
    responses = {
        UPGRADE_PERMISSION_DENIED: ("缺少网络拓扑编辑权限", 403),
        UPGRADE_REQUEST_INVALID: ("升级请求不合法", 400),
        UPGRADE_TOPOLOGY_NOT_FOUND: ("拓扑数据不存在", 404),
        UPGRADE_LEGACY_STATE_INVALID: ("拓扑不是可升级的 legacy 状态", 409),
        UPGRADE_NOT_REQUIRED: ("拓扑无需升级", 409),
        UPGRADE_REVISION_CONFLICT: ("legacy 拓扑版本已变化", 409),
        UPGRADE_INTENT_INVALID: ("升级意图状态冲突", 409),
    }
    message, status = responses.get(error.code, ("拓扑升级失败", 500))
    return _err(message, status=status)
