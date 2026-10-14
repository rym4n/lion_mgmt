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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["start", "stage", "commit"])
async def test_upload_failure_terminalizes_collection(monkeypatch, failure_stage):
    from services.network_topology.collection.errors import CollectionError
    monkeypatch.setattr(submit.settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", 10, raising=False)
    async def start(*args, **kwargs):
        if failure_stage == "start":
            raise submit.topology_upload.UploadError("UPLOAD_CONFLICT")
        return {"upload_session_id": "u1"}
    async def stage(*args, **kwargs):
        if failure_stage == "stage":
            raise submit.topology_upload.UploadError("UPLOAD_CONFLICT")
    async def commit(*args, **kwargs):
        raise submit.topology_upload.UploadError("UPLOAD_CONFLICT")
    async def finish(run, state, **kwargs):
        run.state = state
        run.error_code = kwargs["error_code"]
    monkeypatch.setattr(submit.topology_upload, "start_upload", start)
    monkeypatch.setattr(submit.topology_upload, "stage_chunk", stage)
    monkeypatch.setattr(submit.topology_upload, "commit_upload_session", commit)
    monkeypatch.setattr(submit.runs, "finish_run", finish)
    run = _run(_snapshot()); run.state = "COLLECTED"
    with pytest.raises(CollectionError) as exc:
        await submit.submit_snapshot(SimpleNamespace(topology_id="topo-1"), run)
    assert exc.value.code == "UPLOAD_CONFLICT" and exc.value.status == 409
    assert run.state == "FAILED" and run.error_code == "UPLOAD_UPLOAD_CONFLICT"
    assert run.snapshot_draft is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", ["{invalid", '{"sources":[{}]}'])
async def test_invalid_draft_terminalizes_collection(monkeypatch, draft):
    from services.network_topology.collection.errors import CollectionError
    async def finish(run, state, **kwargs):
        run.state = state
        run.error_code = kwargs["error_code"]
    monkeypatch.setattr(submit.runs, "finish_run", finish)
    monkeypatch.setattr(submit.settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", 1, raising=False)
    run = _run(_snapshot()); run.state = "COLLECTED"; run.snapshot_draft = draft
    with pytest.raises(CollectionError) as exc:
        await submit.submit_snapshot(SimpleNamespace(topology_id="topo-1"), run)
    assert exc.value.code == "SNAPSHOT_INVALID"
    assert run.state == "FAILED" and run.error_code == "SNAPSHOT_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("state, stage", [("RECEIVED", "RECEIVED"), ("RUNNING", "PARSING")])
async def test_status_projects_scoped_inflight_analysis(monkeypatch, state, stage):
    class Repo:
        async def get_analysis(self, run_id, *, tenant_id, topology_id):
            assert (run_id, tenant_id, topology_id) == ("analysis-1", "tenant-1", "topo-1")
            return SimpleNamespace(state=state, stage=stage, graph_revision=8, error_code="TEMPORARY_NETWORK_FAILURE", error_message="Retry pending")
    monkeypatch.setattr(submit.runs, "_repo", lambda repository: Repo())
    run = _run(_snapshot()); run.state = "SUBMITTED"; run.analysis_run_id = "analysis-1"
    run.graph_revision = 3; run.error_code = None; run.error_message = None
    data = await submit.status(SimpleNamespace(topology_id="topo-1"), run)
    assert data["run_state"] == "SUBMITTED" and data["terminal"] is False
    assert (data["analysis_state"], data["stage"]) == (state, stage)
    assert data["graph_revision"] == 8
    assert run.graph_revision == 3 and run.error_code is None and run.error_message is None
    assert data["error_code"] == "TEMPORARY_NETWORK_FAILURE"
    assert data["error_message"] == "Retry pending"


def _valid_single_snapshot():
    from tests.network_topology.test_auto_collection_snapshot_builder import _build, _outcome
    return _build([_outcome("item-a")])


def test_upload_contract_accepts_one_observation_and_rejects_zero():
    from services.network_topology import contracts
    snapshot = _valid_single_snapshot()
    envelope = submit._without_observations(snapshot)
    payload = {"id": "topo-1", "manifest_id": "m1", "snapshot_envelope_without_observations": envelope,
               "client_expected_input_hash": "sha256:" + "0" * 64}
    contracts.validate_contract(payload, submit.topology_upload.UPLOAD_START_SCHEMA)
    envelope["sources"][0]["resources"][0]["result"]["expected_observation_count"] = 0
    with pytest.raises(contracts.ContractError):
        contracts.validate_contract(payload, submit.topology_upload.UPLOAD_START_SCHEMA)


@pytest.mark.asyncio
async def test_single_raw_observation_uses_chunk_ingress_with_valid_contract(monkeypatch):
    from services.network_topology import contracts
    monkeypatch.setattr(submit.settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", 10, raising=False)
    calls = []
    async def start(authority, payload):
        contracts.validate_contract(payload, submit.topology_upload.UPLOAD_START_SCHEMA)
        assert payload["snapshot_envelope_without_observations"]["sources"][0]["resources"][0]["result"]["expected_observation_count"] == 1
        calls.append("start")
        return {"upload_session_id": "u1"}
    async def stage(owner, payload):
        contracts.validate_contract(payload, submit.topology_upload.UPLOAD_CHUNK_SCHEMA)
        calls.append("stage")
    async def commit(*args, **kwargs):
        calls.append("commit")
        return submit.topology_snapshot.SyncAccepted("a1", "s1")
    monkeypatch.setattr(submit.topology_upload, "start_upload", start)
    monkeypatch.setattr(submit.topology_upload, "stage_chunk", stage)
    monkeypatch.setattr(submit.topology_upload, "commit_upload_session", commit)
    monkeypatch.setattr(submit, "_save_run", lambda run: None)
    run = _run(_valid_single_snapshot())
    await submit.submit_snapshot(SimpleNamespace(topology_id="topo-1"), run)
    assert calls == ["start", "stage", "commit"]
    assert run.state == "SUBMITTED" and run.analysis_run_id == "a1"
