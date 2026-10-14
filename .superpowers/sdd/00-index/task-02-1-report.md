# Task 02.1 report

## RED

Before implementation, the required model test attempted to import the new symbols from `models.network_topology_v2` and failed with `ImportError` (symbols did not yet exist).

## GREEN

Focused command:

```text
PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_models.py
.                                                                        [100%]
1 passed, 23 warnings in 1.20s
```

Compilation:

```text
python3 -m compileall -q models migrations
# no output
```

Full network topology suite:

```text
PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology
949 passed, 3 failed, 23 warnings in 21.76s
```

The three failures are the two documented baseline permission-mock failures plus the existing exact public-facade `__all__` assertion, which expects the pre-task model list. The new models remain importable as module attributes while preserving that established facade contract.

## Changes

- Added tenant-scoped `TopologyCollectionPlan`, `TopologyCollectionItem`, and `TopologyCollectionRun` models and run-state constants.
- Exported the new model symbols and constants as attributes from `models.network_topology_v2`.
- Added migration 132 with BaseModel columns, JSONB/TEXT mappings, conditional unique plan and active-run indexes, and reversible async downgrade.
- Added model defaults/table coverage test.
