"""SQLite exercises real SQL/CAS and detached models; PostgreSQL lock order is documented in code."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from tortoise import Tortoise, connections
from tortoise.transactions import in_transaction
from models import NetworkTopology
from models.network_topology_v2 import TopologyCollectionRun as Run, TopologyCollectionPlan as Plan
from models.network_topology_v2 import TopologyAnalysisRun as Analysis, TopologyCollectionItem as Item
from services.network_topology.collection import runs, plans, manifest_issue
from services.network_topology.collection.errors import CollectionError
from services.network_topology.snapshot import OrmSnapshotRepository, SnapshotError


async def analysis(identifier, state="RUNNING"):
    return await Analysis.create(
        id=identifier, tenant_id="t1", topology_row_id="top1", snapshot_row_id="snap1",
        target_profile_revision_row_id="profile1", submitter_subject="collection-run:r1",
        playbook_id="builtin", run_id="r1", analysis_kind="SNAPSHOT", publication_attempt_id=identifier,
        expected_publication_revision=0, expected_current_graph_revision=0,
        parser_bundle_version="v1", input_schema_version="v1", facts_schema_version="v1",
        graph_schema_version="v2", redaction_version="v1", deterministic_parser_versions="{}",
        state=state, stage="PUBLISHING", lease_owner="worker", lease_generation=3, fencing_token=8,
    )


async def create_run(identifier, state, **kwargs):
    return await Run.create(id=identifier, tenant_id="t1", plan_id="p1", topology_id="top1",
                            trigger="manual", state=state, **kwargs)


async def assert_closed(coro):
    try:
        await coro
    except (CollectionError, SnapshotError):
        return
    raise AssertionError("stale transition was accepted")


async def main():
    import libs.utils
    libs.utils.RY_RES_KEY = "local-regression-encryption-key"
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
    try:
        db = connections.get("default")
        # Full project schema has cyclic FKs. Exercise only the owned tables;
        # unrelated referenced profile/snapshot fixtures are deliberately omitted.
        await db.execute_script("PRAGMA foreign_keys=OFF;")
        generator = db.schema_generator(db)
        for model in (NetworkTopology, Run, Plan, Analysis, Item):
            await db.execute_script(generator._get_table_sql(model, True)["table_creation_string"])
        await NetworkTopology.create(id="top1", tenant_id="t1", name="Topology", current_graph_revision=7)
        plan = await Plan.create(id="p1", tenant_id="t1", topology_id="top1", enabled=True)
        await Item.create(id="i1", tenant_id="t1", plan_id="p1", app_name="tool", action_func="get")
        repo = runs.OrmRunRepository()
        # Every background progression receives an independent stale ORM copy.
        for state in ("COLLECTING", "COLLECTED", "SUBMITTED"):
            for terminal in ("CANCELLED", "FAILED"):
                identifier = state + terminal
                current = await create_run(identifier, "COLLECTING")
                stale = await Run.get(id=identifier)
                await runs.finish_run(current, terminal, repository=repo)
                stale.state = state
                stale.snapshot_draft = "private draft"
                await assert_closed(repo.save_run(stale))
                assert (await Run.get(id=identifier)).state == terminal
        # Normal guarded progression persists only its owned fields and keeps
        # the completed dispatch counts when the snapshot stage follows.
        progressive = await create_run("normal", "COLLECTING")
        progressive.item_results = [{"status": "success"}]
        progressive.device_succeeded = 1
        await repo.save_run(progressive)
        progressive.state = "COLLECTED"
        progressive.snapshot_draft = "normal draft"
        await repo.save_run(progressive)
        persisted = await Run.get(id=progressive.id)
        assert persisted.snapshot_draft == "normal draft" and persisted.device_succeeded == 1
        await runs.finish_run(progressive, "CANCELLED")
        # A stale dispatch cannot regress a newly submitted run either.
        current = await create_run("progressed", "SUBMITTED")
        stale = await Run.get(id=current.id)
        stale.state = "COLLECTING"
        await assert_closed(repo.save_run(stale))
        assert (await Run.get(id=current.id)).state == "SUBMITTED"
        await Run.filter(id=current.id).update(state="SUCCEEDED")
        # Freeze cannot resurrect cancellation after target resolution yielded.
        current = await create_run("manifest", "PENDING")
        stale = await Run.get(id=current.id)
        await runs.finish_run(current, "CANCELLED", repository=repo)
        async def locked(*args):
            return SimpleNamespace()
        with patch.object(manifest_issue.manifest, "_locked_topology", locked):
            await assert_closed(manifest_issue.issue_action_manifest(plan, stale, [], now=datetime.now(timezone.utc)))
        # Cancellation wins before submission: shared ingress must reject before
        # creating any analysis, even when the caller held a COLLECTED copy.
        current = await create_run("before-submit", "COLLECTED")
        stale = await Run.get(id=current.id)
        await runs.finish_run(current, "CANCELLED", repository=repo)
        ingress = OrmSnapshotRepository()
        async with in_transaction() as connection:
            await assert_closed(ingress.lock_collection_run("t1", "top1", "collection-run:" + stale.id, connection))
        # Submission wins: creation and linkage share a transaction. A separate
        # cancelling instance then sees and fences that newly attached analysis.
        current = await create_run("after-submit", "COLLECTED")
        cancelling = await Run.get(id=current.id)
        async with in_transaction() as connection:
            locked_run = await ingress.lock_collection_run("t1", "top1", "collection-run:" + current.id, connection)
            ar = await analysis("analysis-linked")
            await ingress.bind_collection_analysis(locked_run, ar, "snap1", connection)
        await runs.finish_run(cancelling, "CANCELLED", repository=repo)
        fenced = await Analysis.get(id=ar.id)
        assert fenced.state == "CANCELLED" and fenced.cancellation_requested
        assert fenced.fencing_token == 9 and fenced.lease_generation == 4 and fenced.lease_owner is None
        # Timeout cancels even an analysis that has entered PUBLISHING, which
        # makes the publisher's durable state/fencing checks reject late output.
        ar = await analysis("analysis-timeout")
        timed = await create_run("timed", "SUBMITTED", analysis_run_id=ar.id,
                                 started_at=datetime.now(timezone.utc)-timedelta(hours=2))
        await runs.sweep_runs()
        assert (await Run.get(id=timed.id)).error_code == "COLLECTION_RUN_TIMEOUT"
        fenced = await Analysis.get(id=ar.id)
        assert fenced.state == "CANCELLED" and fenced.fencing_token == 9
        # Refresh a completed analysis before declaring wall-clock timeout.
        ar = await analysis("analysis-done", "SUCCEEDED")
        done = await create_run("done", "SUBMITTED", analysis_run_id=ar.id,
                                started_at=datetime.now(timezone.utc)-timedelta(hours=2))
        await runs.sweep_runs()
        assert (await Run.get(id=done.id)).state == "SUCCEEDED"
        # Completion during the refresh/timeout race also cannot become FAILED.
        race = await create_run("done-race", "SUBMITTED", analysis_run_id=ar.id)
        await runs.finish_run(race, "FAILED", error_code="COLLECTION_RUN_TIMEOUT")
        assert (await Run.get(id=race.id)).state == "SUBMITTED"
        await runs.refresh_from_analysis(race)
        assert (await Run.get(id=race.id)).state == "SUCCEEDED"
        # Reload/scheduled trigger shows active run without rewriting the last
        # completed pointer; after removal it falls back to the completed run.
        plan = await Plan.get(id="p1")
        last_completed = plan.last_run_id
        scheduled = await runs.start_scheduled("p1")
        summary = await plans.collection_summaries("t1", ["top1"])
        assert summary["top1"]["last_run"]["id"] == scheduled["collection_run_id"]
        assert (await Plan.get(id="p1")).last_run_id == last_completed
        await Run.filter(id=scheduled["collection_run_id"]).update(deleted=True)
        summary = await plans.collection_summaries("t1", ["top1"])
        assert summary["top1"]["last_run"]["id"] == last_completed
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
