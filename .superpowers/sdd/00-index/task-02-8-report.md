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

## Round 1 review fixes

Run detail now requires and validates `body.id` before looking up the run. Plan save rejects nested `tenant`/`tenant_id` fields before topology lookup. Internal status resolves plans under `DEFAULT_TENANT_ID` and scopes run lookup to the plan tenant, preventing cross-tenant plan/run-id combinations. Regression coverage was added for all three cases.

Focused review-fix verification: `26 passed` across the collection views, runs, and wiring tests; `git diff --check` and `python3 -m compileall -q services core` pass.

## Dependency review: manifest failure accounting

The manifest endpoint now terminalizes an already resolved open run when item loading, target freezing, or manifest issuance fails. A `CollectionError` retains its stable code and HTTP status, with its potentially sensitive message omitted from both persisted accounting and the response. Other exceptions map to `COLLECTION_MANIFEST_FAILED` (503). Request validation and closed-run rejection remain outside this accounting boundary.

RED: six parameterized regressions failed before the change: known errors left runs PENDING and unexpected errors escaped. GREEN: `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_views.py tests/network_topology/test_auto_collection_runs.py tests/network_topology/test_auto_collection_dispatch.py tests/network_topology/test_auto_collection_wiring.py` reports `41 passed`. Tests exercise real `finish_run` using the in-memory repository, verify the active-run slot is released, check stable response/persistence codes, and verify raw exception text is absent. An additional closed-run regression verifies unresolved runs are never finished. Compileall and whitespace checks pass.

No live database or Kafka calls were exercised. Failure accounting still depends on successful persistence in `finish_run`; database write failure propagates rather than reporting a terminal transition that did not commit.

## Dependency review: post-commit notification isolation

`finish_run` now performs failure notifications after terminal persistence through an isolated helper. Notification/Kafka errors are logged with only the run id and cannot replace the stable manifest error response; persistence errors still propagate. Added regressions for a committed FAILED run with notification failure and for persistence failure propagation.

RED: six new notification-failure permutations escaped with the notification exception after the run became FAILED. GREEN: `49 passed` across collection views, runs, dispatch, and wiring tests; the original 409/503 responses and public codes survive notification failure. Tests also cover the atomic repository commit path, single failure accounting under repeated finish calls, log redaction, and persistence errors propagating. `python3 -m compileall -q services core` and `git diff --check` pass.

Notification delivery remains best effort with a sanitized warning on failure; this change adds no retry queue. No live notification delivery was exercised.
