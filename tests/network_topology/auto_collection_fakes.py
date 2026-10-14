from types import SimpleNamespace

from services.network_topology.collection import runs


def plan(plan_id, **kw):
    values = dict(id=plan_id, tenant_id="t1", topology_id="topo-1", enabled=True,
                  run_timeout_seconds=3600, notify_enabled=False, consecutive_failures=0,
                  last_run_id=None, deleted=False, items=[SimpleNamespace(id="item")])
    values.update(kw)
    return SimpleNamespace(**values)


class FakeRunRepository:
    def __init__(self, plans=()):
        self.plans = {item.id: item for item in plans}
        self.runs = {}
        self.analyses = {}
        self.topologies = {}
        self._counter = 0

    async def get_plan(self, plan_id):
        return self.plans.get(plan_id)

    async def get_plan_for_topology(self, tenant_id, topology_id):
        return next((p for p in self.plans.values() if p.tenant_id == tenant_id and p.topology_id == topology_id and not p.deleted), None)

    async def get_items(self, plan_id):
        return getattr(self.plans[plan_id], "items", [])

    async def active_run(self, tenant_id, topology_id):
        return next((r for r in self.runs.values() if r.tenant_id == tenant_id and r.topology_id == topology_id and r.state not in runs.RUN_TERMINAL_STATES), None)

    async def create_run(self, **values):
        if await self.active_run(values["tenant_id"], values["topology_id"]):
            raise runs.ActiveRunConflict
        self._counter += 1
        values.setdefault("id", f"run-{self._counter}")
        row = SimpleNamespace(device_total=0, device_succeeded=0, **values)
        self.runs[row.id] = row
        return row

    async def get_run(self, run_id, *, tenant_id=None):
        row = self.runs.get(run_id)
        return row if row is not None and (tenant_id is None or row.tenant_id == tenant_id) else None

    async def save_run(self, run):
        self.runs[run.id] = run

    async def save_plan(self, row):
        self.plans[row.id] = row

    async def get_analysis(self, run_id, *, tenant_id=None, topology_id=None):
        row = getattr(self, "analyses", {}).get(run_id)
        return row if row is not None and row.tenant_id == tenant_id and row.topology_id == topology_id else None

    async def get_topology(self, tenant_id, topology_id):
        row = self.topologies.get(topology_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def open_runs(self):
        return [r for r in self.runs.values() if r.state not in runs.RUN_TERMINAL_STATES]


def run_row(run_id, **kw):
    from datetime import datetime, timezone
    values = dict(id=run_id, tenant_id="t1", plan_id="p", topology_id="topo-1", state="MANIFEST_FROZEN", triggered_by=None,
                  started_at=datetime.now(timezone.utc), device_total=0, device_succeeded=0, frozen_targets=[])
    values.update(kw)
    return SimpleNamespace(**values)


class FakeTargetRepository:
    def __init__(self, *, resources, tools, actions):
        self.resources, self.tools, self.action_rows = resources, tools, actions

    async def resource(self, tenant_id, app_name, resource_id):
        return self.resources.get(resource_id)

    async def all_resources(self, tenant_id, app_name):
        return list(self.resources.values())

    async def latest_tool(self, tenant_id, app_name):
        return self.tools.get(app_name)

    async def tool(self, tenant_id, app_name, version):
        row = self.tools.get(app_name)
        return row if row and row[0] == version else None

    async def actions(self, tenant_id, app_name, version):
        return self.action_rows.get((app_name, version), [])


def raw_collection(resource_outputs):
    """Build a real auto-collection snapshot with all outputs grouped by resource."""
    from services.network_topology.collection.snapshot_builder import ItemOutcome, asset_context_for, build_snapshot
    manifest = {
        "manifest_id": "m1", "manifest_hash": "sha256:" + "1" * 64,
        "collection_sequence": 1, "base_graph_revision": 0,
        "base_publication_revision": 0, "profile_revision": 1,
        "issued_at": "2026-10-14T00:00:00Z", "expires_at": "2026-10-14T01:00:00Z",
        "expected_resources": [
            {"resource_id": resource_id, "capability_profile": "aioe-action-collection/v1",
             "required_kinds": ["raw.action_output"], "optional_kinds": []}
            for resource_id in resource_outputs
        ],
    }
    outcomes = [
        ItemOutcome(item_id=f"item-{index}", app_name="huawei_switch",
                    action_func="collect_topology_observations_v2", app_version="v",
                    resource_id=resource_id, status="success", output=output,
                    observed_at="2026-10-14T00:10:00Z", error_code=None,
                    error_message=None, duration_ms=5)
        for resource_id, outputs in resource_outputs.items()
        for index, output in enumerate(outputs)
    ]
    snapshot = build_snapshot(
        collection_run_id="run-1", manifest=manifest, profile_revision=1,
        outcomes=outcomes, asset_contexts={
            resource_id: asset_context_for("SW1", "10.0.0.1", "huawei_switch", None, resource_id)
            for resource_id in resource_outputs
        }, started_at="2026-10-14T00:05:00Z", ended_at="2026-10-14T00:20:00Z",
    )
    from services.network_topology.run_pipeline import split_collection
    return snapshot, list(split_collection(snapshot).agent_observation_ids)


class FakeAgentJobRepository:
    def __init__(self, collection=None):
        self.jobs = []
        self.collection = collection

    async def create_job(self, **values):
        job = SimpleNamespace(id=f"job-{len(self.jobs) + 1}", documents=None, **values)
        self.jobs.append(job)
        return job

    async def jobs_for(self, analysis_run_id, *, tenant_id):
        return [j for j in self.jobs if j.analysis_run_id == analysis_run_id and j.tenant_id == tenant_id]

    async def save_job(self, job):
        pass

    async def get_job(self, job_id, *, tenant_id):
        return next(j for j in self.jobs if j.id == job_id and j.tenant_id == tenant_id)

    def mark_submitted(self, job_id):
        next(j for j in self.jobs if j.id == job_id).state = "SUBMITTED"

    async def collection_run_id_for(self, analysis_run_id, *, tenant_id):
        return "run-1"

    async def load_collection(self, run):
        return self.collection
