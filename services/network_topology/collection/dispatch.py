"""Dispatch frozen action targets and store only complete sanitized snapshots."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import traceback
from time import monotonic
from typing import Protocol

import orjson
from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import Runlog
from models.asset_management import AssetInstance
from models.network_topology_v2 import RUN_TERMINAL_STATES, TopologyCollectionManifest
from services.network_topology import snapshot
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.observations import parse_action_output
from services.network_topology.redaction import scrub_action_output
from services.network_topology.collection.runs import OrmRunRepository, finish_run
from services.network_topology.collection.snapshot_builder import ItemOutcome, asset_context_for, build_snapshot
from services.network_topology.collection.targets import Target
from settings import TASK_TOPIC

logger = logging.getLogger(__name__)
_ACTIVE_TASKS: set[asyncio.Task] = set()
_START_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class ExecResult:
    status: int
    result_text: str | None
    duration_ms: int


class ActionExecutor(Protocol):
    async def __call__(self, tenant_id: str, target: Target, *, timeout: int, executor_id: str) -> ExecResult: ...


class KafkaRunlogExecutor:
    async def __call__(self, tenant_id, target, *, timeout, executor_id):
        started = monotonic()
        run_id = gen_run_id()
        await aproduce(TASK_TOPIC, {"run_type": "action", "run_id": run_id, "tenant_id": tenant_id,
            "run_args": {"app_name": target.app_name, "name": target.app_name, "app_ver": target.app_version,
                         "main": target.main, "action": target.action_func, "action_name": target.action_name,
                         "action_input": target.action_input, "resource": target.resource_id,
                         "node_name": target.action_name, "retry_times": 0, "timeout": timeout, "executor": executor_id}},
                       raise_on_error=True)
        while monotonic() - started < timeout:
            log = await Runlog.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).order_by("-utime").first()
            if log and ((log.status == 0 and log.result != "执行中") or log.status in (-1, 2, 3)):
                text = log.result if isinstance(log.result, str) or log.result is None else orjson.dumps(log.result).decode()
                return ExecResult(log.status, text, int((monotonic() - started) * 1000))
            await asyncio.sleep(min(1, max(0, timeout - (monotonic() - started))))
        raise CollectionError("COLLECTION_ITEM_TIMEOUT")


def _now():
    return datetime.now(timezone.utc)


def _text_time(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def run_dispatch(plan, run, targets, *, executor=None, now=None, repository=None, persist=True) -> list[ItemOutcome]:
    executor = executor or KafkaRunlogExecutor()
    semaphore = asyncio.Semaphore(plan.concurrency)
    async def execute(target):
        async with semaphore:
            started = monotonic()
            output = None
            error_code = error_message = None
            duration = 0
            try:
                timeout = int(getattr(plan, "per_item_timeout_seconds", 300))
                try:
                    result = await asyncio.wait_for(executor(plan.tenant_id, target, timeout=timeout,
                        executor_id=str(getattr(run, "triggered_by", None) or "")), timeout=timeout)
                except asyncio.TimeoutError:
                    raise CollectionError("COLLECTION_ITEM_TIMEOUT") from None
                duration = result.duration_ms
                if result.status != 0:
                    message = "COLLECTION_ACTION_FAILED"
                    # Parse only a successful executor result; failed payloads may be
                    # empty or non-JSON and must retain Runlog failure semantics.
                    try:
                        payload = scrub_action_output(orjson.loads(result.result_text)) if result.result_text else {}
                        message = str(payload.get("msg") or message)[:512] if isinstance(payload, dict) else message
                    except Exception:
                        pass
                    raise CollectionError("COLLECTION_ACTION_FAILED", message=message)
                output = parse_action_output(result.result_text)
                if isinstance(output, dict) and output.get("code") not in (0, 200):
                    message = output.get("msg") if isinstance(output, dict) else None
                    raise CollectionError("COLLECTION_ACTION_FAILED", message=str(message or "COLLECTION_ACTION_FAILED")[:512])
            except CollectionError as exc:
                error_code, error_message = exc.code, exc.message
                output = None
            except Exception:
                # Never retain an exception string: executors may include credentials/raw output.
                error_code = error_message = "COLLECTION_ACTION_FAILED"
            return ItemOutcome(target.item_id, target.app_name, target.action_func, target.app_version,
                               target.resource_id, "failed" if error_code else "success", output,
                               _text_time(now or _now()), error_code, error_message,
                               duration or int((monotonic() - started) * 1000))
    outcomes = await asyncio.gather(*(execute(t) for t in targets))
    run.item_results = [{k: v for k, v in asdict(o).items() if k != "output"} for o in outcomes]
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


def _task_done(task):
    _ACTIVE_TASKS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background collection terminal accounting failed")


async def start_background(plan, run, *, repository=None) -> dict:
    async with _START_LOCK:
        if run.state == "MANIFEST_FROZEN":
            # The lock serializes stale callers in this process. Repositories may
            # provide a durable conditional claim for multi-process workers.
            if repository is not None and hasattr(repository, "claim_collection_start"):
                claimed = await repository.claim_collection_start(run)
                if not claimed:
                    return {"collection_run_id": str(run.id), "run_state": run.state}
            else:
                run.state = "COLLECTING"
                await run.save(update_fields=["state"])
            task = asyncio.create_task(collect_and_store(plan, run, repository=repository))
            _ACTIVE_TASKS.add(task)
            task.add_done_callback(_task_done)
    return {"collection_run_id": str(run.id), "run_state": run.state}
