# Task 01.1 Report

## Implementation

- Added `AnalysisRunStage.AGENT_NORMALIZING` between `PARSING` and `VALIDATING_FACTS` in the enum and lease stage order.
- Added nullable encrypted `TopologyAnalysisRun.graph_payload` and nullable JSON `agent_observation_ids` fields.
- Added migration `131_20261014150000_topology_analysis_agent_stage.py` to create the corresponding nullable `TEXT` and `JSONB` columns and drop them on downgrade.
- Kept the stage column type unchanged: existing migration `097` defines it as `VARCHAR(20)`, which accommodates `AGENT_NORMALIZING` (18 characters).
- Added focused coverage and updated frozen model/state-machine contracts for the new stage and fields.

## Tests

Commands run from `/opt/dev/aioe/.worktrees/network-topology-auto-collection/lion_mgmt`:

1. `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_stage_order.py` before implementation: exit 1; `2 failed, 23 warnings in 1.36s`. The expected missing enum (`AttributeError: AGENT_NORMALIZING`) and field (`KeyError: 'graph_payload'`) failures confirmed the test was red.
2. Same focused command after implementation: exit 0; `2 passed, 23 warnings in 1.32s`.
3. `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_phase1_models.py tests/network_topology/test_phase3_worker.py`: exit 0; `31 passed, 23 warnings in 3.70s`.
4. `PYTHONPATH=. uv run --frozen python -m compileall -q models/_network_topology_v2_base.py models/_network_topology_v2_analysis.py services/network_topology/leases.py migrations/models/131_20261014150000_topology_analysis_agent_stage.py`: exit 0; no output.
5. `git diff --check`: exit 0; no output.
6. `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology`: exit 1; `4 failed, 879 passed, 23 warnings in 24.83s`.

The four full-suite failures are unchanged from the pre-task baseline provided by the parent task (`4 failed, 877 passed`):

- `test_status_example_matches_machine_contract_and_rejects_stage_as_state`: missing `docs/网络拓扑模块优化SPEC.md` in the aggregate worktree.
- `test_chunk_example_is_single_observation_and_rejects_batch_shape`: same missing spec document.
- `test_thin_routes_use_expected_permissions_and_safe_error_body`: test mock rejects the `refresh` keyword passed by the authorization decorator.
- `test_route_checks_topology_update_permission`: test mock rejects the `refresh` keyword passed by the authorization decorator.

The two additional passing tests account for the pass-count increase from 877 to 879. The five initially task-related contract failures were resolved by updating the frozen stage/model expectations and transition assertion.

## Files Changed

- `models/_network_topology_v2_base.py`
- `models/_network_topology_v2_analysis.py`
- `services/network_topology/leases.py`
- `migrations/models/131_20261014150000_topology_analysis_agent_stage.py`
- `tests/network_topology/test_auto_collection_stage_order.py`
- `tests/network_topology/test_phase1_models.py`
- `tests/network_topology/test_phase3_worker.py`

## Self Review And Concerns

- The migration is additive and transactional; it does not alter tenant ownership or add timezone-naive time values.
- The `graph_payload` model field uses the existing encrypted text field type; `agent_observation_ids` is JSON as specified for the parsing-stage list of string IDs.
- The migration was compile-checked but not applied against a live PostgreSQL database in this task.
- The network-topology suite remains red only on the four pre-existing failures listed above. No unrelated failures were introduced according to the supplied baseline.
