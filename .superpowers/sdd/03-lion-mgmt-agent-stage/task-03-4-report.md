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

## Review fix 2: original snapshot diagnostic reruns and list contract

This section supersedes the earlier publish_as_current=True ruling. Integration review established that an already-published original snapshot cannot be published again: publication requires a strictly newer collection sequence, and publication profile guards reject a newly selected current profile against the original snapshot profile. A 202 with True would therefore enqueue an inevitably failing rerun.

The maintenance endpoint now requests and reports `publish_as_current: false`. The narrow diagnostic predicate is **analysis_kind=REANALYZE and publish_as_current=False**. These runs use the existing pipeline to reparse, normalize, commit formal facts, resolve identities, project a graph with the selected current profile, and validate that persisted graph. Only after successful VALIDATING_GRAPH execution does the worker call fenced mark_succeeded and finish at that stage. It never enters PUBLISHING or calls the graph publisher. The publish feature gate does not block diagnostic graph construction. Lease claim still requires existing topology/snapshot/profile and matching snapshot topology, but intentionally does not apply publication freshness, snapshot-profile equality, current-profile equality, or publication CAS freshness to diagnostic work. The request's existing enqueue-time CAS validation remains in analysis_runs.reanalyze_snapshot.

Fenced success allows VALIDATING_GRAPH only for that diagnostic predicate; ordinary publication still requires PUBLISHING. Cancellation and stale generation/token checks still block success. Persisted facts/graph payloads and hashes stay on the new analysis run, while current topology graph/snapshot/analysis pointers, sequence and revisions remain unchanged. No changes to publication.py/publishing.py or the graph pipeline were needed; existing pipeline validation already provides the required graph-validation boundary.

The maintenance job-list response now uses `data.list`, matching the frontend API contract, with total/page/page_size unchanged. Its focused test explicitly rejects the previous items key.

TDD evidence: the initial revised suite had six failures: original snapshot tried to publish, changed-profile/older snapshots could not be claimed, the narrow diagnostic claim was rejected, and the endpoint still returned items/True. Those failures now pass. A subsequent RED demonstrated mark_succeeded incorrectly accepting PUBLISHING for a diagnostic run; it now requires VALIDATING_GRAPH for diagnostics.

Verification:

- Focused diagnostic reanalysis + maintenance + lease/worker + graph pipeline + reanalysis contract suite: **98 passed, 1 deselected** (the known baseline authorization-mock test), 23 existing Tortoise warnings.
- Full `PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology`: **1124 passed, 2 failed**, 23 existing Tortoise warnings. Failures remain exactly the two baseline refresh= mock failures recorded above.
- New tests execute the real AnalysisPipeline and TopologyAnalysisWorker from SPLITTING through validated graph completion using in-memory repositories and golden structured inputs. Cases cover same already-published snapshot, selected profile changed from 3 to 4, a newer published collection, disabled publishing, persisted valid facts/graph and hashes, no publisher calls, unchanged topology state, invalid graph failure, stale fencing, cancellation, retained existence/snapshot binding guards, and ordinary publishing behavior preservation.
- Changed-file compileall and git diff --check pass.

No live PostgreSQL, deployed worker/agent, migration, external publication or deployment was exercised. Diagnostic identity resolution still uses the existing durable identity registry, as ordinary analysis does; this change prevents graph publication rather than introducing a wholly read-only analysis pipeline.

## Review fix 3: collection detail UI projection

Completed the real collection-run/detail response contract used by the Task 06.4 dialog: item_results is always an array, and analysis_state/stage are present (null before an associated analysis exists). Active SUBMITTED runs call the existing scoped refresh_from_analysis, preserving its idempotent terminal persistence/public error mapping. Terminal rows read their associated analysis for the timeline without refreshing or repeating terminal accounting. Both paths share a tenant/topology/deleted-bound analysis lookup. The collection list contract remains items/collection_run_id; Task 06.4 frontend agent confirmed its matching changes.

Item rows explicitly project the ItemOutcome metadata used by the UI. Raw output, inputs, token-shaped extra fields, frozen targets, snapshot drafts and worker summaries are absent; executor failure message text is replaced with a stable error code because persisted remote messages may contain credentials. Ordinary Agent summary still contains counters only; maintenance Agent diagnostics remain gated as before.

TDD: six new detail tests initially failed on absent analysis_state/stage/item_results. Final focused maintenance + collection run/submit/view suite: **72 passed**, with 23 existing Tortoise warnings. Full topology suite: **1131 passed, 2 failed**, exactly the same baseline authorization-mock refresh= failures above. Added coverage includes active/terminal timelines, failed refresh accounting only once, empty item defaults, other tenant/topology analysis refusal, actual ORM filter scoping, and no raw result/extra token/worker/ordinary transcript leak. Compileall and git diff --check pass. No live browser, database, deployment or lion_agent test was performed.
