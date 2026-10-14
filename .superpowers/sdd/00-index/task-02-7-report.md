# Task 02.7 evidence

## RED

`PYTHONPATH=. .venv/bin/pytest -q tests/network_topology/test_auto_collection_submit.py` failed during collection with `ImportError: cannot import name 'submit'`, before `submit.py` existed.

## GREEN

After implementing `collection.submit`, the focused contract tests pass: `2 passed`.
Related regression checks pass: `20 passed` for `test_auto_collection_runs.py` and `test_auto_collection_snapshot_builder.py`.

The tests cover direct ingress actor and size, assignment of both `sync_id` and `analysis_run_id`, draft clearing/state transition, chunk start/stage/commit ordering, observation count in the observation-free envelope, and canonical expected hash wiring.

## Concerns

Live ORM ingress, upload persistence, and analysis worker behavior were not exercised; tests use focused async fakes at those boundaries. Status returns the collection run projection after `runs.refresh_from_analysis`.
