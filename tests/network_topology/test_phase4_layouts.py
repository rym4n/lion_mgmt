import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.network_topology import layouts


AUTHORITY = layouts.LayoutAuthority("tenant-1", "topology-1", "user-1")
NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


class MemoryLayoutRepository:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.topology = SimpleNamespace(
            id="topology-1",
            tenant_id="tenant-1",
            current_graph_revision=1,
            current_layout_set_revision=0,
            current_layout_set_row_id=None,
        )
        self.layout_sets = {}
        self.layout_versions = {}
        self.overrides = []
        self.sequence = 0

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            yield self

    async def get_topology(self, tenant_id, topology_id, *, connection, lock):
        assert lock and connection is self
        if (tenant_id, topology_id) != ("tenant-1", "topology-1"):
            return None
        return self.topology

    async def get_layout_set(self, tenant_id, topology_id, row_id, *, connection, lock):
        assert lock and connection is self
        return self.layout_sets.get(row_id)

    async def create_layout_version(self, values, *, connection):
        self.sequence += 1
        row = SimpleNamespace(id=f"layout-{self.sequence}", **values)
        self.layout_versions[row.id] = row
        return row

    async def get_layout_version(self, tenant_id, topology_id, row_id, *, connection):
        assert (tenant_id, topology_id) == ("tenant-1", "topology-1")
        assert connection is self
        return self.layout_versions.get(row_id)

    async def create_layout_set(self, values, *, connection):
        self.sequence += 1
        row = SimpleNamespace(id=f"set-{self.sequence}", **values)
        self.layout_sets[row.id] = row
        return row

    async def cas_layout_pointer(
        self,
        topology,
        *,
        expected_row_id,
        expected_set_revision,
        expected_graph_revision,
        new_set,
        next_graph_revision,
        connection,
    ):
        current = (
            topology.current_layout_set_row_id,
            topology.current_layout_set_revision,
            topology.current_graph_revision,
        )
        if current != (
            expected_row_id,
            expected_set_revision,
            expected_graph_revision,
        ):
            return False
        topology.current_layout_set_row_id = new_set.id
        topology.current_layout_set_revision = new_set.layout_set_revision
        if next_graph_revision is not None:
            topology.current_graph_revision = next_graph_revision
        return True

    async def list_active_overrides(self, authority, *, connection, lock):
        assert lock and connection is self
        return [row for row in self.overrides if row.status == "ACTIVE"]

    async def save_override(self, row, fields, *, connection):
        assert connection is self

    async def create_override(self, values, *, connection):
        row = SimpleNamespace(id=f"override-{len(self.overrides) + 1}", **values)
        self.overrides.append(row)
        return row


class PausingMemoryLayoutRepository(MemoryLayoutRepository):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.pause_next_transaction = True

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            if self.pause_next_transaction:
                self.pause_next_transaction = False
                self.entered.set()
                await self.release.wait()
            yield self


def payload(scope_id, expected=0, graph=1, x=10):
    return {
        "id": "topology-1",
        "scope_id": scope_id,
        "expected_scope_layout_revision": expected,
        "graph_revision": graph,
        "items": {"device:a": {"x": x, "y": 20, "pinned": True}},
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "algorithm": "manual",
    }


def run(awaitable):
    return asyncio.run(awaitable)


def test_same_scope_concurrent_save_returns_409():
    async def scenario():
        repo = MemoryLayoutRepository()
        results = await asyncio.gather(
            layouts.save_scope_layout(AUTHORITY, payload("scope:a"), repository=repo),
            layouts.save_scope_layout(AUTHORITY, payload("scope:a"), repository=repo),
            return_exceptions=True,
        )
        errors = [item for item in results if isinstance(item, layouts.LayoutError)]
        assert len(errors) == 1
        assert errors[0].status == 409
        assert errors[0].code == layouts.LAYOUT_SCOPE_CONFLICT

    run(scenario())


def test_cross_scope_concurrent_save_copy_on_write_retains_both_scopes():
    async def scenario():
        repo = MemoryLayoutRepository()
        await asyncio.gather(
            layouts.save_scope_layout(AUTHORITY, payload("scope:a"), repository=repo),
            layouts.save_scope_layout(AUTHORITY, payload("scope:b"), repository=repo),
        )
        current = repo.layout_sets[repo.topology.current_layout_set_row_id]
        scope_map = layouts.decode_scope_version_map(current.scope_version_map_json)
        assert set(scope_map) == {"scope:a", "scope:b"}
        assert current.layout_set_revision == 2

    run(scenario())


def test_graph_publish_and_drag_competition_preserves_committed_drag():
    async def scenario():
        repo = PausingMemoryLayoutRepository()
        drag_task = asyncio.create_task(
            layouts.save_scope_layout(
                AUTHORITY, payload("scope:a", x=99), repository=repo
            )
        )
        await repo.entered.wait()
        publish_task = asyncio.create_task(
            layouts.rebase_layouts_for_graph(
                AUTHORITY,
                expected_graph_revision=1,
                graph_revision=2,
                scope_items={"scope:a": {"device:a", "device:new"}},
                aliases={},
                repository=repo,
            )
        )
        await asyncio.sleep(0)
        assert not publish_task.done()
        repo.release.set()
        _, publish = await asyncio.gather(drag_task, publish_task)
        current = repo.layout_sets[repo.topology.current_layout_set_row_id]
        entry = layouts.decode_scope_version_map(current.scope_version_map_json)["scope:a"]
        version = repo.layout_versions[entry["layout_version_row_id"]]
        document = layouts.decode_layout_document(version.layout_json)
        assert publish.graph_revision == 2
        assert document["items"]["device:a"]["x"] == 99
        assert document["pending_item_ids"] == ["device:new"]

    run(scenario())


def test_graph_publish_winning_race_rejects_old_graph_drag():
    async def scenario():
        repo = PausingMemoryLayoutRepository()
        publish_task = asyncio.create_task(
            layouts.rebase_layouts_for_graph(
                AUTHORITY,
                expected_graph_revision=1,
                graph_revision=2,
                scope_items={"scope:a": {"device:a"}},
                aliases={},
                repository=repo,
            )
        )
        await repo.entered.wait()
        drag_task = asyncio.create_task(
            layouts.save_scope_layout(
                AUTHORITY, payload("scope:a", graph=1), repository=repo
            )
        )
        await asyncio.sleep(0)
        assert not drag_task.done()
        repo.release.set()
        publish, drag = await asyncio.gather(
            publish_task, drag_task, return_exceptions=True
        )
        assert publish.graph_revision == 2
        assert isinstance(drag, layouts.LayoutError)
        assert drag.code == layouts.LAYOUT_GRAPH_CONFLICT

    run(scenario())


def test_old_graph_layout_save_is_rejected_after_publish():
    async def scenario():
        repo = MemoryLayoutRepository()
        await layouts.rebase_layouts_for_graph(
            AUTHORITY,
            expected_graph_revision=1,
            graph_revision=2,
            scope_items={"scope:a": {"device:a"}},
            aliases={},
            repository=repo,
        )
        with pytest.raises(layouts.LayoutError) as error:
            await layouts.save_scope_layout(
                AUTHORITY, payload("scope:a", graph=1), repository=repo
            )
        assert error.value.status == 409
        assert error.value.code == layouts.LAYOUT_GRAPH_CONFLICT

    run(scenario())


def test_layout_request_cannot_cross_authority_topology():
    request = payload("scope:a")
    request["id"] = "topology-other"
    with pytest.raises(layouts.LayoutError) as error:
        run(
            layouts.save_scope_layout(
                AUTHORITY, request, repository=MemoryLayoutRepository()
            )
        )
    assert error.value.status == 400


def test_graph_rebase_keeps_valid_items_and_marks_removed_items_orphan():
    async def scenario():
        repo = MemoryLayoutRepository()
        request = payload("scope:a")
        request["items"]["device:removed"] = {"x": 30, "y": 40, "pinned": False}
        await layouts.save_scope_layout(AUTHORITY, request, repository=repo)
        await layouts.rebase_layouts_for_graph(
            AUTHORITY,
            expected_graph_revision=1,
            graph_revision=2,
            scope_items={"scope:a": {"device:a", "device:new"}},
            aliases={},
            repository=repo,
        )
        current = repo.layout_sets[repo.topology.current_layout_set_row_id]
        entry = layouts.decode_scope_version_map(current.scope_version_map_json)["scope:a"]
        document = layouts.decode_layout_document(
            repo.layout_versions[entry["layout_version_row_id"]].layout_json
        )
        assert set(document["items"]) == {"device:a"}
        assert document["orphan_items"]["device:removed"]["x"] == 30
        assert document["pending_item_ids"] == ["device:new"]

    run(scenario())


def test_graph_rebase_migrates_unique_alias_coordinates():
    async def scenario():
        repo = MemoryLayoutRepository()
        await layouts.save_scope_layout(
            AUTHORITY, payload("scope:a", x=77), repository=repo
        )
        await layouts.rebase_layouts_for_graph(
            AUTHORITY,
            expected_graph_revision=1,
            graph_revision=2,
            scope_items={"scope:a": {"device:new"}},
            aliases={"device:a": "device:new"},
            repository=repo,
        )
        current = repo.layout_sets[repo.topology.current_layout_set_row_id]
        entry = layouts.decode_scope_version_map(current.scope_version_map_json)["scope:a"]
        document = layouts.decode_layout_document(
            repo.layout_versions[entry["layout_version_row_id"]].layout_json
        )
        assert document["items"]["device:new"]["x"] == 77
        assert document["pending_item_ids"] == []
        assert document["orphan_items"] == {}

    run(scenario())


def test_initial_null_layout_set_revision_is_preserved_in_cas_comparison():
    async def scenario():
        repo = MemoryLayoutRepository()
        repo.topology.current_layout_set_revision = None
        await layouts.save_scope_layout(
            AUTHORITY, payload("scope:a"), repository=repo
        )
        assert repo.topology.current_layout_set_revision == 1

    run(scenario())


def override(target, kind="display_name", value="Core switch"):
    return SimpleNamespace(
        id=f"override-{target}",
        tenant_id="tenant-1",
        topology_row_id="topology-1",
        profile_revision_row_id="profile-1",
        target_stable_id=target,
        override_kind=kind,
        override_value=layouts.encode_json(value),
        status="ACTIVE",
        created_by="user-1",
        orphaned_at=None,
        migrated_to_target_stable_id=None,
    )


def test_alias_migration_creates_active_replacement_and_keeps_audit_row():
    repo = MemoryLayoutRepository()
    original = override("device:old")
    repo.overrides.append(original)
    result = run(
        layouts.reconcile_presentation_overrides(
            AUTHORITY,
            valid_stable_ids={"device:new"},
            aliases={"device:old": "device:new"},
            authorized_scope_ids=set(),
            repository=repo,
            clock=lambda: NOW,
        )
    )
    assert result.migrated == 1
    assert original.status == "MIGRATED"
    assert original.migrated_to_target_stable_id == "device:new"
    replacement = repo.overrides[-1]
    assert replacement.target_stable_id == "device:new"
    assert replacement.status == "ACTIVE"


def test_unresolved_override_is_orphaned_and_warned_not_deleted():
    repo = MemoryLayoutRepository()
    original = override("device:gone")
    repo.overrides.append(original)
    warnings = []
    result = run(
        layouts.reconcile_presentation_overrides(
            AUTHORITY,
            valid_stable_ids=set(),
            aliases={},
            authorized_scope_ids=set(),
            repository=repo,
            clock=lambda: NOW,
            warn=warnings.append,
        )
    )
    assert result.orphaned == 1
    assert original in repo.overrides
    assert original.status == "ORPHANED"
    assert original.orphaned_at == NOW
    assert warnings == ["Orphaned presentation override override-device:gone"]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("display_name", "Core"),
        ("hidden", True),
        ("highlighted", True),
        ("note", "Investigate"),
        ("color", "#ff8800"),
        ("scope_mapping", {"scope_ids": ["scope:a"]}),
    ],
)
def test_override_allowlist_accepts_presentation_only_fields(kind, value):
    assert layouts.validate_override(kind, value, {"scope:a"}) == value


@pytest.mark.parametrize("kind", ["hostname", "status", "ip", "relations", "facts"])
def test_override_rejects_observed_runtime_facts(kind):
    with pytest.raises(layouts.LayoutError) as error:
        layouts.validate_override(kind, "changed", {"scope:a"})
    assert error.value.status == 422
    assert error.value.code == layouts.OVERRIDE_KIND_FORBIDDEN


def test_scope_mapping_must_be_authorized():
    with pytest.raises(layouts.LayoutError) as error:
        layouts.validate_override(
            "scope_mapping", {"scope_ids": ["scope:other"]}, {"scope:a"}
        )
    assert error.value.status == 403
    assert error.value.code == layouts.OVERRIDE_SCOPE_FORBIDDEN
