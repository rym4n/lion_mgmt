# Task 2 Report: Submit Interface

## Scope

Implemented the one-shot policy-agent submission flow in `lion_mgmt`:

- Added `agent_jobs.check_submission` for schema, raw-output evidence, failed-code, and exact disjoint resource coverage validation.
- Added `agent_jobs.clean_failed` to scrub failed messages and cap persisted text at 512 characters.
- Added `agent_jobs.store_submission` with a tenant-scoped, token-bound, timeout-bound `RUNNING` to `SUBMITTED` compare-and-set update that succeeds only when exactly one row changes.
- Added `POST /network-access-control/internal/policy-agent-facts/submit`, preserving the shared `authenticate_job(..., timeout_seconds=1800)` contract.
- Added focused endpoint tests covering accepted documents, fabricated raw text, incomplete/overlapping/out-of-scope coverage, failed-entry scrubbing, invalid codes, and replay rejection.

## Validation

The test-first RED run failed because `store_submission` was absent. After implementation:

```text
PYTHONPATH=. uv run pytest -q tests/network_access_control/test_policy_agent_submit.py
9 passed, 23 warnings

PYTHONPATH=. uv run pytest -q tests/network_access_control/test_policy_agent_submit.py tests/network_access_control/test_policy_agent_input.py
16 passed, 23 warnings

uv run python -m compileall -q services/network_access_control/collection/agent_jobs.py services/network_access_control/collection/agent_views.py tests/network_access_control/test_policy_agent_submit.py
exit 0

git diff --check
exit 0
```

The warnings are existing dependency deprecation warnings from Tortoise field declarations.
