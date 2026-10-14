from types import SimpleNamespace
import orjson
import pytest

from services.network_topology.collection import submit


def _run(snapshot):
    return SimpleNamespace(id="run-1", tenant_id="tenant-1", topology_id="topo-1", snapshot_draft=orjson.dumps(snapshot).decode(), state="COLLECTING", sync_id=None, snapshot_id=None, analysis_run_id=None)


def _snapshot():
    return {"target_manifest": {"manifest_id": "m1"}, "collector": {"playbook_id": "aioe-builtin-network-topology-collection", "run_id": "run-1"}, "sources": [{"action_id": "a", "action_version": "1", "resources": [{"resource_id": "r1", "result": {"status": "success", "observations": [{"observation_id": "o1", "kind": "raw.action_output"}]}}]}]}

@pytest.mark.asyncio
async def test_small_snapshot_uses_direct_ingest_and_sets_analysis_run(monkeypatch):
    calls = []
    async def fake_ingest(tenant_id, topology_id, actor, envelope, *, body_size, **kw):
        calls.append((tenant_id, topology_id, actor, body_size))
        return SimpleNamespace(sync_id="s1", snapshot_id="snap-1", status="RECEIVED")
    monkeypatch.setattr(submit.topology_snapshot, "ingest_direct_snapshot", fake_ingest)
    monkeypatch.setattr(submit, "_save_run", lambda run: calls.append("saved"))
    run = _run(_snapshot()); plan = SimpleNamespace(id="p1", topology_id="topo-1")
    data = await submit.submit_snapshot(plan, run)
    assert calls[0][:3] == ("tenant-1", "topo-1", "collection-run:run-1")
    assert data["sync_id"] == "s1"
    assert run.analysis_run_id == "s1" and run.state == "SUBMITTED" and run.snapshot_draft is None

@pytest.mark.asyncio
async def test_large_snapshot_stages_each_observation_with_contract_envelope(monkeypatch):
    monkeypatch.setattr(submit.settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", 10, raising=False)
    snapshot = _snapshot(); snapshot["sources"][0]["resources"][0]["result"]["observations"].append({"observation_id": "o2", "kind": "x"})
    calls = []
    async def start(authority, payload): calls.append(("start", authority, payload)); return {"upload_session_id": "u1"}
    async def stage(owner, payload): calls.append(("stage", owner, payload)); return {}
    async def commit(owner, sid, *, expected_payload_hash): calls.append(("commit", owner, sid, expected_payload_hash)); return SimpleNamespace(sync_id="s2", snapshot_id="snap-2", status="RECEIVED")
    monkeypatch.setattr(submit.topology_upload, "start_upload", start); monkeypatch.setattr(submit.topology_upload, "stage_chunk", stage); monkeypatch.setattr(submit.topology_upload, "commit_upload_session", commit)
    monkeypatch.setattr(submit, "_save_run", lambda run: None)
    run = _run(snapshot)
    data = await submit.submit_snapshot(SimpleNamespace(topology_id="topo-1"), run)
    assert [c[0] for c in calls] == ["start", "stage", "stage", "commit"]
    envelope = calls[0][2]["snapshot_envelope_without_observations"]
    assert "observations" not in envelope["sources"][0]["resources"][0]["result"]
    assert envelope["sources"][0]["resources"][0]["result"]["expected_observation_count"] == 2
    assert run.analysis_run_id == "s2" and data["sync_id"] == "s2"
