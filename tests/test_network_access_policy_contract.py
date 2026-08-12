# coding=utf-8
import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from libs.error_code.exception import CustomError
from models.network_access_control import NetworkAccessActualPolicy
from services.network_access_control import manager


SNAPSHOT_FIELDS = (
    "accessory_project_id",
    "device_asset_id",
    "device_key",
    "device_name",
    "device_type",
    "collection_run_id",
    "raw_rule_id",
    "policy_key",
    "policy_type",
    "rule_name",
    "source_zone",
    "source_address",
    "target_zone",
    "target_address",
    "protocol",
    "port",
    "source_port",
    "destination_port",
    "service",
    "policy_order",
    "action",
    "enabled",
    "raw_text",
    "policy_data",
    "parse_status",
    "revision",
    "last_synced_at",
)
ROOT = Path(__file__).resolve().parents[1]


class _PolicyRow:
    def __init__(self, **values):
        defaults = {
            "accessory_project_id": None,
            "device_asset_id": None,
            "device_key": None,
            "device_name": None,
            "device_type": None,
            "collection_run_id": None,
            "raw_rule_id": None,
            "policy_key": None,
            "policy_type": None,
            "rule_name": None,
            "source_zone": None,
            "source_address": None,
            "target_zone": None,
            "target_address": None,
            "protocol": None,
            "port": None,
            "source_port": None,
            "destination_port": None,
            "service": None,
            "policy_order": None,
            "action": "permit",
            "enabled": True,
            "raw_text": None,
            "policy_data": {},
            "parse_status": "parsed",
            "revision": 1,
            "last_synced_at": None,
            "deleted": False,
            "creator": None,
            "updater": None,
        }
        defaults.update(values)
        self.__dict__.update(defaults)
        self.save_calls = []

    async def save(self, **kwargs):
        self.save_calls.append(kwargs)

    @property
    def json(self):
        payload = {"id": self.id} | {
            field: getattr(self, field) for field in SNAPSHOT_FIELDS
        }
        policy_data = self.policy_data or {}

        def first(*keys, fallback=None):
            for key in keys:
                value = policy_data.get(key)
                if value not in (None, ""):
                    return value
            return fallback

        payload.update(
            {
                "source_port": first("source_port", "src_port"),
                "destination_port": first(
                    "destination_port",
                    "target_port",
                    "dest_port",
                    "dst_port",
                    fallback=self.port,
                ),
                "service": first(
                    "service",
                    "service_name",
                    "application",
                    fallback=self.protocol,
                ),
                "policy_order": first(
                    "policy_order",
                    "rule_order",
                    "position",
                    "sequence",
                    "order",
                ),
            }
        )
        return payload


class _PolicyQuery:
    def __init__(self, model, filters):
        self.model = model
        self.filters = filters
        self.excludes = {}

    def using_db(self, connection):
        self.model.query_log.append(("using_db", self.filters, connection))
        return self

    def select_for_update(self):
        self.model.query_log.append(("select_for_update", self.filters))
        return self

    def exclude(self, **kwargs):
        self.excludes.update(kwargs)
        return self

    def _matches(self, row):
        for key, value in self.filters.items():
            if key.endswith("__in"):
                if getattr(row, key[:-4]) not in value:
                    return False
            elif getattr(row, key) != value:
                return False
        return not any(
            getattr(row, key) == value for key, value in self.excludes.items()
        )

    async def first(self):
        return next((row for row in self.model.rows if self._matches(row)), None)

    async def all(self):
        return [row for row in self.model.rows if self._matches(row)]

    async def exists(self):
        return any(self._matches(row) for row in self.model.rows)


class _FakePolicyModel:
    rows = []
    query_log = []
    next_id = 1

    @classmethod
    def reset(cls):
        cls.rows = []
        cls.query_log = []
        cls.next_id = 1

    @classmethod
    def filter(cls, **kwargs):
        cls.query_log.append(("filter", deepcopy(kwargs)))
        return _PolicyQuery(cls, kwargs)

    @classmethod
    async def create(cls, **kwargs):
        kwargs.pop("using_db", None)
        row = _PolicyRow(id=f"policy-{cls.next_id}", **kwargs)
        cls.next_id += 1
        cls.rows.append(row)
        return row


class _FakeHistoryModel:
    rows = []

    @classmethod
    async def create(cls, **kwargs):
        cls.rows.append(deepcopy(kwargs))
        return cls.rows[-1]


class _Transaction:
    async def __aenter__(self):
        return "connection-1"

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.fixture(autouse=True)
def fake_policy_store(monkeypatch):
    _FakePolicyModel.reset()
    _FakeHistoryModel.rows = []
    monkeypatch.setattr(manager, "NetworkAccessActualPolicy", _FakePolicyModel)
    monkeypatch.setattr(manager, "NetworkAccessPolicyChange", _FakeHistoryModel)
    monkeypatch.setattr(manager, "in_transaction", lambda: _Transaction())


def _policy_payload(policy_key, *, device_key="device-a", action="permit"):
    return {
        "device_key": device_key,
        "device_name": "边界防火墙",
        "device_type": "firewall",
        "policy_key": policy_key,
        "rule_name": policy_key,
        "source_address": "10.0.0.0/8",
        "target_address": "172.16.0.0/16",
        "protocol": "tcp",
        "port": "443",
        "source_port": "1024-65535",
        "destination_port": "443",
        "service": "HTTPS",
        "policy_order": 10,
        "action": action,
        "policy_data": {"vendor": "example"},
    }


def test_actual_policy_json_flattens_structured_policy_details():
    policy = NetworkAccessActualPolicy(
        tenant_id="tenant-1",
        device_key="device-a",
        policy_key="allow-api",
        port="80",
        protocol="tcp",
        policy_data={
            "source_port": "1024-65535",
            "destination_port": "443",
            "service": "HTTPS",
            "policy_order": 10,
        },
    )

    assert policy.json["source_port"] == "1024-65535"
    assert policy.json["destination_port"] == "443"
    assert policy.json["service"] == "HTTPS"
    assert policy.json["policy_order"] == 10


def test_single_policy_crud_appends_immutable_before_after_history():
    created = asyncio.run(
        manager.save_actual_policy(
            "tenant-1",
            _policy_payload("allow-api") | {"batch_id": "manual-create"},
            "alice",
        )
    )

    assert created.revision == 1
    assert len(_FakeHistoryModel.rows) == 1
    added = _FakeHistoryModel.rows[0]
    assert added["tenant_id"] == "tenant-1"
    assert added["change_type"] == "added"
    assert added["before_data"] == {}
    assert added["after_data"]["action"] == "permit"
    assert added["after_data"]["source_port"] == "1024-65535"
    assert added["after_data"]["destination_port"] == "443"
    assert added["after_data"]["service"] == "HTTPS"
    assert added["batch_id"] == "manual-create"

    updated = asyncio.run(
        manager.save_actual_policy(
            "tenant-1",
            {
                "id": created.id,
                "revision": 1,
                "action": "deny",
                "batch_id": "manual-update",
            },
            "bob",
        )
    )

    assert updated.revision == 2
    assert updated.action == "deny"
    assert updated.policy_data == {
        "vendor": "example",
        "source_port": "1024-65535",
        "destination_port": "443",
        "service": "HTTPS",
        "policy_order": 10,
    }
    modified = _FakeHistoryModel.rows[1]
    assert modified["change_type"] == "modified"
    assert modified["before_data"]["action"] == "permit"
    assert modified["after_data"]["action"] == "deny"
    assert modified["revision"] == 2

    result = asyncio.run(manager.delete_actual_policy("tenant-1", created.id, "carol"))

    assert result == {"id": created.id}
    assert created.deleted is True
    assert created.revision == 3
    deleted = _FakeHistoryModel.rows[2]
    assert deleted["change_type"] == "deleted"
    assert deleted["before_data"]["action"] == "deny"
    assert deleted["after_data"] == {}
    assert added["after_data"]["action"] == "permit"
    assert added["after_data"]["revision"] == 1


def test_bulk_sync_skips_unchanged_history_and_replaces_missing_policy():
    existing = asyncio.run(
        manager.save_actual_policy("tenant-1", _policy_payload("keep"), "seed")
    )
    stale = asyncio.run(
        manager.save_actual_policy("tenant-1", _policy_payload("stale"), "seed")
    )
    other_device = asyncio.run(
        manager.save_actual_policy(
            "tenant-1",
            _policy_payload("other", device_key="device-b"),
            "seed",
        )
    )
    other_tenant = asyncio.run(
        manager.save_actual_policy("tenant-2", _policy_payload("tenant-two"), "seed")
    )
    _FakeHistoryModel.rows = []

    result = asyncio.run(
        manager.sync_actual_policies(
            "tenant-1",
            {
                "device_key": "device-a",
                "device_name": "边界防火墙",
                "device_type": "firewall",
                "replace": True,
                "batch_id": "playbook-run-1",
                "sync_source": "nightly-firewall-playbook",
                "policies": [
                    {
                        key: value
                        for key, value in _policy_payload("keep").items()
                        if key not in {"device_key", "device_name", "device_type"}
                    },
                    {
                        key: value
                        for key, value in _policy_payload("new").items()
                        if key not in {"device_key", "device_name", "device_type"}
                    },
                ],
            },
            "playbook",
        )
    )

    assert result == {
        "batch_id": "playbook-run-1",
        "received": 2,
        "added": 1,
        "modified": 0,
        "moved": 0,
        "deleted": 1,
        "unchanged": 1,
        "synced": 1,
        "history_count": 2,
    }
    assert existing.deleted is False
    assert existing.revision == 2
    assert stale.deleted is True
    assert stale.revision == 2
    assert other_device.deleted is False
    assert other_tenant.deleted is False
    assert sorted(item["change_type"] for item in _FakeHistoryModel.rows) == [
        "added",
        "deleted",
    ]
    assert {item["batch_id"] for item in _FakeHistoryModel.rows} == {"playbook-run-1"}
    assert {item["sync_source"] for item in _FakeHistoryModel.rows} == {
        "nightly-firewall-playbook"
    }


def test_policy_order_only_update_is_recorded_as_move():
    existing = asyncio.run(
        manager.save_actual_policy("tenant-1", _policy_payload("move-me"), "seed")
    )
    _FakeHistoryModel.rows = []

    updated = asyncio.run(
        manager.save_actual_policy(
            "tenant-1",
            {
                "id": existing.id,
                "revision": existing.revision,
                "policy_order": 30,
                "batch_id": "move-batch",
            },
            "alice",
        )
    )

    assert updated.policy_data["policy_order"] == 30
    assert len(_FakeHistoryModel.rows) == 1
    moved = _FakeHistoryModel.rows[0]
    assert moved["change_type"] == "moved"
    assert moved["before_data"]["policy_order"] == 10
    assert moved["after_data"]["policy_order"] == 30
    assert moved["creator"] == "alice"


def test_canonical_policy_details_prefer_structured_data_over_legacy_fields():
    structured = manager._normalize_actual_policy_payload(
        {
            "device_key": "device-a",
            "policy_key": "canonical-precedence",
            "port": "80",
            "protocol": "tcp",
            "policy_data": {
                "src_port": "1024-65535",
                "target_port": "443",
                "application": "HTTPS",
                "sequence": 10,
            },
        },
        "playbook",
    )

    assert structured["policy_data"] == {
        "src_port": "1024-65535",
        "target_port": "443",
        "application": "HTTPS",
        "sequence": 10,
        "source_port": "1024-65535",
        "destination_port": "443",
        "service": "HTTPS",
        "policy_order": 10,
    }

    top_level = manager._normalize_actual_policy_payload(
        {
            "device_key": "device-a",
            "policy_key": "canonical-precedence",
            "source_port": "2000-65535",
            "destination_port": "8443",
            "service": "HTTPS-ALT",
            "policy_order": 20,
            "port": "80",
            "protocol": "tcp",
            "policy_data": {
                "source_port": "1024-65535",
                "destination_port": "443",
                "service": "HTTPS",
                "policy_order": 10,
            },
        },
        "playbook",
    )

    assert top_level["policy_data"] == {
        "source_port": "2000-65535",
        "destination_port": "8443",
        "service": "HTTPS-ALT",
        "policy_order": 20,
    }


def test_policy_mutations_cannot_cross_tenant_boundary():
    foreign = asyncio.run(
        manager.save_actual_policy("tenant-2", _policy_payload("foreign"), "tenant-two")
    )
    _FakeHistoryModel.rows = []

    with pytest.raises(CustomError):
        asyncio.run(
            manager.save_actual_policy(
                "tenant-1",
                {
                    "id": foreign.id,
                    "revision": foreign.revision,
                    "action": "deny",
                },
                "tenant-one",
            )
        )
    with pytest.raises(CustomError):
        asyncio.run(manager.delete_actual_policy("tenant-1", foreign.id, "tenant-one"))

    assert foreign.deleted is False
    assert foreign.action == "permit"
    assert _FakeHistoryModel.rows == []
    locked_filters = [
        entry[1]
        for entry in _FakePolicyModel.query_log
        if entry[0] == "select_for_update"
    ]
    assert locked_filters == [
        {"id": foreign.id, "tenant_id": "tenant-1", "deleted": False},
        {"id": foreign.id, "tenant_id": "tenant-1", "deleted": False},
    ]


def test_policy_identity_is_non_null_and_unique_for_active_inventory_rows():
    fields_map = NetworkAccessActualPolicy._meta.fields_map
    migration = (
        ROOT / "migrations/models/83_20260720161000_access_policy_inventory_history.py"
    ).read_text()

    assert fields_map["device_key"].null is False
    assert fields_map["policy_key"].null is False
    assert manager.POLICY_CHANGE_TYPES == {"added", "modified", "moved", "deleted"}
    assert (
        'CREATE UNIQUE INDEX IF NOT EXISTS "idx_scr_actual_tenant_policy_identity"'
        in migration
    )
    assert ') WHERE NOT "deleted";' in migration
    assert 'ALTER COLUMN "device_key" SET NOT NULL' in migration
    assert 'ALTER COLUMN "policy_key" SET NOT NULL' in migration
    assert (
        "NULLIF(\"raw_rule_id\", ''),\n                'legacy-policy:' || \"id\""
        in migration
    )
    assert "NULLIF(\"rule_name\", '')" not in migration
