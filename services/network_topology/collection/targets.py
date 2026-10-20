"""Topology re-export of the shared target freezer."""
from services.action_collection.targets import (  # noqa: F401
    OrmTargetRepository,
    Target,
    TargetRepository,
    freeze_targets,
)
