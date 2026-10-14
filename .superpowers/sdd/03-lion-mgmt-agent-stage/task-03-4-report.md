# Task 03.4 implementation report

Implemented collection-run `agent` summaries and maintenance AgentJob list/detail/rerun plus script-cache clear routes; registered the maintenance blueprint with the licensed topology module.

Collection detail selects the latest attempt for its analysis run under the authenticated tenant. No analysis/job returns `agent: null`. Ordinary output has state, resource count, cache hits/misses only; maintenance adds job/attempt/iterations/usage/profile/error/transcript diagnostics. Documents and token hashes never appear in collection detail.

All four maintenance handlers check current server-owned maintenance mode before reading the request body or querying jobs. Lists support explicit tenant/state filters across tenants with bounded pagination, stable newest ordering, and ORM values projection excluding documents, transcript and token hash. Detail returns explicitly selected metadata and decoded ORM-decrypted documents/transcript, omitting the token hash. Clear forwards only the validated explicit tenant to lion_agent's fixed internal path with the internal token, validates the removed count, and maps transport/upstream failures to safe AGENT_UNAVAILABLE responses.

Rerun fetches the job's tenant-bound analysis run, tenant/topology-bound snapshot row, and current topology. It passes the snapshot's external snapshot_id, current profile and current graph/publication CAS values to existing `reanalyze_snapshot`, with the maintenance session actor. New accepted responses use HTTP 202 and include `publish_as_current: true`: this operation can replace the current graph through the existing fenced publication path. CAS conflicts remain 409.

## Controller rulings / contract adaptations

- The brief names ErrorCode.PERMISSION_DENIED, which does not exist. Controller approved existing ErrorCode.FORBIDDEN as equivalent; used for every first-line gate.
- Explicit tenant_id is intentionally accepted for maintenance cross-tenant list and clear.
- Controller ruled `publish_as_current=True` with current CAS. False would always be rejected by `publishing._check_publication_preconditions` as PUBLISH_TARGET_CHANGED; nonpublication analysis support would expand the owning stage contract. Source comments and accepted result expose the publication behavior.
- The real analysis model stores snapshot_row_id, not snapshot_id; its tenant-bound snapshot row provides the external ID expected by reanalyze_snapshot.

## TDD / verification

Initial focused RED: missing maintenance module and agent summary/job lookup. Follow-up RED caught the missing snapshot lookup and incorrect False publication parameter; corrected against real model and controller ruling.

- Maintenance + collection run/view focused suite: **51 passed**, 23 existing Tortoise deprecation warnings.
- Full `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology`: **1107 passed, 2 failed**, 23 existing Tortoise warnings. Both failures are unchanged baseline authorization mocks rejecting refresh=:
  - test_phase2_analysis_runs.py::test_thin_routes_use_expected_permissions_and_safe_error_body
  - test_phase4_legacy_upgrade_api.py::test_route_checks_topology_update_permission
- Changed-file compileall and git diff --check pass.
- Existing full-suite reanalysis tests cover actual stale CAS rejection before enqueue; new maintenance adapter test verifies safe 409 propagation.

No deployed lion_agent/cache clear, PostgreSQL decryption/query/pagination, live rerun publication, browser UI or deployment was exercised. No remote push or migration was performed.
