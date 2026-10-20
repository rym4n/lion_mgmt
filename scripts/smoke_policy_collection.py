"""Real migrated TEST database only; never auto-generates schema.

Run: uv run python scripts/smoke_policy_collection.py --confirmed-test-database
     (container: docker exec -w /app lion-mgmt /app/.venv/bin/python /tmp/smoke_policy_collection.py --confirmed-test-database)
The switch attests that settings.TORTOISE points to a verified test DB with
migration 135 applied and no running application workers. Uses a throwaway
tenant and deletes every row it created in ``finally``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/app")

from tortoise import Tortoise

import models
import settings
from services.action_collection.executor import ExecResult
from services.network_access_control.collection import agent_jobs, pipeline, runs
from services.network_access_control.collection.agent_stage import AgentNormalizer
from tests.network_access_control.policy_agent_fixtures import RULE_TEXT, document

TENANT = f"smoke-policy-{uuid.uuid4()}"
RULE = {
    "policy_key": "smoke-1",
    "rule_name": "smoke-1",
    "source_address": "10.0.0.0/8",
    "target_address": "any",
    "destination_port": "443",
    "protocol": "tcp",
    "action": "permit",
    "parse_status": "parsed",
    "raw_text": "rule name smoke-1",
    "policy_order": 1,
}


async def fake_executor(tenant_id, target, *, timeout, executor_id):
    del tenant_id, target, timeout, executor_id
    return ExecResult(
        0,
        '{"code":200,"data":{"outputs":[{"source":"display security-policy rule all","content":%s}]}}'
        % json.dumps(RULE_TEXT),
        5,
    )


async def smoke_llm(tenant_id, *, purpose="chat", config_id=None):
    del tenant_id, purpose, config_id
    return type("SmokeLlm", (), {"to_payload": lambda self: {"model": "smoke"}})()


class SmokeClient:
    async def execute(self, payload):
        job = await models.NetworkAccessPolicyAgentJob.get(id=payload["job_id"])
        documents = [{"resource_id": resource_id, "document": document(resource_id)}
                     for resource_id in job.resource_ids]
        assert await agent_jobs.store_submission(job, documents, [])
        return 200, {"submitted": True, "cache_hits": 0, "cache_misses": 1,
                      "iterations": 1, "profile_version": "policy-normalizer/1",
                      "usage": {}, "transcript": [], "failed": []}


async def smoke():
    await Tortoise.init(config=settings.TORTOISE)
    try:
        model_key = "firewall"  # Must belong to POLICY_DEVICE_MODEL_KEYS.
        created_asset_model = await models.AssetModel.create(
            tenant_id=TENANT,
            key=f"smoke_fw_{uuid.uuid4().hex[:8]}",
            name="smoke firewall",
            group_id=str(uuid.uuid4()),
            group_key="network_device",
        )
        asset = await models.AssetInstance.create(
            tenant_id=TENANT,
            asset_model_id=created_asset_model.id,
            asset_model_key=model_key,
            name="smoke-fw",
            attributes={},
        )
        await models.NetworkAccessPresetPolicy.create(
            tenant_id=TENANT,
            device_asset_id=asset.id,
            rule_name="smoke-1",
            source_address="10.0.0.0/8",
            target_address="any",
            protocol="tcp",
            port="443",
            action="permit",
        )
        plan = await models.NetworkAccessPolicyCollectionPlan.create(
            tenant_id=TENANT, enabled=False
        )
        resource_id = str(uuid.uuid4())
        run = await models.NetworkAccessPolicyCollectionRun.create(
            tenant_id=TENANT,
            plan_id=plan.id,
            trigger="manual",
            triggered_by="smoke",
            state="COLLECTING",
            started_at=datetime.now(timezone.utc),
            device_total=1,
            heartbeat_at=datetime.now(timezone.utc),
            frozen_targets=[
                {
                    "item_id": "i1",
                    "app_name": "huawei_usg",
                    "app_version": "v1",
                    "main": "main.py",
                    "action_func": "collect_policy_observations",
                    "action_name": "采集策略",
                    "action_input": {},
                    "resource_id": resource_id,
                    "resource_name": "smoke-fw",
                    "host": "10.0.0.1",
                    "asset_instance_id": asset.id,
                }
            ],
        )
        normalizer = AgentNormalizer(client=SmokeClient(), llm_resolver=smoke_llm)
        await pipeline.run_pipeline(plan, run, executor=fake_executor, normalizer=normalizer)
        row = await models.NetworkAccessPolicyCollectionRun.get(id=run.id)
        assert row.state == "SUCCEEDED", (row.state, row.error_code, row.item_results)
        job = await models.NetworkAccessPolicyAgentJob.get(collection_run_id=str(run.id))
        assert job.state == "SUCCEEDED"
        assert row.compare_run_id, "compare did not run"
        policies = await models.NetworkAccessActualPolicy.filter(
            tenant_id=TENANT, deleted=False
        )
        assert [p.device_key for p in policies] == [f"resource:{resource_id}"]
        assert policies[0].collection_run_id == str(run.id)

        # A second pass with a new run id must not write history when unchanged.
        changes_before = await models.NetworkAccessPolicyChange.filter(
            tenant_id=TENANT
        ).count()
        run2 = await models.NetworkAccessPolicyCollectionRun.create(
            tenant_id=TENANT,
            plan_id=plan.id,
            trigger="manual",
            triggered_by="smoke",
            state="COLLECTING",
            started_at=datetime.now(timezone.utc),
            device_total=1,
            frozen_targets=row.frozen_targets,
        )
        await pipeline.run_pipeline(plan, run2, executor=fake_executor,
                                    normalizer=AgentNormalizer(client=SmokeClient(), llm_resolver=smoke_llm))
        assert (
            await models.NetworkAccessPolicyChange.filter(tenant_id=TENANT).count()
            == changes_before
        )

        # Real TIMESTAMPTZ values through the sweep (aware vs naive comparisons).
        stale = await models.NetworkAccessPolicyCollectionRun.create(
            tenant_id=TENANT,
            plan_id=plan.id,
            trigger="manual",
            state="APPLYING",
            started_at=datetime.now(timezone.utc),
            heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        await runs.sweep_runs()
        stale = await models.NetworkAccessPolicyCollectionRun.get(id=stale.id)
        assert stale.error_code == "COLLECTION_WORKER_LOST", stale.error_code
        print("SMOKE OK")
    finally:
        for model in (
            models.NetworkAccessPolicyCompareItem,
            models.NetworkAccessPolicyCompareRun,
            models.NetworkAccessPolicyChange,
            models.NetworkAccessActualPolicy,
            models.NetworkAccessPresetPolicy,
            models.NetworkAccessPolicyAgentJob,
            models.NetworkAccessPolicyCollectionRun,
            models.NetworkAccessPolicyCollectionPlan,
            models.AssetInstance,
            models.AssetModel,
        ):
            await model.filter(tenant_id=TENANT).delete()
        await Tortoise.close_connections()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmed-test-database", action="store_true")
    if not parser.parse_args().confirmed_test_database:
        raise SystemExit("Refusing to run without --confirmed-test-database")
    asyncio.run(smoke())


if __name__ == "__main__":
    main()
