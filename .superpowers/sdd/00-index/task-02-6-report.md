# Task 02.6 evidence

## RED

Added focused dispatch, target freezing, and output failure tests. The first run failed at collection because the three production modules did not exist (`ImportError: cannot import name dispatch`).

## GREEN

Implemented `targets.py`, `manifest_issue.py`, and `dispatch.py`. The focused suite passes:

```
.venv/bin/python -m pytest tests/network_topology/test_auto_collection_dispatch.py -q
6 passed
```

Coverage includes semaphore concurrency, action/output failure handling, missing-resource all-or-nothing behavior, latest-tool/resource expansion and host extraction, persisted result redaction, and device counting.

## Concerns

Manifest persistence relies on the existing manifest/profile database seams and requires a live configured Tortoise connection for integration coverage. Background recovery remains delegated to `sweep_runs` as specified.
