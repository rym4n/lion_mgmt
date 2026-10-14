"""Detached ORM instance regressions run in isolation from fake-model test suites."""
import subprocess
import sys
from pathlib import Path


def test_collection_durable_transitions_and_analysis_fences():
    script = Path(__file__).with_name("collection_orm_regression.py")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


import pytest
from types import SimpleNamespace
import orjson
from tests.network_topology.test_phase2_snapshot_direct import (
    _envelope, _Repository, _PayloadStore, NOW,
)
from services.network_topology.snapshot import commit_prepared_snapshot, validate_direct_snapshot
from services.network_topology.payload_store import PreparedPayload


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_shared_ingress_links_collection_in_same_transaction_and_lock_order(fail_commit):
    envelope = _envelope()
    events = []
    class Repository(_Repository):
        async def lock_manifest(self, *args):
            events.append("topology/manifest")
            return await super().lock_manifest(*args)

        async def lock_collection_run(self, tenant, topology, actor, connection):
            events.append("collection")
            self.collection_connection = connection
            return connection.pending["collection"]

        async def create_analysis_run(self, values, connection):
            assert connection is self.collection_connection
            events.append("analysis")
            return await super().create_analysis_run(values, connection)

        async def bind_collection_analysis(self, collection, analysis, snapshot_id, connection):
            assert connection is self.collection_connection
            assert collection is connection.pending["collection"]
            events.append("link")
            collection.state = "SUBMITTED"
            collection.analysis_run_id = analysis.id

    repo = Repository(envelope, fail_commit=fail_commit)
    repo.state["collection"] = SimpleNamespace(state="COLLECTED", analysis_run_id=None)
    validated = validate_direct_snapshot("topology-1", envelope, body_size=len(orjson.dumps(envelope)), now=NOW)
    prepared = PreparedPayload("tenant-1", validated.payload_hash,
        len(validated.prepared_envelope.canonical_bytes), validated.prepared_envelope.canonical_bytes.decode())
    call = commit_prepared_snapshot("tenant-1", "topology-1", "account-1", validated, prepared,
        repository=repo, store=_PayloadStore(prepared), now=NOW)
    if fail_commit:
        with pytest.raises(RuntimeError, match="commit failure"):
            await call
        assert repo.state["collection"].state == "COLLECTED" and not repo.state["runs"]
    else:
        accepted = await call
        assert repo.state["collection"].analysis_run_id == accepted.sync_id
        assert repo.state["collection"].state == "SUBMITTED"
    assert events == ["topology/manifest", "collection", "analysis", "link"]
