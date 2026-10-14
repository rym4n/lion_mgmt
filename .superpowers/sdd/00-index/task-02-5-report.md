# Task 02.5 Evidence

## RED

The required lifecycle tests were added in `tests/network_topology/test_auto_collection_runs.py` with the injectable `FakeRunRepository` seam. Before implementation, importing `services.network_topology.collection.runs` failed because the module did not exist.

## GREEN

Implemented `services/network_topology/collection/runs.py` with:

- scheduled and manual run creation, active-run fencing, and Kafka flow submission;
- plan/run ownership and terminal-state guards, timeout closure, cancellation, and analysis refresh;
- terminal state accounting, failure notification action publishing, pagination/detail helpers, and sweep API;
- ORM repository tenant/deleted filters and `IntegrityError` to `ActiveRunConflict` translation.

The focused test command passed:

```text
PYTHONPATH=. uv run --project . pytest -q tests/network_topology/test_auto_collection_runs.py
5 passed, 23 warnings in 1.56s
```

Syntax and whitespace checks also passed:

```text
python3 -m py_compile services/network_topology/collection/runs.py tests/network_topology/auto_collection_fakes.py tests/network_topology/test_auto_collection_runs.py
git diff --check
```

## Concerns / limits

- PostgreSQL uniqueness, Kafka delivery, and analysis-worker integration were not exercised in this local fake-repository test; these remain external smoke checks.
- Listener scheduling is intentionally left for Task 02.8; `sweep_runs()` is implemented as the callable lifecycle API.
- The unchanged result path uses the available topology and analysis `graph_hash` values; a deployment-specific snapshot hash adapter may be needed if those fields are not populated by the publisher.

## Round 1 Fix Evidence

- `finish_run` now rejects nonterminal target states, claims terminal state once (row-locked in the ORM repository), and only notifies on the failure threshold crossing at three failures. Repeated terminalization is a no-op.
- Manual flow publication uses `raise_on_error=True`; broker failure marks the created run `FAILED` with `COLLECTION_FLOW_SUBMIT_FAILED` before re-raising.
- Analysis refresh scopes both repository and ORM lookups by tenant and topology.
- Sweep timeout handling falls back to the run timeout contract (3600 seconds) when the plan is missing or deleted.

Round 1 focused verification:

```text
PYTHONPATH=. uv run --project . pytest -q tests/network_topology/test_auto_collection_runs.py
7 passed, 23 warnings in 1.57s
```
