# Task 9 implementation report

## Scope and changes

- Added `scripts/smoke_policy_collection.py`, requiring `--confirmed-test-database`, using `settings.TORTOISE`, and cleaning all rows for its unique tenant in `finally`. The fixture follows current `AssetModel`, `AssetInstance`, preset policy, collection run, and executor contracts.
- The first real PostgreSQL run found a production defect in `pipeline.apply_stage`: a UUID run ID plus UUID resource ID created a 73-character batch ID, rejected by `manager.apply_actual_policies`' 64-character field limit. Added `_policy_batch_id`, retaining readable IDs at or below 64 characters and using deterministic SHA-256 hex for longer IDs; added a two-resource bounded/unique regression test.

## RED and argument gate

- Before implementation: `PYTHONPATH=. uv run --frozen python -c "import ast,sys; ast.parse(open('scripts/smoke_policy_collection.py').read()); print('PARSE_OK')"` failed with `FileNotFoundError` because the script did not exist.
- After implementation, AST parse printed `PARSE_OK`.
- `PYTHONPATH=. uv run --frozen python scripts/smoke_policy_collection.py` exited 1 and printed exactly `Refusing to run without --confirmed-test-database`.

## Disposable PostgreSQL 16 evidence

- Created only `aioe-task9-policy-smoke-20261020` from local `postgres:16`, published to localhost on ephemeral port 32769, using throwaway database `task9_policy_smoke`, user `task9_smoke_user`, and password `task9_throwaway_pass`. Installed pgvector in that container because historical migration 10 requires it; no project migration was edited.
- Initial Aerich migration attempt against plain PostgreSQL failed at historical migration 10 with `FeatureNotSupportedError: extension "vector" is not available`. After installing pgvector inside the task-owned container, `uv run aerich upgrade` exited 0 and reported all migrations through `135_20261020100000_network_policy_collection.py` successful. Confirmed Aerich’s latest version row is migration 135.
- The successful smoke command was:
  `RY_DB_HOST=127.0.0.1 RY_DB_PORT=32769 RY_DB_USER=task9_smoke_user RY_DB_PASS=task9_throwaway_pass RY_DB_NAME=task9_policy_smoke RY_RES_KEY=task9_throwaway_resource_key_32_chars uv run python scripts/smoke_policy_collection.py --confirmed-test-database`
  It exited 0 and printed `SMOKE OK`. `RY_RES_KEY` is required by this local settings configuration and was set to a disposable throwaway value.
- An earlier smoke attempt exposed the oversized batch ID defect described above. A preliminary invocation also showed the sample output needed the actual `{code: 200, data: {outputs: [...]}}` action envelope; the script fixture now uses that envelope.
- Stopped and removed only the task-owned container after the successful smoke.

## Regression checks and review

- `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_access_control/ tests/test_network_access_policy_contract.py tests/test_access_control_full_field_search.py tests/network_topology/`: **1537 passed, 23 warnings** (warnings are existing Tortoise deprecations).
- `python3 -m compileall -q scripts/smoke_policy_collection.py services/network_access_control/collection/pipeline.py tests/network_access_control/test_policy_collection_pipeline_collect.py`: passed.
- `git diff --check`: passed.
- Scope includes the requested smoke script plus the minimal production fix and regression test required by its real-DB finding. No source migrations, shared databases, generated schemas, deployments, pushes, or merges were used.
