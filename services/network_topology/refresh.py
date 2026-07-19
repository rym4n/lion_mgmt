# coding=utf-8
"""Configuration-driven network topology refresh orchestration."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import math
import os
from typing import Any
import uuid

import orjson
from sanic.log import logger
from tortoise import connections
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from libs.kafka import aproduce
from libs.security.authorization import (
    check_action_execute_permission,
    tool_requires_resource,
)
from libs.utils import decrypt_aes256_gcm, encrypt_aes256_gcm
from models import (
    Account,
    NetworkTopology,
    Runlog,
    Tenant,
    Tool,
    ToolAction,
    ToolResources,
)
from services.network_topology.analyzer import analyze_topology
from settings import TASK_TOPIC, TZ

__all__ = [
    "RefreshClaim",
    "TopologyRefreshError",
    "claim_due_topology",
    "claim_due_topologies",
    "execute_claimed_refresh",
    "is_topology_collection_action",
    "migrate_legacy_topology_secrets",
    "normalize_source_config",
    "queue_topology_refresh",
    "recover_stale_topologies",
    "refresh_topology",
    "sweep_due_topologies",
    "validate_action_sources",
]


REFRESH_STATUS_PENDING = "pending"
REFRESH_STATUS_RUNNING = "running"
REFRESH_STATUS_SUCCESS = "success"
REFRESH_STATUS_FAILED = "failed"

_TERMINAL_RUNLOG_STATUSES = {-2, -1, 0, 2, 3}
_SUCCESS_RUNLOG_STATUS = 0
_IN_PROGRESS_RESULT = "执行中"
_DEFAULT_REFRESH_INTERVAL_SECONDS = 3600
_MIN_REFRESH_INTERVAL_SECONDS = 30
_POLL_INTERVAL_SECONDS = max(
    0.1, float(os.environ.get("RY_NETWORK_TOPOLOGY_POLL_INTERVAL_SECONDS") or 1)
)
_LEASE_SECONDS = max(
    60, int(os.environ.get("RY_NETWORK_TOPOLOGY_REFRESH_LEASE_SECONDS") or 1800)
)
_LEASE_HEARTBEAT_SECONDS = max(5, min(60, _LEASE_SECONDS // 3))
_SOURCE_CONCURRENCY = max(
    1, min(16, int(os.environ.get("RY_NETWORK_TOPOLOGY_SOURCE_CONCURRENCY") or 4))
)
_MAX_SOURCE_CONFIG_BYTES = max(
    1024,
    int(
        os.environ.get("RY_NETWORK_TOPOLOGY_MAX_SOURCE_CONFIG_BYTES") or 5 * 1024 * 1024
    ),
)
_SECRET_PLACEHOLDER = "********"
_ENCRYPTED_SECRET_PREFIX = "enc::"
_ENCRYPTED_RESULT_PREFIX = "enc-result::"
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "community",
    "credential",
    "private_key",
    "private-key",
    "privatekey",
    "api_key",
    "apikey",
)


class TopologyRefreshError(ValueError):
    """A topology source definition or refresh run is invalid."""

    def __init__(self, message: str, *, run_ids: list[str] | None = None):
        super().__init__(message)
        self.run_ids = list(run_ids or [])


def is_topology_collection_action(action_data: Any) -> bool:
    """Only explicitly classified query actions may run on a refresh schedule."""
    return (
        isinstance(action_data, dict)
        and str(action_data.get("classify") or "").strip().lower() == "query"
    )


@dataclass(frozen=True)
class RefreshClaim:
    topology_id: str
    tenant_id: str
    revision: int
    refresh_started_at: datetime
    refresh_token: str


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace(" ", "_")
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def _action_secret_keys(
    action_data: dict, action_input: dict | None = None
) -> set[str]:
    secret_keys = {str(key) for key in (action_input or {}) if _is_secret_key(str(key))}
    for definition in action_data.get("input") or []:
        if not isinstance(definition, dict):
            continue
        key = str(definition.get("key") or "").strip()
        input_type = str(
            definition.get("type") or definition.get("data_type") or ""
        ).lower()
        if key and (input_type == "password" or _is_secret_key(key)):
            secret_keys.add(key)
    return secret_keys


def _encrypt_action_secret(value: Any) -> str:
    if isinstance(value, str) and value.startswith(_ENCRYPTED_SECRET_PREFIX):
        return value
    serialized = orjson.dumps(value).decode("utf-8")
    return f"{_ENCRYPTED_SECRET_PREFIX}{encrypt_aes256_gcm(serialized)}"


def _decrypt_action_secret(value: Any, *, index: int, key: str) -> Any:
    if not isinstance(value, str) or not value.startswith(_ENCRYPTED_SECRET_PREFIX):
        return value
    try:
        plaintext = decrypt_aes256_gcm(value[len(_ENCRYPTED_SECRET_PREFIX) :])
        return orjson.loads(plaintext)
    except (TypeError, ValueError, orjson.JSONDecodeError) as exc:
        raise TopologyRefreshError(
            f"第 {index + 1} 个动作来源的敏感参数 {key} 无法解密"
        ) from exc


def _decrypt_action_result(value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith(_ENCRYPTED_RESULT_PREFIX):
        return value
    try:
        plaintext = decrypt_aes256_gcm(value[len(_ENCRYPTED_RESULT_PREFIX) :])
        return orjson.loads(plaintext)
    except (TypeError, ValueError, orjson.JSONDecodeError) as exc:
        raise TopologyRefreshError("动作采集结果无法解密") from exc


def _matching_existing_source(
    existing_action_sources: list | None,
    normalized: dict,
    index: int,
) -> dict:
    normalized_resources = [str(item) for item in normalized["resource_ids"]]
    if index < len(existing_action_sources or []):
        source = (existing_action_sources or [])[index]
        if (
            isinstance(source, dict)
            and str(source.get("action_id") or "") == normalized["action_id"]
            and [str(item) for item in source.get("resource_ids") or []]
            == normalized_resources
        ):
            return source
    candidates = [
        source
        for source in existing_action_sources or []
        if isinstance(source, dict)
        and str(source.get("action_id") or "") == normalized["action_id"]
    ]
    for source in candidates:
        if [
            str(item) for item in source.get("resource_ids") or []
        ] == normalized_resources:
            return source
    return {}


def _normalize_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def normalize_source_config(value: Any) -> Any:
    """Normalize action output so semantically identical configuration hashes equally."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = _normalize_text(value)
        if text[:1] in {"{", "["}:
            try:
                return normalize_source_config(orjson.loads(text))
            except orjson.JSONDecodeError:
                pass
        return text
    if isinstance(value, dict):
        return {
            str(key): normalize_source_config(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [normalize_source_config(item) for item in value]
    if isinstance(value, set):
        normalized = [normalize_source_config(item) for item in value]
        return sorted(normalized, key=lambda item: _canonical_json(item))
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _normalize_text(str(value))


def _canonical_json(value: Any) -> bytes:
    return orjson.dumps(normalize_source_config(value), option=orjson.OPT_SORT_KEYS)


def _semantic_source_config(value: Any) -> Any:
    """Remove execution identifiers while retaining source/resource boundaries."""
    normalized = normalize_source_config(value)
    if not isinstance(normalized, dict):
        return normalized
    semantic = {
        key: item
        for key, item in normalized.items()
        if key not in {"run_id", "run_ids", "runlog_id"}
    }
    semantic_sources = []
    for source in normalized.get("sources") or []:
        if not isinstance(source, dict):
            semantic_sources.append(source)
            continue
        semantic_source = {
            key: item
            for key, item in source.items()
            if key not in {"run_id", "run_ids"}
        }
        semantic_resources = []
        for resource in source.get("resources") or []:
            if isinstance(resource, dict):
                semantic_resources.append(
                    {
                        key: item
                        for key, item in resource.items()
                        if key not in {"run_id", "runlog_id"}
                    }
                )
            else:
                semantic_resources.append(resource)
        semantic_source["resources"] = semantic_resources
        semantic_sources.append(semantic_source)
    semantic["sources"] = semantic_sources
    return semantic


def _source_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(_semantic_source_config(value))).hexdigest()


def _safe_positive_int(value: Any, default: int, *, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    result = max(1, result)
    return min(result, maximum) if maximum else result


def _normalize_action_source(source: Any, index: int) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise TopologyRefreshError(f"第 {index + 1} 个动作来源格式错误")

    action_id = str(source.get("action_id") or "").strip()
    if not action_id:
        raise TopologyRefreshError(f"第 {index + 1} 个动作来源缺少 action_id")

    resource_ids = source.get("resource_ids")
    if resource_ids is None:
        resource_ids = []
    if not isinstance(resource_ids, (list, tuple)):
        raise TopologyRefreshError(
            f"第 {index + 1} 个动作来源的 resource_ids 必须是数组"
        )
    normalized_resource_ids = []
    for resource_id in resource_ids:
        resource_id = str(resource_id or "").strip()
        if resource_id and resource_id not in normalized_resource_ids:
            normalized_resource_ids.append(resource_id)

    action_input = source.get("action_input")
    if action_input is None:
        action_input = {}
    if not isinstance(action_input, dict):
        raise TopologyRefreshError(
            f"第 {index + 1} 个动作来源的 action_input 必须是对象"
        )

    return {
        "action_id": action_id,
        "resource_ids": normalized_resource_ids,
        "action_input": normalize_source_config(action_input),
    }


def _required_action_input(action_data: dict, action_input: dict, index: int) -> dict:
    normalized_input = dict(action_input)
    input_definitions = action_data.get("input") or []
    if not isinstance(input_definitions, list):
        raise TopologyRefreshError(f"第 {index + 1} 个动作来源的参数定义格式错误")

    for definition in input_definitions:
        if not isinstance(definition, dict):
            continue
        key = str(definition.get("key") or "").strip()
        if not key:
            continue
        default_value = definition.get("default")
        if default_value in (None, ""):
            default_value = definition.get("value")
        if key not in normalized_input and default_value not in (None, ""):
            normalized_input[key] = normalize_source_config(default_value)

        required = definition.get("required") is True or str(
            definition.get("required") or ""
        ).lower() in {
            "1",
            "true",
            "yes",
        }
        value = normalized_input.get(key)
        missing_value = (
            value is None
            or (isinstance(value, str) and not value.strip())
            or value == []
            or value == {}
        )
        if required and missing_value:
            label = str(definition.get("description") or definition.get("name") or key)
            raise TopologyRefreshError(
                f"第 {index + 1} 个动作来源缺少必填参数: {label}"
            )
    return normalized_input


async def _resolve_action_sources(
    action_sources,
    tenant_id: str,
    account,
    *,
    existing_action_sources: list | None = None,
) -> tuple[list[dict], list[dict]]:
    if action_sources is None:
        action_sources = []
    if not isinstance(action_sources, list):
        raise TopologyRefreshError("action_sources 必须是数组")
    if not tenant_id:
        raise TopologyRefreshError("缺少租户信息")
    if not account or not getattr(account, "id", None):
        raise TopologyRefreshError("拓扑刷新执行账号不存在")

    normalized_sources = []
    resolved_sources = []
    for index, source in enumerate(action_sources):
        normalized = _normalize_action_source(source, index)
        action = await ToolAction.get_or_none(
            id=normalized["action_id"],
            tenant_id=tenant_id,
            deleted=False,
        )
        if not action or not isinstance(action.action, dict):
            raise TopologyRefreshError(f"第 {index + 1} 个动作来源不存在或已删除")
        if not is_topology_collection_action(action.action):
            raise TopologyRefreshError(
                f"第 {index + 1} 个动作来源不是只读查询动作，禁止用于周期采集"
            )

        action_func = str(action.action.get("func") or "").strip()
        action_name = str(action.action.get("name") or action_func).strip()
        if not action_func:
            raise TopologyRefreshError(f"第 {index + 1} 个动作来源缺少动作标识")

        existing_source = _matching_existing_source(
            existing_action_sources,
            normalized,
            index,
        )
        existing_input = existing_source.get("action_input") or {}
        if not isinstance(existing_input, dict):
            existing_input = {}
        secret_keys = _action_secret_keys(action.action, normalized["action_input"])
        secret_keys.update(
            str(key)
            for key in existing_source.get("secret_keys") or []
            if str(key).strip()
        )
        secret_keys.update(
            str(key)
            for key, value in normalized["action_input"].items()
            if isinstance(value, str) and value.startswith(_ENCRYPTED_SECRET_PREFIX)
        )

        execution_input = dict(normalized["action_input"])
        for key in secret_keys:
            if key not in execution_input:
                continue
            value = execution_input.get(key)
            if value == _SECRET_PLACEHOLDER:
                if key not in existing_input:
                    raise TopologyRefreshError(
                        f"第 {index + 1} 个动作来源的敏感参数 {key} 需要重新填写"
                    )
                value = existing_input[key]
            execution_input[key] = _decrypt_action_secret(
                value,
                index=index,
                key=key,
            )

        execution_input = _required_action_input(
            action.action,
            execution_input,
            index,
        )
        persisted_input = dict(execution_input)
        for key in secret_keys:
            value = persisted_input.get(key)
            if value not in (None, "", [], {}):
                persisted_input[key] = _encrypt_action_secret(value)
        persisted_definition = {
            **normalized,
            "action_input": persisted_input,
        }
        if secret_keys:
            persisted_definition["secret_keys"] = sorted(secret_keys)
        execution_definition = {
            **normalized,
            "action_input": execution_input,
        }

        tool = await Tool.get_or_none(
            name=action.tool_name,
            version=action.version_number,
            tenant_id=tenant_id,
            deleted=False,
            maintain=False,
        )
        if not tool or not tool.main:
            raise TopologyRefreshError(
                f"第 {index + 1} 个动作来源对应的应用不存在或不可执行"
            )

        resource_ids = normalized["resource_ids"]
        if not resource_ids and await tool_requires_resource(tool.name, tenant_id):
            raise TopologyRefreshError(f"第 {index + 1} 个动作来源必须选择至少一个资源")
        resources = []
        if resource_ids:
            resource_rows = (
                await ToolResources.filter(
                    id__in=resource_ids,
                    tool_name=tool.name,
                    tenant_id=tenant_id,
                    deleted=False,
                    internal=False,
                )
                .only("id", "tool_name", "name")
                .all()
            )
            resource_by_id = {str(resource.id): resource for resource in resource_rows}
            missing = [
                resource_id
                for resource_id in resource_ids
                if resource_id not in resource_by_id
            ]
            if missing:
                raise TopologyRefreshError(
                    f"第 {index + 1} 个动作来源包含不存在或不属于该应用的资源: {', '.join(missing)}"
                )
            resources = [resource_by_id[resource_id] for resource_id in resource_ids]

        resource_arg = ",".join(resource_ids)
        if not await check_action_execute_permission(
            account,
            tool.name,
            action_func,
            resource_arg,
            tenant_id,
        ):
            raise TopologyRefreshError(f"执行账号无权执行第 {index + 1} 个动作来源")

        normalized_sources.append(
            {
                **persisted_definition,
                "tool_name": str(tool.name or ""),
                "tool_version": str(tool.version or ""),
                "tool_label": str(tool.description or tool.name or ""),
                "action_name": action_name,
                "action_func": action_func,
                "resource_names": [str(resource.name or "") for resource in resources],
            }
        )
        resolved_sources.append(
            {
                "index": index,
                "definition": execution_definition,
                "persisted_definition": persisted_definition,
                "secret_keys": sorted(secret_keys),
                "action": action,
                "action_func": action_func,
                "action_name": action_name,
                "tool": tool,
                "resources": resources,
            }
        )

    return normalized_sources, resolved_sources


async def validate_action_sources(
    action_sources,
    tenant_id: str,
    account,
    *,
    existing_action_sources: list | None = None,
) -> list[dict]:
    """Validate tenant ownership and execution permission, returning persistable data."""
    normalized, _resolved = await _resolve_action_sources(
        action_sources,
        tenant_id,
        account,
        existing_action_sources=existing_action_sources,
    )
    return normalized


def _encrypt_legacy_action_source(
    source: dict,
    action_data: dict | None,
) -> tuple[dict, bool]:
    migrated = dict(source)
    action_input = source.get("action_input")
    if not isinstance(action_input, dict):
        return migrated, False

    secret_keys = _action_secret_keys(action_data or {}, action_input)
    if action_data is None:
        secret_keys.update(str(key) for key in action_input)
    secret_keys.update(
        str(key) for key in source.get("secret_keys") or [] if str(key).strip()
    )
    secret_keys.update(
        str(key)
        for key, value in action_input.items()
        if "credential" in str(key).strip().lower().replace(" ", "_")
        or "privatekey" in str(key).strip().lower().replace(" ", "_")
        or (isinstance(value, str) and value.startswith(_ENCRYPTED_SECRET_PREFIX))
    )
    migrated_input = dict(action_input)
    changed = False
    for key in secret_keys:
        value = migrated_input.get(key)
        if value in (None, "", [], {}) or value == _SECRET_PLACEHOLDER:
            continue
        if not (isinstance(value, str) and value.startswith(_ENCRYPTED_SECRET_PREFIX)):
            migrated_input[key] = _encrypt_action_secret(value)
            changed = True
    if secret_keys and sorted(source.get("secret_keys") or []) != sorted(secret_keys):
        migrated["secret_keys"] = sorted(secret_keys)
        changed = True
    if changed:
        migrated["action_input"] = migrated_input
    return migrated, changed


async def migrate_legacy_topology_secrets() -> dict[str, int]:
    """Idempotently encrypt legacy raw configs and sensitive action parameters."""
    connection = connections.get("default")
    raw_rows = await connection.execute_query_dict(
        """
        SELECT "id", "source_config"
        FROM "network_topology"
        WHERE "source_config" IS NOT NULL
          AND "source_config" <> ''
        """
    )
    encrypted_configs = 0
    for row in raw_rows:
        raw_value = row.get("source_config")
        if not isinstance(raw_value, str) or not raw_value:
            continue
        try:
            decrypt_aes256_gcm(raw_value)
            continue
        except ValueError:
            encrypted_value = encrypt_aes256_gcm(raw_value)
        updated, _ = await connection.execute_query(
            """
            UPDATE "network_topology"
            SET "source_config" = $1
            WHERE "id" = $2 AND "source_config" = $3
            """,
            (encrypted_value, row["id"], raw_value),
        )
        encrypted_configs += int(updated or 0)

    encrypted_inputs = 0
    # Security cleanup includes soft-deleted rows because their stored secrets remain data at rest.
    topologies = (
        await NetworkTopology.all()
        .only(
            "id",
            "tenant_id",
            "action_sources",
            "revision",
        )
        .all()
    )
    for topology in topologies:
        sources = topology.action_sources or []
        if not isinstance(sources, list) or not sources:
            continue
        action_ids = {
            str(source.get("action_id") or "")
            for source in sources
            if isinstance(source, dict) and source.get("action_id")
        }
        actions = (
            await ToolAction.filter(
                id__in=action_ids,
                tenant_id=topology.tenant_id,
                deleted=False,
            )
            .only("id", "action")
            .all()
        )
        action_data = {
            str(action.id): action.action if isinstance(action.action, dict) else {}
            for action in actions
        }
        migrated_sources = []
        changed = False
        for source in sources:
            if not isinstance(source, dict):
                migrated_sources.append(source)
                continue
            migrated, source_changed = _encrypt_legacy_action_source(
                source,
                action_data.get(str(source.get("action_id") or "")),
            )
            migrated_sources.append(migrated)
            changed = changed or source_changed
        if changed:
            updated = await NetworkTopology.filter(
                id=topology.id,
                tenant_id=topology.tenant_id,
                action_sources=sources,
                revision=topology.revision,
            ).update(action_sources=migrated_sources)
            encrypted_inputs += int(bool(updated))

    return {
        "source_configs": encrypted_configs,
        "action_inputs": encrypted_inputs,
    }


async def queue_topology_refresh(
    topology: NetworkTopology, force: bool = False
) -> bool:
    """Make one topology due now. Running leases are deliberately left untouched."""
    if topology.refresh_status == REFRESH_STATUS_RUNNING:
        return False
    if not topology.enabled and not force:
        return False

    now = datetime.now(TZ)
    updated = (
        await NetworkTopology.filter(
            id=topology.id,
            tenant_id=topology.tenant_id,
            revision=topology.revision,
            deleted=False,
        )
        .exclude(refresh_status=REFRESH_STATUS_RUNNING)
        .update(
            refresh_status=REFRESH_STATUS_PENDING,
            next_refresh_at=now,
            refresh_started_at=None,
            refresh_token=None,
            refresh_error="",
        )
    )
    if updated:
        topology.refresh_status = REFRESH_STATUS_PENDING
        topology.next_refresh_at = now
        topology.refresh_started_at = None
        topology.refresh_token = None
        topology.refresh_error = ""
    return bool(updated)


async def recover_stale_topologies(now: datetime | None = None) -> int:
    """Release expired leases; their old workers cannot pass the completion CAS."""
    current = now or datetime.now(TZ)
    deadline = current - timedelta(seconds=_LEASE_SECONDS)
    return await NetworkTopology.filter(
        deleted=False,
        refresh_status=REFRESH_STATUS_RUNNING,
        refresh_started_at__lt=deadline,
    ).update(
        refresh_status=REFRESH_STATUS_PENDING,
        refresh_started_at=None,
        refresh_token=None,
        next_refresh_at=current,
        refresh_error="上次刷新任务租约已过期，已自动重试",
    )


async def claim_due_topology(now: datetime | None = None) -> RefreshClaim | None:
    """Atomically claim one pending/manual or enabled/periodic topology."""
    current = now or datetime.now(TZ)
    async with in_transaction() as connection:
        due = (
            await NetworkTopology.filter(
                Q(refresh_status=REFRESH_STATUS_PENDING)
                | Q(
                    enabled=True,
                    refresh_status__in=[REFRESH_STATUS_SUCCESS, REFRESH_STATUS_FAILED],
                ),
                deleted=False,
                next_refresh_at__lte=current,
            )
            .using_db(connection)
            .select_for_update(skip_locked=True)
            .order_by("next_refresh_at", "ctime")
            .first()
        )
        if not due:
            return None

        refresh_token = str(uuid.uuid4())
        due.refresh_status = REFRESH_STATUS_RUNNING
        due.refresh_started_at = current
        due.refresh_token = refresh_token
        due.refresh_error = ""
        await due.save(
            update_fields=[
                "refresh_status",
                "refresh_started_at",
                "refresh_token",
                "refresh_error",
                "utime",
            ],
            using_db=connection,
        )
        return RefreshClaim(
            topology_id=str(due.id),
            tenant_id=str(due.tenant_id),
            revision=int(due.revision or 0),
            refresh_started_at=current,
            refresh_token=refresh_token,
        )


async def claim_due_topologies(limit: int = 4) -> list[RefreshClaim]:
    await recover_stale_topologies()
    claims = []
    for _ in range(max(1, limit)):
        claim = await claim_due_topology()
        if not claim:
            break
        claims.append(claim)
    return claims


def _action_timeout(resolved_source: dict) -> int:
    action_data = resolved_source["action"].action or {}
    # With at most 20 sources and four-way concurrency, 300 seconds keeps the
    # full collection inside the 30-minute refresh lease.
    return _safe_positive_int(action_data.get("timeout"), 300, maximum=300)


async def _wait_for_action_run(
    run_id: str, resolved_source: dict, tenant_id: str
) -> list[Runlog]:
    resource_ids = resolved_source["definition"]["resource_ids"]
    expected_logs = max(1, len(resource_ids))
    deadline = asyncio.get_running_loop().time() + _action_timeout(resolved_source)

    while True:
        logs = (
            await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False)
            .order_by("utime", "ctime", "id")
            .all()
        )
        action_logs = [
            log
            for log in logs
            if (
                getattr(log, "node_type", None) == "action-node"
                and getattr(log, "tool_name", None) == resolved_source["tool"].name
                and getattr(log, "action_name", None) == resolved_source["action_func"]
            )
        ]
        terminal_logs = [
            log
            for log in action_logs
            if getattr(log, "status", None) in _TERMINAL_RUNLOG_STATUSES
            and getattr(log, "result", None) != _IN_PROGRESS_RESULT
        ]
        failed = [
            log
            for log in terminal_logs
            if getattr(log, "status", None) != _SUCCESS_RUNLOG_STATUS
        ]
        if failed:
            raise TopologyRefreshError(
                f"动作执行失败 run_id={run_id} action={resolved_source['action_func']}"
            )
        if len(terminal_logs) >= expected_logs:
            return terminal_logs[:expected_logs]
        if asyncio.get_running_loop().time() >= deadline:
            raise TopologyRefreshError(f"动作执行超时 run_id={run_id}")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _build_action_params(resolved_source: dict, executor_id: str) -> dict:
    definition = resolved_source["definition"]
    persisted_definition = resolved_source.get("persisted_definition") or definition
    action = resolved_source["action"]
    tool = resolved_source["tool"]
    action_data = action.action or {}
    return {
        "resource": ",".join(definition["resource_ids"]),
        "node_name": resolved_source["action_name"],
        "action": resolved_source["action_func"],
        "action_name": resolved_source["action_name"],
        "app_name": tool.name,
        "name": tool.description or tool.name,
        "app_ver": tool.version,
        "main": tool.main,
        # Sensitive values remain encrypted on Kafka and are decrypted only by
        # lion_action immediately before the application call.
        "action_input": persisted_definition["action_input"],
        "encrypted_action_input_keys": list(resolved_source.get("secret_keys") or []),
        "encrypt_action_result": True,
        "retry_times": _safe_positive_int(
            action_data.get("retry_times"), 1, maximum=10
        ),
        "timeout": _action_timeout(resolved_source),
        "executor": executor_id,
    }


def _resource_results(resolved_source: dict, logs: list[Runlog]) -> list[dict]:
    resources = resolved_source["resources"]
    if not resources:
        log = logs[0]
        return [
            {
                "resource_id": "",
                "resource_name": "",
                "runlog_id": str(log.id),
                "result": normalize_source_config(_decrypt_action_result(log.result)),
            }
        ]

    unmatched = list(logs)
    results = []
    for resource in resources:
        resource_id = str(resource.id)
        matched = next(
            (
                log
                for log in unmatched
                if str(getattr(log, "resource_name", "") or "")
                in {resource_id, str(resource.name or "")}
            ),
            None,
        )
        if matched is None:
            matched = unmatched[0] if unmatched else None
        if matched is None:
            raise TopologyRefreshError(f"动作结果缺少资源 {resource_id} 的输出")
        unmatched.remove(matched)
        results.append(
            {
                "resource_id": resource_id,
                "resource_name": str(resource.name or ""),
                "runlog_id": str(matched.id),
                "result": normalize_source_config(
                    _decrypt_action_result(matched.result)
                ),
            }
        )
    return results


async def _execute_one_source(
    resolved_source: dict, tenant_id: str, executor_id: str, semaphore
) -> dict:
    async with semaphore:
        resources = resolved_source["resources"] or [None]
        run_ids = []
        resource_results = []
        for resource in resources:
            resource_ids = [str(resource.id)] if resource is not None else []
            scoped_source = {
                **resolved_source,
                "definition": {
                    **resolved_source["definition"],
                    "resource_ids": resource_ids,
                },
                "resources": [resource] if resource is not None else [],
            }
            run_id = str(uuid.uuid4())
            run_ids.append(run_id)
            params = _build_action_params(scoped_source, executor_id)
            await aproduce(
                TASK_TOPIC,
                {
                    "run_type": "action",
                    "run_id": run_id,
                    "run_args": params,
                    "tenant_id": tenant_id,
                },
            )
            try:
                logs = await _wait_for_action_run(run_id, scoped_source, tenant_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, TopologyRefreshError)
                    else "动作采集异常"
                )
                raise TopologyRefreshError(message, run_ids=run_ids) from exc
            for result in _resource_results(scoped_source, logs):
                result["run_id"] = run_id
                resource_results.append(result)
        return {
            "source_index": resolved_source["index"],
            "action_id": str(resolved_source["action"].id),
            "tool_name": str(resolved_source["tool"].name),
            "action_name": resolved_source["action_func"],
            "action_display_name": resolved_source["action_name"],
            "run_id": run_ids[0],
            "run_ids": run_ids,
            "resources": resource_results,
        }


async def _collect_source_config(
    topology: NetworkTopology, account
) -> tuple[dict, list[str]]:
    _normalized, resolved_sources = await _resolve_action_sources(
        topology.action_sources,
        topology.tenant_id,
        account,
    )
    if not resolved_sources:
        raise TopologyRefreshError("未配置网络设备采集动作")

    semaphore = asyncio.Semaphore(_SOURCE_CONCURRENCY)
    tasks = [
        asyncio.create_task(
            _execute_one_source(source, topology.tenant_id, str(account.id), semaphore),
            name=f"network_topology_source_{topology.id}_{source['index']}",
        )
        for source in resolved_sources
    ]
    try:
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    completed_run_ids = [
        run_id
        for result in gathered
        if isinstance(result, dict)
        for run_id in result.get("run_ids") or [result.get("run_id")]
        if run_id
    ]
    failures = [result for result in gathered if isinstance(result, BaseException)]
    if failures:
        failed_run_ids = [
            run_id for failure in failures for run_id in getattr(failure, "run_ids", [])
        ]
        first_failure = failures[0]
        raise TopologyRefreshError(
            str(first_failure) or first_failure.__class__.__name__,
            run_ids=completed_run_ids + failed_run_ids,
        ) from first_failure

    sources = sorted(gathered, key=lambda item: item["source_index"])
    aggregate = normalize_source_config({"schema_version": 1, "sources": sources})
    encoded = _canonical_json(aggregate)
    if len(encoded) > _MAX_SOURCE_CONFIG_BYTES:
        raise TopologyRefreshError(
            f"设备配置采集结果超过 {_MAX_SOURCE_CONFIG_BYTES // (1024 * 1024)}MB 限制"
        )
    return aggregate, [
        run_id
        for source in sources
        for run_id in source.get("run_ids") or [source.get("run_id")]
        if run_id
    ]


def _next_refresh_at(topology: NetworkTopology, now: datetime) -> datetime | None:
    if not topology.enabled:
        return None
    interval = _safe_positive_int(
        topology.refresh_interval,
        _DEFAULT_REFRESH_INTERVAL_SECONDS,
    )
    return now + timedelta(seconds=max(_MIN_REFRESH_INTERVAL_SECONDS, interval))


def _lease_filter(
    topology_id: str,
    tenant_id: str,
    expected_revision: int,
    *,
    refresh_token: str | None = None,
    refresh_started_at: datetime | None = None,
):
    query = NetworkTopology.filter(
        id=topology_id,
        tenant_id=tenant_id,
        revision=expected_revision,
        refresh_status=REFRESH_STATUS_RUNNING,
        deleted=False,
    )
    if refresh_token:
        return query.filter(refresh_token=refresh_token)
    return query.filter(refresh_started_at=refresh_started_at)


async def _heartbeat_refresh_lease(
    topology_id: str,
    tenant_id: str,
    expected_revision: int,
    refresh_token: str,
    *,
    owner_task: asyncio.Task | None = None,
    lease_lost_event: asyncio.Event | None = None,
) -> bool:
    def stop_owner() -> None:
        if lease_lost_event is not None:
            lease_lost_event.set()
        if owner_task is not None and not owner_task.done():
            owner_task.cancel()

    while True:
        await asyncio.sleep(_LEASE_HEARTBEAT_SECONDS)
        try:
            updated = await _lease_filter(
                topology_id,
                tenant_id,
                expected_revision,
                refresh_token=refresh_token,
            ).update(refresh_started_at=datetime.now(TZ))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "网络拓扑刷新租约续期失败 topology_id=%s: %s",
                topology_id,
                exc,
            )
            stop_owner()
            return False
        if not updated:
            stop_owner()
            return False


async def _await_with_refresh_lease(awaitable, heartbeat_task):
    if heartbeat_task is None:
        return await awaitable
    operation_task = asyncio.create_task(awaitable)
    try:
        done, _pending = await asyncio.wait(
            {operation_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        operation_task.cancel()
        with suppress(asyncio.CancelledError):
            await operation_task
        raise
    if heartbeat_task in done:
        operation_task.cancel()
        with suppress(asyncio.CancelledError):
            await operation_task
        raise TopologyRefreshError("拓扑刷新租约已失效，已停止设备采集")
    return await operation_task


async def _mark_refresh_failure(
    topology: NetworkTopology,
    expected_revision: int,
    refresh_started_at: datetime,
    refresh_token: str | None,
    error: BaseException,
    run_ids: list[str],
) -> bool:
    now = datetime.now(TZ)
    updated = await _lease_filter(
        topology.id,
        topology.tenant_id,
        expected_revision,
        refresh_token=refresh_token,
        refresh_started_at=refresh_started_at,
    ).update(
        last_run_ids=run_ids,
        refresh_status=REFRESH_STATUS_FAILED,
        last_refresh_at=now,
        next_refresh_at=_next_refresh_at(topology, now),
        refresh_started_at=None,
        refresh_token=None,
        refresh_error=_normalize_text(str(error) or error.__class__.__name__)[:4096],
    )
    return bool(updated)


async def _restore_cancelled_refresh(
    topology: NetworkTopology,
    expected_revision: int,
    refresh_started_at: datetime,
    refresh_token: str | None,
) -> None:
    retry_at = datetime.now(TZ) + timedelta(seconds=_MIN_REFRESH_INTERVAL_SECONDS)
    await _lease_filter(
        topology.id,
        topology.tenant_id,
        expected_revision,
        refresh_token=refresh_token,
        refresh_started_at=refresh_started_at,
    ).update(
        refresh_status=REFRESH_STATUS_PENDING,
        next_refresh_at=retry_at,
        refresh_started_at=None,
        refresh_token=None,
        refresh_error="刷新任务被取消，等待自动重试",
    )


async def refresh_topology(
    topology_id: str,
    expected_revision: int,
    refresh_started_at: datetime | None = None,
    *,
    tenant_id: str | None = None,
    refresh_token: str | None = None,
) -> dict:
    """Execute one claimed refresh and finish it with revision/lease compare-and-set."""
    query = NetworkTopology.filter(
        id=topology_id,
        revision=expected_revision,
        refresh_status=REFRESH_STATUS_RUNNING,
        deleted=False,
    )
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    if refresh_started_at is not None:
        query = query.filter(refresh_started_at=refresh_started_at)
    if refresh_token is not None:
        query = query.filter(refresh_token=refresh_token)
    topology = await query.first()
    if not topology or not topology.refresh_started_at:
        return {
            "id": topology_id,
            "status": "superseded",
            "changed": False,
            "last_run_ids": [],
        }

    lease_started_at = topology.refresh_started_at
    lease_token = refresh_token or getattr(topology, "refresh_token", None)
    heartbeat_task = None
    lease_lost_event = asyncio.Event()
    if lease_token:
        heartbeat_task = asyncio.create_task(
            _heartbeat_refresh_lease(
                str(topology.id),
                str(topology.tenant_id),
                expected_revision,
                lease_token,
                owner_task=asyncio.current_task(),
                lease_lost_event=lease_lost_event,
            ),
            name=f"network_topology_lease_{topology.id}",
        )
    run_ids: list[str] = []
    try:
        executor_id = str(topology.executor_id or "").strip()
        if not executor_id:
            raise TopologyRefreshError("未配置拓扑刷新执行账号")
        is_tenant_member = await Tenant.filter(
            id=topology.tenant_id,
            deleted=False,
            member__id=executor_id,
        ).exists()
        if not is_tenant_member:
            raise TopologyRefreshError("拓扑刷新执行账号已不属于当前租户")
        account = await Account.get_or_none(
            id=executor_id, deleted=False, disabled=False
        ).prefetch_related("roles")
        if not account:
            raise TopologyRefreshError("拓扑刷新执行账号不存在或已停用")

        aggregate, run_ids = await _await_with_refresh_lease(
            _collect_source_config(topology, account),
            heartbeat_task,
        )
        current_hash = _source_hash(aggregate)
        now = datetime.now(TZ)
        common_updates = {
            "last_run_ids": run_ids,
            "refresh_status": REFRESH_STATUS_SUCCESS,
            "last_refresh_at": now,
            "next_refresh_at": _next_refresh_at(topology, now),
            "refresh_started_at": None,
            "refresh_token": None,
            "refresh_error": "",
        }

        if current_hash == (topology.source_hash or ""):
            updated = await _lease_filter(
                topology.id,
                topology.tenant_id,
                expected_revision,
                refresh_token=lease_token,
                refresh_started_at=lease_started_at,
            ).update(**common_updates)
            status = REFRESH_STATUS_SUCCESS if updated else "superseded"
            return {
                "id": topology.id,
                "status": status,
                "changed": False,
                "last_run_ids": run_ids,
            }

        source_config = _canonical_json(aggregate).decode("utf-8")
        graph = await _await_with_refresh_lease(
            analyze_topology(
                source_config,
                tenant_id=topology.tenant_id,
                session_id=(
                    f"network-topology:{topology.id}:r{expected_revision}:"
                    f"h{current_hash[:16]}"
                ),
            ),
            heartbeat_task,
        )
        updated = await _lease_filter(
            topology.id,
            topology.tenant_id,
            expected_revision,
            refresh_token=lease_token,
            refresh_started_at=lease_started_at,
        ).update(
            source_config=source_config,
            source_hash=current_hash,
            topology_json=graph,
            **common_updates,
        )
        status = REFRESH_STATUS_SUCCESS if updated else "superseded"
        return {
            "id": topology.id,
            "status": status,
            "changed": bool(updated),
            "last_run_ids": run_ids,
        }
    except asyncio.CancelledError:
        if lease_lost_event.is_set():
            return {
                "id": topology.id,
                "status": "superseded",
                "changed": False,
                "last_run_ids": run_ids,
            }
        await asyncio.shield(
            _restore_cancelled_refresh(
                topology,
                expected_revision,
                lease_started_at,
                lease_token,
            )
        )
        raise
    except Exception as exc:
        run_ids = getattr(exc, "run_ids", run_ids)
        logger.exception(
            "网络拓扑刷新失败 topology_id=%s revision=%s: %s",
            topology.id,
            expected_revision,
            exc,
        )
        updated = await _mark_refresh_failure(
            topology,
            expected_revision,
            lease_started_at,
            lease_token,
            exc,
            run_ids,
        )
        return {
            "id": topology.id,
            "status": REFRESH_STATUS_FAILED if updated else "superseded",
            "changed": False,
            "last_run_ids": run_ids,
            "error": _normalize_text(str(exc) or exc.__class__.__name__),
        }
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


async def execute_claimed_refresh(claim: RefreshClaim) -> dict:
    return await refresh_topology(
        claim.topology_id,
        claim.revision,
        refresh_started_at=claim.refresh_started_at,
        tenant_id=claim.tenant_id,
        refresh_token=claim.refresh_token,
    )


async def sweep_due_topologies(limit: int = 4) -> int:
    """Claim and concurrently execute a bounded batch of due topologies."""
    claims = await claim_due_topologies(limit=limit)
    if not claims:
        return 0
    results = await asyncio.gather(
        *(execute_claimed_refresh(claim) for claim in claims),
        return_exceptions=True,
    )
    for claim, result in zip(claims, results):
        if isinstance(result, BaseException):
            logger.error(
                "网络拓扑后台刷新异常 topology_id=%s: %s", claim.topology_id, result
            )
    return len(claims)
