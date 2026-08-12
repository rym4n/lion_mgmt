import asyncio
from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace

import orjson
import pytest

from services.network_topology import contracts
from services.network_topology import manifest
from services.network_topology import views
from services.network_topology.canonicalization import canonicalize_json, sha256_uri


NOW = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
AUTHORITY = manifest.ManifestAuthority(
    tenant_id="tenant-1",
    topology_id="topology-1",
    subject="user-1",
    playbook_id="playbook-1",
    run_id="run-1",
)


class _Query:
    def __init__(self, rows, filters):
        self.rows = rows
        self.filters = filters
        self.connection = None
        self.locked = False
        self.limit_value = None
        self.ordering = None

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        self.locked = True
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def order_by(self, value):
        self.ordering = value
        return self

    async def first(self):
        return self.rows[0] if self.rows else None

    async def all(self):
        return self.rows

    def __await__(self):
        async def resolve():
            return self.rows

        return resolve().__await__()


def _fake_model(monkeypatch, name, rows):
    calls = []

    def filter_rows(**filters):
        query = _Query(rows, filters)
        calls.append(query)
        return query

    monkeypatch.setattr(manifest, name, SimpleNamespace(filter=filter_rows))
    return calls


def _payload(**changes):
    payload = {
        "id": "topology-1",
        "profile_revision": 1,
        "resolved_resource_ids": ["resource-1"],
        "selection_provenance": {
            "playbook_id": "playbook-1",
            "run_id": "run-1",
            "selector_hash": f"sha256:{'0' * 64}",
        },
    }
    payload.update(changes)
    return payload


def _assert_code(error, code):
    assert isinstance(error.value, manifest.ManifestError)
    assert error.value.code == code


def test_request_is_bound_to_authenticated_user_and_bounded_identifiers():
    assert manifest._validate_request(AUTHORITY, _payload())["id"] == "topology-1"
    assert manifest.authority_from_user_session(
        {"tenant_id": "tenant-1", "sub": "user-1"}, _payload()
    ) == AUTHORITY

    with pytest.raises(manifest.ManifestError) as plain_session:
        manifest.authority_from_user_session({}, _payload())
    _assert_code(plain_session, manifest.MANIFEST_AUTHORITY_INVALID)

    for payload in (
        _payload(id=" topology-1"),
        _payload(resolved_resource_ids=[f"resource-{index}" for index in range(101)]),
        {**_payload(), "tenant_id": "tenant-2"},
    ):
        with pytest.raises(manifest.ManifestError) as invalid:
            manifest._validate_request(AUTHORITY, payload)
        _assert_code(invalid, manifest.MANIFEST_INVALID_REQUEST)


def test_manifest_ingress_uses_global_switch(monkeypatch):
    monkeypatch.setattr(manifest, "TOPOLOGY_V2_INGRESS_ENABLED", False)
    with pytest.raises(manifest.ManifestError) as error:
        manifest._require_ingress()
    _assert_code(error, manifest.MANIFEST_INGRESS_DISABLED)
    monkeypatch.setattr(manifest, "TOPOLOGY_V2_INGRESS_ENABLED", True)
    manifest._require_ingress()


class _LockedIssueStubs:
    def __init__(self, events):
        self.events = events
        self.topology = SimpleNamespace()
        self.profile = SimpleNamespace(revision=1)

    async def locked_topology(self, *_args):
        self.events.append("topology-lock")
        return self.topology

    def clock(self):
        self.events.append("clock")
        return NOW

    async def current_profile(self, *_args):
        self.events.append("profile-lock")
        return self.profile, {}

    async def resources(self, *_args):
        self.events.append("resource-lock")
        return []

    async def sequence(self, *_args):
        return 1

    async def persist(self, *_args):
        return None

    async def upgrade_intent(self, *_args):
        self.events.append("version-intent-locks")
        return None, self.clock()


def test_clock_is_read_only_after_manifest_business_locks(monkeypatch):
    events = []
    stubs = _LockedIssueStubs(events)
    monkeypatch.setattr(manifest, "_locked_topology", stubs.locked_topology)
    monkeypatch.setattr(manifest, "_require_ingress", lambda: None)
    monkeypatch.setattr(manifest, "_current_profile", stubs.current_profile)
    monkeypatch.setattr(manifest, "_locked_resources", stubs.resources)
    monkeypatch.setattr(manifest, "_validated_registry_version", lambda: "v1")
    monkeypatch.setattr(manifest, "_expected_resources", lambda _rows: [])
    monkeypatch.setattr(manifest, "_upgrade_intent", stubs.upgrade_intent)
    monkeypatch.setattr(manifest, "_next_sequence", stubs.sequence)
    monkeypatch.setattr(
        manifest,
        "_target_manifest",
        lambda *_args: {"manifest_id": "manifest-1"},
    )
    monkeypatch.setattr(manifest, "_persist_manifest", stubs.persist)
    monkeypatch.setattr(manifest.contracts, "validate_contract", lambda *_args: None)

    result = asyncio.run(
        manifest._issue_locked(
            object(), AUTHORITY, _payload(), stubs.clock, "manifest-1"
        )
    )
    assert result.target_manifest == {"manifest_id": "manifest-1"}
    assert events == [
        "topology-lock",
        "profile-lock",
        "resource-lock",
        "version-intent-locks",
        "clock",
    ]


def test_profile_requires_one_current_canonical_row(monkeypatch):
    profile = contracts.load_schema("aioe.network-topology.profile/v1")["examples"][0]
    row = SimpleNamespace(
        profile_json=orjson.dumps(profile),
        profile_hash=sha256_uri(canonicalize_json(profile)),
    )
    topology = SimpleNamespace(
        topology_profile_revision=1,
        root_scope_id=profile["root_scope_seed"],
    )
    calls = _fake_model(monkeypatch, "TopologyProfileRevision", [row])
    actual_row, actual_profile = asyncio.run(
        manifest._current_profile(object(), AUTHORITY, topology, 1)
    )
    assert (actual_row, actual_profile) == (row, profile)
    assert calls[0].limit_value == 2

    row.profile_hash = f"sha256:{'f' * 64}"
    with pytest.raises(manifest.ManifestError) as mismatch:
        asyncio.run(manifest._current_profile(object(), AUTHORITY, topology, 1))
    _assert_code(mismatch, manifest.MANIFEST_PROFILE_STALE)

    _fake_model(monkeypatch, "TopologyProfileRevision", [row, row])
    with pytest.raises(manifest.ManifestError) as duplicate:
        asyncio.run(manifest._current_profile(object(), AUTHORITY, topology, 1))
    _assert_code(duplicate, manifest.MANIFEST_PROFILE_STALE)


def test_profile_revision_change_during_issue_is_rejected(monkeypatch):
    profile = contracts.load_schema("aioe.network-topology.profile/v1")["examples"][0]
    topology = SimpleNamespace(
        topology_profile_revision=1,
        root_scope_id=profile["root_scope_seed"],
    )
    row = SimpleNamespace(
        profile_json=orjson.dumps(profile),
        profile_hash=sha256_uri(canonicalize_json(profile)),
    )

    class CompetingProfileQuery(_Query):
        def __await__(self):
            async def resolve():
                topology.topology_profile_revision = 2
                return self.rows

            return resolve().__await__()

    monkeypatch.setattr(
        manifest,
        "TopologyProfileRevision",
        SimpleNamespace(
            filter=lambda **filters: CompetingProfileQuery([row], filters)
        ),
    )
    with pytest.raises(manifest.ManifestError) as stale:
        asyncio.run(manifest._current_profile(object(), AUTHORITY, topology, 1))
    _assert_code(stale, manifest.MANIFEST_PROFILE_STALE)


def test_resource_visibility_and_capability_failures_are_stable(monkeypatch):
    calls = _fake_model(monkeypatch, "AssetInstance", [])
    with pytest.raises(manifest.ManifestError) as missing:
        asyncio.run(
            manifest._locked_resources(
                object(), "tenant-1", ["cross-tenant-resource"], {}
            )
        )
    _assert_code(missing, manifest.MANIFEST_RESOURCE_NOT_FOUND)
    assert calls[0].filters["tenant_id"] == "tenant-1"

    resource = SimpleNamespace(
        id="resource-1",
        asset_model_key="network_device",
        attributes={
            "vendor": "generic",
            "platform": "unknown",
            "os_family": "unknown",
        },
    )
    for detail, code in (
        ("No capability profile matches", manifest.MANIFEST_CAPABILITY_UNSUPPORTED),
        (
            "Ambiguous capability profile matches",
            manifest.MANIFEST_CAPABILITY_AMBIGUOUS,
        ),
    ):
        monkeypatch.setattr(
            manifest.contracts,
            "resolve_capability",
            lambda *_args, detail=detail: (_ for _ in ()).throw(
                contracts.ContractError(detail)
            ),
        )
        with pytest.raises(manifest.ManifestError) as failure:
            manifest._resolve_resource_capability(resource)
        _assert_code(failure, code)


def test_upgrade_intent_counts_all_active_rows_before_validating(monkeypatch):
    topology = SimpleNamespace(id="topology-1", revision=7)
    version = SimpleNamespace(id="version-7")
    _fake_model(monkeypatch, "TopologyUpgradeIntent", [])
    with pytest.raises(manifest.ManifestError) as missing:
        asyncio.run(
            manifest._active_upgrade_intent(
                object(), AUTHORITY, topology, version, lambda: NOW
            )
        )
    _assert_code(missing, manifest.MANIFEST_UPGRADE_INTENT_REQUIRED)

    valid = SimpleNamespace(
        legacy_graph_version_row_id="version-7",
        expected_legacy_revision=7,
        consumed_at=None,
        cancelled_at=None,
        expires_at=NOW + timedelta(minutes=5),
    )
    _fake_model(monkeypatch, "TopologyUpgradeIntent", [valid, valid])
    with pytest.raises(manifest.ManifestError) as duplicate:
        asyncio.run(
            manifest._active_upgrade_intent(
                object(), AUTHORITY, topology, version, lambda: NOW
            )
        )
    _assert_code(duplicate, manifest.MANIFEST_UPGRADE_INTENT_INVALID)

    valid.expires_at = NOW - timedelta(seconds=1)
    _fake_model(monkeypatch, "TopologyUpgradeIntent", [valid])
    with pytest.raises(manifest.ManifestError) as expired:
        asyncio.run(
            manifest._active_upgrade_intent(
                object(), AUTHORITY, topology, version, lambda: NOW
            )
        )
    _assert_code(expired, manifest.MANIFEST_UPGRADE_INTENT_INVALID)

    valid.expires_at = NOW + timedelta(minutes=5)
    _fake_model(monkeypatch, "TopologyUpgradeIntent", [valid])
    assert asyncio.run(
        manifest._active_upgrade_intent(
            object(), AUTHORITY, topology, version, lambda: NOW
        )
    ) == (valid, NOW)


def test_v2_manifest_locks_and_validates_current_graph_version(monkeypatch):
    version = SimpleNamespace(
        id="version-3",
        version=3,
        document_schema_version=manifest.compatibility.V2_SCHEMA_VERSION,
        tenant_id="tenant-1",
        topology_id="topology-1",
    )
    calls = _fake_model(monkeypatch, "NetworkTopologyVersion", [version])
    topology = SimpleNamespace(
        document_schema_version=manifest.compatibility.V2_SCHEMA_VERSION,
        generation_source=manifest.compatibility.PLAYBOOK_GENERATION_SOURCE,
        current_graph_version_row_id="version-3",
        current_graph_revision=3,
        revision=8,
    )
    assert asyncio.run(
        manifest._upgrade_intent(object(), AUTHORITY, topology, lambda: NOW)
    ) == (None, NOW)
    assert calls[0].locked is True
    assert calls[0].filters == {
        "id": "version-3",
        "topology_id": "topology-1",
        "tenant_id": "tenant-1",
        "deleted": False,
    }

    version.document_schema_version = manifest.compatibility.LEGACY_SCHEMA_VERSION
    with pytest.raises(manifest.ManifestError) as stale:
        asyncio.run(
            manifest._upgrade_intent(object(), AUTHORITY, topology, lambda: NOW)
        )
    _assert_code(stale, manifest.MANIFEST_UPGRADE_INTENT_INVALID)


def test_sequence_uses_all_manifest_history_only(monkeypatch):
    topology = SimpleNamespace(id="topology-1")
    topology_calls = _fake_model(monkeypatch, "NetworkTopology", [topology])
    latest = SimpleNamespace(collection_sequence=9)
    calls = _fake_model(
        monkeypatch,
        "TopologyCollectionManifest",
        [latest],
    )
    value = asyncio.run(manifest._next_sequence(object(), AUTHORITY, topology))
    assert value == 10
    latest.collection_sequence = value
    assert asyncio.run(manifest._next_sequence(object(), AUTHORITY, topology)) == 11
    assert topology_calls[0].locked is True
    assert topology_calls[0].filters == {
        "id": "topology-1",
        "tenant_id": "tenant-1",
        "deleted": False,
    }
    assert calls[0].filters == {
        "tenant_id": "tenant-1",
        "topology_row_id": "topology-1",
    }
    assert calls[0].ordering == "-collection_sequence"


class _SequenceConnection:
    def __init__(self, lock, topology):
        self.lock = lock
        self.topology = topology
        self.owns_lock = False


class _SequenceTopologyQuery:
    def __init__(self, filters):
        self.filters = filters
        self.connection = None
        self.locked = False

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        self.locked = True
        return self

    async def first(self):
        assert self.locked
        await self.connection.lock.acquire()
        self.connection.owns_lock = True
        return self.connection.topology


class _SequenceHistoryQuery(_Query):
    async def first(self):
        await asyncio.sleep(0)
        key = (self.filters["tenant_id"], self.filters["topology_row_id"])
        values = self.rows.get(key, [])
        return SimpleNamespace(collection_sequence=max(values)) if values else None


async def _allocate_sequence(lock, topology, history, authority):
    connection = _SequenceConnection(lock, topology)
    try:
        sequence = await manifest._next_sequence(connection, authority, topology)
        await asyncio.sleep(0)
        key = (authority.tenant_id, authority.topology_id)
        history.setdefault(key, []).append(sequence)
        return sequence
    finally:
        if connection.owns_lock:
            connection.lock.release()


def test_concurrent_sequence_is_strictly_monotonic(monkeypatch):
    topology = SimpleNamespace(id="topology-1")
    lock = asyncio.Lock()
    history = {}
    monkeypatch.setattr(
        manifest,
        "NetworkTopology",
        SimpleNamespace(filter=lambda **filters: _SequenceTopologyQuery(filters)),
    )
    monkeypatch.setattr(
        manifest,
        "TopologyCollectionManifest",
        SimpleNamespace(
            filter=lambda **filters: _SequenceHistoryQuery(history, filters)
        ),
    )

    async def issue_concurrently():
        return await asyncio.gather(
            *(_allocate_sequence(lock, topology, history, AUTHORITY) for _ in range(8))
        )

    sequences = asyncio.run(issue_concurrently())
    assert sorted(sequences) == list(range(1, 9))
    assert len(set(sequences)) == len(sequences)


def test_sequence_history_is_isolated_between_tenants(monkeypatch):
    topology = SimpleNamespace(id="shared-topology-id")
    history = {
        ("tenant-1", "shared-topology-id"): [1, 2],
        ("tenant-2", "shared-topology-id"): [1, 2, 3, 4],
    }
    monkeypatch.setattr(
        manifest,
        "NetworkTopology",
        SimpleNamespace(
            filter=lambda **filters: _Query([topology], filters)
        ),
    )
    monkeypatch.setattr(
        manifest,
        "TopologyCollectionManifest",
        SimpleNamespace(
            filter=lambda **filters: _SequenceHistoryQuery(history, filters)
        ),
    )
    authorities = [
        manifest.ManifestAuthority(
            tenant_id=tenant_id,
            topology_id="shared-topology-id",
            subject="user-1",
            playbook_id="playbook-1",
            run_id="run-1",
        )
        for tenant_id in ("tenant-1", "tenant-2")
    ]

    async def read_sequences():
        return [
            await manifest._next_sequence(object(), authority, topology)
            for authority in authorities
        ]

    assert asyncio.run(read_sequences()) == [3, 5]


def test_target_manifest_freezes_strict_base_revisions():
    resources = contracts.load_schema(manifest.MANIFEST_RESPONSE_SCHEMA)["examples"][0][
        "data"
    ]["target_manifest"]["expected_resources"]
    topology = SimpleNamespace(current_graph_revision=3, publication_revision=4)
    target = manifest._target_manifest(
        topology,
        SimpleNamespace(revision=1),
        resources,
        1,
        "manifest-1",
        NOW,
        "aioe.network-topology.registry/capability-profiles/v1",
    )
    assert (target["base_graph_revision"], target["base_publication_revision"]) == (
        3,
        4,
    )
    topology.current_graph_revision = None
    with pytest.raises(manifest.ManifestError) as invalid:
        manifest._target_manifest(
            topology,
            SimpleNamespace(revision=1),
            resources,
            1,
            "manifest-1",
            NOW,
            "aioe.network-topology.registry/capability-profiles/v1",
        )
    _assert_code(invalid, manifest.MANIFEST_ISSUE_FAILED)


def _request(body, *, content_type="application/json", query_string=""):
    return SimpleNamespace(
        body=body,
        headers={"content-type": content_type},
        query_string=query_string,
        ctx=SimpleNamespace(session={}),
    )


@pytest.mark.parametrize(
    ("http_request", "status"),
    [
        (_request(b"{}", content_type="text/plain"), 415),
        (_request(b"{}", query_string="debug=1"), 400),
        (_request(b"[]"), 400),
        (_request(b'{"id":"a","id":"b"}'), 400),
        (_request(b"x" * (16 * 1024 + 1)), 413),
    ],
)
def test_manifest_http_rejects_noncanonical_request_boundary(http_request, status):
    response = asyncio.run(inspect.unwrap(views.issue_topology_manifest)(http_request))
    payload = orjson.loads(response.body)
    assert response.status == status
    assert payload["data"]["error_code"] == manifest.MANIFEST_INVALID_REQUEST


def test_manifest_http_returns_only_public_result(monkeypatch):
    request = _request(orjson.dumps(_payload()))
    monkeypatch.setattr(
        views.topology_manifest,
        "authority_from_user_session",
        lambda session, payload: AUTHORITY,
    )

    async def issue(authority, payload):
        assert (authority, payload["id"]) == (AUTHORITY, "topology-1")
        return manifest.ManifestIssueResult({"manifest_id": "manifest-1"}, "v1")

    monkeypatch.setattr(views.topology_manifest, "issue_manifest", issue)
    response = asyncio.run(inspect.unwrap(views.issue_topology_manifest)(request))
    payload = orjson.loads(response.body)
    assert response.status == 200
    assert payload["data"] == {"target_manifest": {"manifest_id": "manifest-1"}}
