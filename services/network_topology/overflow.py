"""Deterministic presentation-only overflow buckets for scope projections."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping


DIRECT_ITEM_LIMIT = 200
OVERFLOW_STRATEGY = "kind-importance-stable-id/v1"
TOKEN_VERSION = 1
IMPORTANCE_ORDER = {"critical": 0, "important": 1, "normal": 2}


class OverflowTokenError(ValueError):
    """Raised when a continuation token no longer matches its bucket."""


@dataclass(frozen=True, slots=True)
class OverflowBucket:
    id: str
    category: str
    total_count: int
    strategy: str
    summary: Mapping[str, Any]
    continuation_token: str
    member_ids: tuple[str, ...]

    def as_node(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "projection_kind": "view_bucket",
            "kind": "view_bucket",
            "category": self.category,
            "total_count": self.total_count,
            "strategy": self.strategy,
            "summary": dict(self.summary),
            "continuation_token": self.continuation_token,
        }


@dataclass(frozen=True, slots=True)
class OverflowPlan:
    total_count: int
    overflowed: bool
    strategy: str
    visible_items: tuple[dict[str, Any], ...]
    buckets: tuple[OverflowBucket, ...]


@dataclass(frozen=True, slots=True)
class BucketPage:
    bucket_id: str
    total_count: int
    offset: int
    items: tuple[dict[str, Any], ...]
    continuation_token: str | None


def plan_overflow(
    scope_id: str,
    items: Iterable[Mapping[str, Any]],
    *,
    limit: int = DIRECT_ITEM_LIMIT,
) -> OverflowPlan:
    """Return direct items or deterministic category buckets above the limit."""
    normalized = _normalized_items(items)
    _validate_limit(limit)
    if len(normalized) <= limit:
        return OverflowPlan(len(normalized), False, OVERFLOW_STRATEGY, normalized, ())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        grouped[_category(item)].append(item)
    buckets = tuple(
        _bucket(scope_id, category, members, limit)
        for category, members in sorted(grouped.items())
    )
    return OverflowPlan(len(normalized), True, OVERFLOW_STRATEGY, (), buckets)


def page_bucket(
    scope_id: str,
    items: Iterable[Mapping[str, Any]],
    bucket_id: str,
    continuation_token: str | None = None,
    *,
    limit: int = DIRECT_ITEM_LIMIT,
) -> BucketPage:
    """Read one stable bucket page, rejecting stale or cross-scope tokens."""
    plan = plan_overflow(scope_id, items, limit=limit)
    bucket = next((item for item in plan.buckets if item.id == bucket_id), None)
    if bucket is None:
        raise OverflowTokenError("unknown or stale overflow bucket")
    token = continuation_token or bucket.continuation_token
    payload = _decode_token(token)
    _validate_token(payload, scope_id, bucket, limit)
    offset = payload["offset"]
    by_id = {str(item["id"]): item for item in _normalized_items(items)}
    members = tuple(by_id[item_id] for item_id in bucket.member_ids)
    page = members[offset : offset + limit]
    next_offset = offset + len(page)
    next_token = _token(scope_id, bucket.id, bucket.member_ids, next_offset, limit)
    if next_offset >= bucket.total_count:
        next_token = None
    return BucketPage(bucket.id, bucket.total_count, offset, page, next_token)


def locate_item_bucket(
    scope_id: str,
    items: Iterable[Mapping[str, Any]],
    stable_id: str,
    *,
    limit: int = DIRECT_ITEM_LIMIT,
) -> str | None:
    """Locate a canonical search hit without applying first-page truncation."""
    plan = plan_overflow(scope_id, items, limit=limit)
    return next(
        (bucket.id for bucket in plan.buckets if stable_id in bucket.member_ids),
        None,
    )


def _normalized_items(
    items: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in items:
        item = dict(raw)
        identifier = str(item.get("id") or "")
        if not identifier:
            raise ValueError("overflow item needs a stable id")
        if identifier in by_id:
            raise ValueError(f"duplicate overflow item {identifier}")
        by_id[identifier] = item
    return tuple(sorted(by_id.values(), key=_item_sort_key))


def _item_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    kind = _item_kind(item)
    importance = str(item.get("importance") or "normal")
    rank = IMPORTANCE_ORDER.get(importance, len(IMPORTANCE_ORDER))
    return kind, rank, importance, str(item["id"])


def _item_kind(item: Mapping[str, Any]) -> str:
    projection_kind = str(item.get("projection_kind") or "")
    if projection_kind == "scope_ref":
        return "scope"
    return str(item.get("kind") or projection_kind or "entity")


def _category(item: Mapping[str, Any]) -> str:
    importance = str(item.get("importance") or "normal")
    return f"{_item_kind(item)}:{importance}"


def _bucket(scope_id, category, members, limit):
    ordered = tuple(sorted(members, key=_item_sort_key))
    member_ids = tuple(str(item["id"]) for item in ordered)
    identifier = _bucket_id(scope_id, category, member_ids)
    kind, importance = category.rsplit(":", 1)
    summary = {
        "kind": kind,
        "importance": importance,
        "first_id": member_ids[0],
        "last_id": member_ids[-1],
    }
    return OverflowBucket(
        identifier,
        category,
        len(member_ids),
        OVERFLOW_STRATEGY,
        summary,
        _token(scope_id, identifier, member_ids, 0, limit),
        member_ids,
    )


def _bucket_id(scope_id, category, member_ids):
    digest = _digest([scope_id, category, *member_ids])
    return f"view-bucket:{digest}"


def _member_digest(member_ids):
    return _digest(list(member_ids))


def _digest(value):
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return sha256(serialized.encode()).hexdigest()


def _token(scope_id, bucket_id, member_ids, offset, limit):
    payload = {
        "v": TOKEN_VERSION,
        "scope": scope_id,
        "bucket": bucket_id,
        "members": _member_digest(member_ids),
        "offset": offset,
        "limit": limit,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_token(token):
    try:
        padding = "=" * (-len(token) % 4)
        value = json.loads(urlsafe_b64decode(token + padding))
    except (Base64Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OverflowTokenError("invalid overflow continuation token") from exc
    if not isinstance(value, dict):
        raise OverflowTokenError("invalid overflow continuation token")
    return value


def _validate_token(payload, scope_id, bucket, limit):
    expected = {
        "v": TOKEN_VERSION,
        "scope": scope_id,
        "bucket": bucket.id,
        "members": _member_digest(bucket.member_ids),
        "limit": limit,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise OverflowTokenError("continuation token does not match bucket")
    offset = payload.get("offset")
    if not isinstance(offset, int) or offset < 0 or offset >= bucket.total_count:
        raise OverflowTokenError("continuation token offset is invalid")


def _validate_limit(limit):
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("overflow limit must be a positive integer")
