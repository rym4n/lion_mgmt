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

    async def open_runs(self):
        return [r for r in self.runs.values() if r.state not in runs.RUN_TERMINAL_STATES]
