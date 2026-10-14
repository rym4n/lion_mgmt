# Task 03.1 Report

Implemented `TopologyAgentJob` and migration `133_20261014170000_topology_agent_job.py`.

## Changes

- Added the tenant-scoped `TopologyAgentJob` ORM model and exported it from `models.network_topology_v2`.
- Added durable job fields, encrypted `documents`/`transcript`, and tenant-prefixed analysis/collection indexes.
- Added repeatable PostgreSQL DDL and downgrade for `network_topology_agent_job`.
- Added the focused model contract test.

## Verification

- `PYTHONPATH=. uv run pytest -q tests/network_topology/test_auto_collection_agent_job_model.py` -> **1 passed** (23 dependency deprecation warnings).
- `PYTHONPATH=. uv run python -m compileall -q models/_network_topology_v2_auto_collection.py models/network_topology_v2.py migrations/models/133_20261014170000_topology_agent_job.py` -> **passed**.
- `git diff --check` -> **passed**.

The worktree already contained unrelated modifications in `services/network_topology/collection/runs.py`, `services/network_topology/collection/views.py`, and `tests/network_topology/test_auto_collection_views.py`; they were preserved and not staged.
