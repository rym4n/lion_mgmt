"""Issue the action collector manifest using the topology's current profile."""
from dataclasses import asdict
from datetime import timedelta, timezone
import uuid

from tortoise.transactions import in_transaction

from services.network_topology import manifest
from services.network_topology import contracts
from services.network_topology.canonicalization import canonicalize_and_hash_manifest, canonicalize_json, sha256_uri
from services.network_topology.collection.snapshot_builder import PLAYBOOK_ID
from services.network_topology.collection.errors import CollectionError
from models.network_topology_v2 import TopologyCollectionRun


async def issue_action_manifest(plan, run, targets, *, now) -> dict:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    authority = manifest.ManifestAuthority(plan.tenant_id, plan.topology_id,
        subject=f"collection-run:{run.id}", playbook_id=PLAYBOOK_ID, run_id=str(run.id))
    resource_ids = sorted({t.resource_id for t in targets})
    provenance = {"playbook_id": PLAYBOOK_ID, "run_id": str(run.id),
                  "selector_hash": sha256_uri(canonicalize_json(resource_ids))}
    async with in_transaction() as connection:
        topology = await manifest._locked_topology(connection, authority)
        current = await TopologyCollectionRun.filter(
            id=run.id, tenant_id=plan.tenant_id, topology_id=plan.topology_id,
            plan_id=plan.id, deleted=False,
        ).using_db(connection).select_for_update().first()
        if current is None or current.state != "PENDING":
            raise CollectionError("COLLECTION_RUN_CLOSED", 409)
        profile_row, profile = await manifest._current_profile(connection, authority, topology, topology.topology_profile_revision)
        capability = next((p for p in contracts.load_registry(
            "aioe.network-topology.registry/capability-profiles/v1")["profiles"]
                           if p.get("id") == "aioe-action-collection/v1"), None)
        if capability is None:
            raise manifest.ManifestError(manifest.MANIFEST_CAPABILITY_UNSUPPORTED)
        expected = [{"resource_id": rid, "capability_profile": "aioe-action-collection/v1",
                     "required_kinds": ["raw.action_output"],
                     "optional_kinds": list(capability["optional_kinds"])} for rid in resource_ids]
        registry = manifest._validated_registry_version()
        intent, issued_at = await manifest._upgrade_intent(connection, authority, topology, lambda: now)
        sequence = await manifest._next_sequence(connection, authority, topology)
        target = manifest._target_manifest(topology, profile_row, expected, sequence, str(uuid.uuid4()), issued_at, registry)
        target["expires_at"] = manifest._utc_text(issued_at + timedelta(seconds=plan.run_timeout_seconds))
        canonical = canonicalize_and_hash_manifest(target)
        target = {**canonical.projection["target_manifest"], "manifest_hash": canonical.manifest_hash}
        await manifest._persist_manifest(connection, authority, topology, profile_row, intent, provenance, target, registry)
        run.manifest_id = target["manifest_id"]
        run.frozen_targets = [asdict(t) for t in targets]
        run.device_total = len(resource_ids)
        run.state = "MANIFEST_FROZEN"
        await run.save(using_db=connection, update_fields=["manifest_id", "frozen_targets", "device_total", "state"])
    return target
