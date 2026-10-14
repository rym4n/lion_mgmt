# Task 02.7 evidence

## RED

`PYTHONPATH=. .venv/bin/pytest -q tests/network_topology/test_auto_collection_submit.py` failed during collection with `ImportError: cannot import name 'submit'`, before `submit.py` existed.

## GREEN

After implementing `collection.submit`, the focused contract tests pass: `2 passed`.
Related regression checks pass: `20 passed` for `test_auto_collection_runs.py` and `test_auto_collection_snapshot_builder.py`.

The tests cover direct ingress actor and size, assignment of both `sync_id` and `analysis_run_id`, draft clearing/state transition, chunk start/stage/commit ordering, observation count in the observation-free envelope, and canonical expected hash wiring.

## Concerns

Live ORM ingress, upload persistence, and analysis worker behavior were not exercised; tests use focused async fakes at those boundaries. Status returns the collection run projection after `runs.refresh_from_analysis`.

## Round 1 review fixes

Upload start/stage/commit failures now call `runs.finish_run(..., FAILED, error_code="UPLOAD_<code>")`; malformed JSON and canonicalization errors use `SNAPSHOT_INVALID` and are terminalized. In-flight status copies tenant/topology-scoped analysis state, stage, graph revision, and errors through `runs.refresh_from_analysis`.

Added tests for all three upload failure points, malformed drafts, and RECEIVED/RUNNING analysis status. Focused suite: `9 passed`.

The upload envelope contract requires `expected_observation_count >= 2`; chunked submission rejects any resource with fewer observations as `SNAPSHOT_INVALID` so it cannot send an invalid envelope.

## Round 2 ledger ruling

The upload envelope minimum is now 1, matching collection/v2 raw-only capability output. Chunk submission no longer rejects a valid one-observation resource; zero remains rejected by the packaged JSON Schema. Added contract validation tests for both cases and an end-to-end one-observation chunk path.

Analysis graph/error values are kept as transient `_analysis_*` status projection fields, leaving persisted collection terminal accounting untouched. Focused suite: `11 passed`.
