# Task 02.8 evidence

## RED

Before the HTTP adapter existed, the required route registration and internal caller tests could not import `services.network_topology.collection.views` because the module did not exist. The collection summary batch test also had no implementation to exercise.

## GREEN

Implemented `collection_bp` with plan, run, and internal collection routes. User routes derive tenant and actor identity from the session, validate topology ownership, and hide `timed_id`; internal routes require the default tenant, reject body tenant fields, and map `CollectionError` to the required `{code: 4000, msg, data.error_code}` shape. Added `get_run_for_plan`, batched topology collection summaries, delete cleanup, blueprint registration, and a cancellable 60-second run sweep task.

Focused verification:

```text
PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_views.py
4 passed

PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_views.py tests/network_topology/test_auto_collection_wiring.py
13 passed

PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_wiring.py tests/network_topology/test_auto_collection_plans.py tests/network_topology/test_auto_collection_runs.py
37 passed

python3 -m compileall -q services core
```

## Concerns

The full `tests/network_topology` run has two unrelated pre-existing permission-wrapper failures where test fakes reject the decorator's `refresh=` keyword; the remaining 1027 tests passed. No live database, Kafka, or production sweep execution was performed.
