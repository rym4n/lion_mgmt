# tests/network_access_control/policy_collection_fakes.py
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

from models.network_access_control import POLICY_RUN_TERMINAL_STATES
from services.network_access_control.collection import runs


def plan(plan_id="p1", **kw):
    values = dict(id=plan_id, tenant_id="t1", enabled=True, concurrency=4, per_item_timeout_seconds=300,
                  run_timeout_seconds=3600, notify_enabled=False, notify_config=None, consecutive_failures=0,
                  last_run_id=None, deleted=False)
    values.update(kw)
    return SimpleNamespace(**values)


def item(item_id="i1", resources=None, **kw):
    values = dict(id=item_id, tenant_id="t1", plan_id="p1", deleted=False,
                  app_name="huawei_usg", app_version=None, action_func="collect_policy_observations",
                  action_input={}, resources=resources if resources is not None else [
                      {"resource_id": "r1", "device_asset_id": "a1"}],
                  enabled=True, order=0)
    values.update(kw)
    return SimpleNamespace(**values)


def run_row(run_id="run-1", **kw):
    values = dict(id=run_id, tenant_id="t1", plan_id="p1", deleted=False,
                  trigger="manual", triggered_by="acc", state="PENDING",
                  flow_run_id=None, frozen_targets=None, raw_outputs=None, item_results=None, device_total=0,
                  device_succeeded=0, agent_job_id=None, compare_run_id=None, compare_error=None, error_code=None,
                  error_message=None, started_at=datetime.now(timezone.utc), finished_at=None, heartbeat_at=None)
    values.update(kw)
    return SimpleNamespace(**values)


class FakePolicyRunRepository:
    def __init__(self, plans=(), items=(), policy_counts=None):
        self.plans = {p.id: p for p in plans}
        self.items = list(items)
        self.runs = {}
        self.policy_counts = Counter(policy_counts or {})
        self._counter = 0

    async def get_plan(self, plan_id, *, tenant_id=None):
        row = self.plans.get(plan_id)
        return row if row is not None and not row.deleted and (tenant_id is None or row.tenant_id == tenant_id) else None

    async def get_plan_for_tenant(self, tenant_id):
        return next((p for p in self.plans.values() if p.tenant_id == tenant_id and not p.deleted), None)

    async def get_items(self, plan):
        return sorted((i for i in self.items if i.tenant_id == plan.tenant_id and i.plan_id == plan.id
                       and not i.deleted and i.enabled), key=lambda i: (i.order, i.id))

    async def active_run(self, tenant_id):
        return next((r for r in self.runs.values()
                     if r.tenant_id == tenant_id and not r.deleted
                     and r.state not in POLICY_RUN_TERMINAL_STATES), None)

    async def create_run(self, **values):
        if not values.get("tenant_id"):
            raise ValueError("Collection runs require an explicit tenant")
        if await self.active_run(values["tenant_id"]):
            raise runs.ActiveRunConflict
        self._counter += 1
        values.setdefault("id", f"run-{self._counter}")
        row = run_row(**values)
        self.runs[row.id] = row
        return row

    async def get_run(self, run_id, *, tenant_id=None):
        row = self.runs.get(run_id)
        return row if row is not None and not row.deleted and (tenant_id is None or row.tenant_id == tenant_id) else None

    async def transition(self, run, from_state, to_state, values):
        runs._validate_values(values)
        if from_state in POLICY_RUN_TERMINAL_STATES or to_state in POLICY_RUN_TERMINAL_STATES:
            return False
        row = self.runs.get(run.id, run)
        if row.state != from_state or row.tenant_id != run.tenant_id or row.deleted:
            return False
        for key, value in {**values, "state": to_state}.items():
            setattr(row, key, value)
            setattr(run, key, value)
        self.runs[run.id] = row
        return True

    async def save_fields(self, run, values):
        runs._validate_values(values)
        row = self.runs.get(run.id, run)
        if row.state in POLICY_RUN_TERMINAL_STATES or row.tenant_id != run.tenant_id or row.deleted:
            return False
        for key, value in values.items():
            setattr(row, key, value)
            setattr(run, key, value)
        return True

    async def finish_terminal(self, run, state, finished_at, values, *, expected_state=None, terminal_guard=None):
        runs._validate_values(values)
        if state not in POLICY_RUN_TERMINAL_STATES:
            raise ValueError("Expected a terminal collection run state")
        row = self.runs.get(run.id)
        if row is None or row.tenant_id != run.tenant_id or row.deleted:
            return False if expected_state is not None or terminal_guard is not None else None
        if (row.state in POLICY_RUN_TERMINAL_STATES
                or (expected_state is not None and row.state != expected_state)
                or (terminal_guard is not None and not terminal_guard(row))):
            run.state, run.finished_at = row.state, row.finished_at
            return False if expected_state is not None or terminal_guard is not None else None
        for key, value in {**values, "state": state, "finished_at": finished_at}.items():
            setattr(row, key, value)
            setattr(run, key, value)
        self.runs[run.id] = row
        plan_row = self.plans.get(row.plan_id)
        if plan_row is not None and not plan_row.deleted and plan_row.tenant_id == row.tenant_id:
            runs.account_plan(plan_row, row)
            return plan_row
        return None

    async def list_runs(self, tenant_id, page, page_size):
        rows = [r for r in self.runs.values() if r.tenant_id == tenant_id and not r.deleted]
        return rows[(page - 1) * page_size: page * page_size], len(rows)

    async def open_runs(self):
        return [r for r in self.runs.values() if not r.deleted and r.state not in POLICY_RUN_TERMINAL_STATES]

    async def count_policies(self, tenant_id, device_key):
        return self.policy_counts[device_key]

    async def clear_expired_raw_outputs(self, before):
        cleared = 0
        for row in self.runs.values():
            if (not row.deleted and row.state in POLICY_RUN_TERMINAL_STATES
                    and row.raw_outputs is not None and row.finished_at and row.finished_at < before):
                row.raw_outputs = None
                cleared += 1
        return cleared
