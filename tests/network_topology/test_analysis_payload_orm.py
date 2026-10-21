"""Exercise analysis payload writes through the real ORM and SQLite executor."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("binding", ["merge", "publication"])
def test_analysis_payload_bindings_persist_inline_and_object_references(binding):
    # Tortoise model initialization is global; isolate the real ORM from the
    # suite's in-memory repositories and static model declaration checks.
    probe = r'''
import asyncio
import sys
from datetime import datetime, timezone

from tortoise import Tortoise, connections

import libs.utils
from models.network_topology_v2 import TopologyAnalysisRun, TopologyPayloadObject
from services.network_topology.facts.merge import (
    PreparedAnalysisPayloads, activate_analysis_run_payloads,
)
from services.network_topology.payload_store import PayloadStore, PreparedPayload
from services.network_topology.publishing import OrmPublishingRepository


async def main():
    libs.utils.RY_RES_KEY = "local-regression-encryption-key"
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
    try:
        connection = connections.get("default")
        await connection.execute_script("PRAGMA foreign_keys=OFF;")
        generator = connection.schema_generator(connection)
        for model in (TopologyAnalysisRun, TopologyPayloadObject):
            await connection.execute_script(generator._get_table_sql(model, True)["table_creation_string"])
        for use_object in (False, True):
            run = await TopologyAnalysisRun.create(
                tenant_id="t1", topology_row_id="top1", snapshot_row_id="snap1",
                target_profile_revision_row_id="profile1", submitter_subject="collector",
                playbook_id="builtin", run_id="run1", analysis_kind="SNAPSHOT",
                publication_attempt_id=f"publication-{use_object}",
                expected_publication_revision=0, expected_current_graph_revision=0,
                parser_bundle_version="v1", input_schema_version="v1", facts_schema_version="v1",
                graph_schema_version="v2", redaction_version="v1", deterministic_parser_versions="{}",
            )
            prepared = []
            for name in ("facts", "report"):
                row = None
                if use_object:
                    row = await TopologyPayloadObject.create(
                        tenant_id="t1", content_hash="sha256:" + "a" * 64,
                        object_key=f"objects/{name}", encryption_version="v1", key_version="v1",
                        size_bytes=2, status="READY", verified_at=datetime.now(timezone.utc),
                    )
                prepared.append(PreparedPayload(
                    "t1", "sha256:" + "a" * 64, 2,
                    inline_payload=None if row else "{}", payload_object_id=row.id if row else None,
                ))
            store = PayloadStore()
            if sys.argv[1] == "merge":
                await activate_analysis_run_payloads(
                    run, PreparedAnalysisPayloads(*prepared), store=store, connection=connection,
                )
            else:
                row = await store.activate("t1", prepared[0], connection=connection)
                await OrmPublishingRepository().bind_facts_payload(run, prepared[0], row, connection)
            persisted = await TopologyAnalysisRun.get(id=run.id)
            assert persisted.fact_graph_payload == prepared[0].inline_payload
            assert persisted.fact_graph_payload_object_row_id == prepared[0].payload_object_id
            if sys.argv[1] == "merge":
                assert persisted.validation_report == prepared[1].inline_payload
                assert persisted.validation_report_object_row_id == prepared[1].payload_object_id
                assert persisted.facts_hash == prepared[0].payload_hash
    finally:
        await Tortoise.close_connections()


asyncio.run(main())
'''
    result = subprocess.run(
        [sys.executable, "-c", probe, binding], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
