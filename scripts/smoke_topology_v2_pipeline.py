"""Real migrated TEST database only; never auto-generates schema.

Run: uv run python scripts/smoke_topology_v2_pipeline.py --confirmed-test-database
The explicit switch attests that settings.TORTOISE_ORM points to a verified test
DB with topology migrations applied and no running application workers.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orjson
from tortoise import Tortoise

import models
import settings
from core.maintenance import DEFAULT_TENANT_ID
from services.network_topology import compatibility_manager, manifest, snapshot
from services.network_topology.analysis_pipeline import build_default_pipeline
from services.network_topology.canonicalization import sha256_uri
from services.network_topology.leases import OrmLeaseRepository, TERMINAL_STATES
from services.network_topology.worker import TopologyAnalysisWorker


class SmokeLeaseRepository(OrmLeaseRepository):
    """Claim only the temporary smoke run, never unrelated test work."""

    def __init__(self, run_id):
        self.run_id = run_id

    async def claim_candidates(self, now, limit, *, connection):
        return await models.TopologyAnalysisRun.filter(
            id=self.run_id, tenant_id=DEFAULT_TENANT_ID, deleted=False,
        ).exclude(state__in=tuple(TERMINAL_STATES)).using_db(connection).select_for_update()


async def smoke():
    await Tortoise.init(config=settings.TORTOISE_ORM)
    topology = resource = worker = None
    try:
        if not all((settings.TOPOLOGY_V2_INGRESS_ENABLED,
                    settings.TOPOLOGY_V2_ANALYSIS_ENABLED,
                    settings.TOPOLOGY_V2_PUBLISH_ENABLED)):
            raise RuntimeError("Smoke requires ingress, analysis and publish enabled")
        actor = "topology-v2-smoke"
        nonce = str(uuid.uuid4())
        created = await compatibility_manager.create_compatible_topology(
            tenant_id=DEFAULT_TENANT_ID, name=f"topology-v2-smoke-{nonce}", created_by=actor,
        )
        topology = created.topology
        initial_revision = topology.current_graph_revision
        assert initial_revision == 1, initial_revision
        resource = await models.AssetInstance.create(
            tenant_id=DEFAULT_TENANT_ID, asset_model_id=nonce,
            asset_model_key="switch", name=f"smoke-huawei-{nonce}",
            attributes={"device_type": "switch", "vendor": "huawei"},
        )
        collection = orjson.loads((Path(__file__).resolve().parents[1] /
            "tests/fixtures/network_topology/huawei/full-golden-v1.json").read_bytes())["collection"]
        collection["snapshot_id"] = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        collection["collector"]["run_id"] = run_id
        authority = manifest.ManifestAuthority(DEFAULT_TENANT_ID, topology.id, actor,
            collection["collector"]["playbook_id"], run_id)
        issued = await manifest.issue_manifest(authority, {
            "id": topology.id, "profile_revision": 1, "resolved_resource_ids": [resource.id],
            "selection_provenance": {"playbook_id": authority.playbook_id,
                "run_id": run_id, "selector_hash": sha256_uri(resource.id.encode())},
        })
        collection["target_manifest"] = issued.target_manifest
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        collection["collection_started_at"] = collection["collection_ended_at"] = stamp
        for source in collection["sources"]:
            for item in source["resources"]:
                item["resource_id"] = resource.id
                for observation in item["result"]["observations"]:
                    observation["observed_at"] = stamp
        completeness = collection["completeness"]
        completeness["expected_resource_ids"] = completeness["collected_resource_ids"] = [resource.id]
        for coverage in completeness["observation_coverage"]:
            coverage["resource_id"] = resource.id
        accepted = await snapshot.ingest_direct_snapshot(DEFAULT_TENANT_ID, topology.id,
            actor, collection, body_size=len(orjson.dumps(collection)))
        worker = TopologyAnalysisWorker(worker_id=f"smoke:{nonce}",
            pipeline=build_default_pipeline(), lease_repository=SmokeLeaseRepository(accepted.sync_id))
        for _ in range(30):
            await worker.run_once()
            run = await models.TopologyAnalysisRun.get(id=accepted.sync_id)
            if run.state in TERMINAL_STATES:
                break
            await asyncio.sleep(0.1)
        await topology.refresh_from_db()
        assert run.state == "SUCCEEDED", f"Smoke state={run.state}, error={run.error_code}"
        assert topology.current_graph_revision == initial_revision + 1, topology.current_graph_revision
        print("SMOKE OK")
    finally:
        try:
            if worker is not None:
                await worker.shutdown()
            if topology is not None:
                # Soft-delete every topology-owned row, including Facts/presentation audit rows.
                payload_ids = set()
                for model in Tortoise.apps["models"].values():
                    fields = model._meta.fields_map
                    key = "topology_row_id" if "topology_row_id" in fields else "topology_id"
                    if key in fields and "deleted" in fields:
                        rows = model.filter(**{key: topology.id, "tenant_id": DEFAULT_TENANT_ID})
                        object_fields = [name for name in fields if name.endswith("object_row_id")]
                        if object_fields:
                            for row in await rows.values(*object_fields):
                                payload_ids.update(value for value in row.values() if value is not None)
                        await rows.update(deleted=True)
                if payload_ids:
                    await models.TopologyPayloadObject.filter(
                        id__in=payload_ids, tenant_id=DEFAULT_TENANT_ID,
                    ).update(deleted=True)
                await models.NetworkTopology.filter(id=topology.id).update(deleted=True)
            if resource is not None:
                await models.AssetInstance.filter(id=resource.id).update(deleted=True)
        finally:
            await Tortoise.close_connections()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmed-test-database", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(smoke())
