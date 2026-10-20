"""Dispatch frozen action targets and store only complete sanitized snapshots."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging
import traceback

import orjson
from models.asset_management import AssetInstance
from models.network_topology_v2 import RUN_TERMINAL_STATES, TopologyCollectionManifest, TopologyCollectionRun
from services.network_topology import snapshot
from services.action_collection.background import start_tracked
from services.action_collection.execute import ItemOutcome, execute_targets, text_time as _text_time
from services.action_collection.executor import ActionExecutor, ExecResult, KafkaRunlogExecutor  # noqa: F401
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.runs import OrmRunRepository, finish_run
from services.network_topology.collection.snapshot_builder import asset_context_for, build_snapshot
from services.network_topology.collection.targets import Target

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


async def run_dispatch(plan, run, targets, *, executor=None, now=None, repository=None, persist=True) -> list[ItemOutcome]:
    outcomes = await execute_targets(
        plan.tenant_id, targets, concurrency=plan.concurrency,
        per_item_timeout=int(getattr(plan, "per_item_timeout_seconds", 300)),
        executor_id=str(getattr(run, "triggered_by", None) or ""), executor=executor, now=now)
    run.item_results = [{k: v for k, v in asdict(o).items() if k not in {"output", "action_run_id"}} for o in outcomes]
    resource_ids = {t.resource_id for t in targets}
    failed_ids = {o.resource_id for o in outcomes if o.status != "success"}
    run.device_succeeded = len(resource_ids - failed_ids)
    if run.state not in RUN_TERMINAL_STATES:
        run.state = "COLLECTING"
        if persist:
            await (repository or OrmRunRepository()).save_run(run)
    return outcomes


async def collect_and_store(plan, run, *, executor=None, repository=None) -> dict:
    repo = repository or OrmRunRepository()
    try:
        targets = [Target(**t) for t in run.frozen_targets or []]
        outcomes = await run_dispatch(plan, run, targets, executor=executor, repository=repo)
        summary = {"device_total": run.device_total, "device_succeeded": run.device_succeeded, "item_results": run.item_results}
        if not outcomes or any(o.status != "success" for o in outcomes):
            await finish_run(run, "FAILED", error_code="COLLECTION_INCOMPLETE", repository=repo)
            return summary
        row = await TopologyCollectionManifest.filter(manifest_id=run.manifest_id, tenant_id=run.tenant_id,
                                                     topology_row_id=run.topology_id, deleted=False).first()
        if row is None:
            raise CollectionError("COLLECTION_MANIFEST_NOT_FOUND")
        manifest = snapshot._manifest_row_projection(row)
        contexts = {}
        for target in targets:
            asset = None
            if target.asset_instance_id:
                asset_row = await AssetInstance.filter(id=target.asset_instance_id, tenant_id=run.tenant_id, deleted=False).first()
                asset = asset_row.json if asset_row else None
            contexts[target.resource_id] = asset_context_for(target.resource_name, target.host, target.app_name, asset, target.resource_id)
        draft = build_snapshot(collection_run_id=str(run.id), manifest=manifest, profile_revision=manifest["profile_revision"],
            outcomes=outcomes, asset_contexts=contexts, started_at=_text_time(run.started_at), ended_at=_text_time(_now()))
        if run.state not in RUN_TERMINAL_STATES:
            run.snapshot_draft = orjson.dumps(draft).decode()
            run.state = "COLLECTED"
            await repo.save_run(run)
        return summary
    except CollectionError as exc:
        await finish_run(run, "FAILED", error_code=exc.code, error_message=exc.message, repository=repo)
        return {"device_total": run.device_total, "device_succeeded": run.device_succeeded,
                "item_results": getattr(run, "item_results", None) or []}
    except Exception:
        logger.error("Collection dispatch failed for run %s\n%s", run.id, traceback.format_exc())
        await finish_run(run, "FAILED", error_code="COLLECTION_DISPATCH_FAILED", repository=repo)
        return {"device_total": run.device_total, "device_succeeded": run.device_succeeded,
                "item_results": getattr(run, "item_results", None) or []}


async def start_background(plan, run, *, repository=None) -> dict:
    if run.state == "MANIFEST_FROZEN":
        if repository is not None:
            claimed = await repository.claim_collection_start(run)
        else:
            # Claim liveness atomically: a long queue wait must not look like a lost worker.
            claimed = await TopologyCollectionRun.filter(
                id=run.id, tenant_id=run.tenant_id, topology_id=run.topology_id,
                deleted=False, state="MANIFEST_FROZEN").update(state="COLLECTING", heartbeat_at=_now())
        if claimed:
            run.state = "COLLECTING"
            start_tracked(collect_and_store(plan, run, repository=repository),
                          run=run, run_model=TopologyCollectionRun)
        else:
            if repository is not None:
                current = await repository.get_run(run.id, tenant_id=run.tenant_id)
            else:
                current = await TopologyCollectionRun.filter(
                    id=run.id, tenant_id=run.tenant_id, deleted=False).first()
            if current is not None:
                run.state = current.state
    return {"collection_run_id": str(run.id), "run_state": run.state}
