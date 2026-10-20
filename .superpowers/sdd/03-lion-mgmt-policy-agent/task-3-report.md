# Task 3 Report: AgentNormalizer

## Scope

Implemented the policy collection `NORMALIZING` stage in `lion_mgmt`:

- Added `PolicyNormalizerClient` for the authenticated lion_agent policy-normalizer call.
- Added tenant-scoped `OrmPolicyAgentJobRepository` with per-run job attempts and raw-output loading.
- Added `AgentNormalizer` and `build_agent_normalizer`, including remaining-time timeout calculation, LLM configuration handling, terminal job lifecycle, agent error mapping, redacted summaries, and per-resource results.
- Revalidated every submitted document against the shared schema and raw-output evidence rules before returning successful `NormalizeResult` values.
- Added fake repository/client fixtures and focused lifecycle, timeout, error, redaction, missing-document, fabricated-document, retry, and factory tests.

## Validation

The test-first RED run failed during collection because `agent_stage.py` did not exist. After implementation:

```text
PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_access_control/test_policy_agent_stage.py
15 passed, 23 warnings

PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_access_control
383 passed, 23 warnings

uv run python -m compileall -q services/network_access_control/collection/agent_stage.py tests/network_access_control/policy_agent_fakes.py tests/network_access_control/test_policy_agent_stage.py
exit 0

git diff --check
exit 0
```

Warnings are existing Tortoise field deprecation warnings.
