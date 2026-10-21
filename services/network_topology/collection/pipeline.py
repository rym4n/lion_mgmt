"""Backend orchestration from a saved collection plan to topology ingress."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from services.action_collection.background import start_tracked  # noqa: F401
from services.network_topology import manifest
from services.network_topology.collection import runs
from services.network_topology.collection.dispatch import collect_and_store
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.manifest_issue import issue_action_manifest
from services.network_topology.collection.submit import submit_snapshot
from services.network_topology.collection.targets import freeze_targets

logger = logging.getLogger(__name__)


async def run_pipeline(plan_id, collection_run_id, *, repository=None):
    """Run collection stages directly, checking durable state at each boundary."""
    repo = runs._repo(repository)
    run = None
    try:
        plan, run = await runs.require_open_run(plan_id, collection_run_id, repository=repo)
        if run.state != "PENDING":
            return
        items = await repo.get_items(plan.id)
        if not items:
            raise CollectionError("PLAN_EMPTY", 400)
        targets = await freeze_targets(plan, items)
        if not targets:
            raise CollectionError("PLAN_EMPTY", 400)

        plan, run = await runs.require_open_run(plan_id, collection_run_id, repository=repo)
        if run.state != "PENDING":
            return
        await issue_action_manifest(plan, run, targets, now=datetime.now(timezone.utc))

        plan, run = await runs.require_open_run(plan_id, collection_run_id, repository=repo)
        if run.state != "MANIFEST_FROZEN":
            return
        run.state = "COLLECTING"
        await repo.save_run(run)
        # This pipeline owns the tracked task and its heartbeat; dispatch runs
        # in that same task instead of starting another background owner.
        await collect_and_store(plan, run, repository=repo)

        plan, run = await runs.require_open_run(plan_id, collection_run_id, repository=repo)
        if run.state != "COLLECTED":
            return
        await submit_snapshot(plan, run)
    except CollectionError as exc:
        if run is not None and exc.code != "COLLECTION_RUN_CLOSED":
            await runs.finish_run(run, "FAILED", error_code=exc.code,
                                  error_message=exc.message, repository=repo)
    except manifest.ManifestError as exc:
        if run is not None:
            await runs.finish_run(run, "FAILED", error_code=exc.code,
                                  error_message=exc.public_message, repository=repo)
    except Exception:
        logger.error("Direct topology collection failed for run %s", collection_run_id)
        if run is not None:
            await runs.finish_run(run, "FAILED", error_code="COLLECTION_PIPELINE_FAILED", repository=repo)
