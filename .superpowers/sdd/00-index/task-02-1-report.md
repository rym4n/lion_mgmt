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

## Review fix round 1

Updated `models.network_topology_v2.__all__` to include `RUN_STATES`, `RUN_TERMINAL_STATES`, `TopologyCollectionItem`, `TopologyCollectionPlan`, and `TopologyCollectionRun`. Updated the exact facade contract test and added focused export coverage; model-inventory tests now ignore the two constants when iterating model classes.

Focused review command:

```text
PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology/test_auto_collection_models.py tests/network_topology/test_phase1_models.py tests/network_topology/test_phase1_model_inventory.py tests/network_topology/test_phase1_model_relations.py
23 passed, 23 warnings in 2.66s
```

Full suite:

```text
PYTHONPATH=. uv run --frozen --with pytest pytest -q tests/network_topology
951 passed, 2 failed, 23 warnings in 21.67s
```

The remaining two failures are the documented permission-mock baseline failures.
