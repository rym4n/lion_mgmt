import asyncio
import json
from pathlib import Path

from models.network_access_control import (
    POLICY_RUN_TERMINAL_STATES,
    POLICY_RUN_WORKING_STATES,
    NetworkAccessPolicyAgentJob,
    NetworkAccessPolicyCollectionItem,
    NetworkAccessPolicyCollectionPlan,
    NetworkAccessPolicyCollectionRun,
)

ROOT = Path(__file__).resolve().parents[2]


def test_tables_and_defaults():
    assert NetworkAccessPolicyCollectionPlan._meta.db_table == "network_access_policy_collection_plan"
    assert NetworkAccessPolicyCollectionItem._meta.db_table == "network_access_policy_collection_item"
    assert NetworkAccessPolicyCollectionRun._meta.db_table == "network_access_policy_collection_run"
    assert NetworkAccessPolicyAgentJob._meta.db_table == "network_access_policy_agent_job"
    plan = NetworkAccessPolicyCollectionPlan._meta.fields_map
    assert plan["schedule_cron"].default == "0 0 3 * * *"
    assert plan["run_timeout_seconds"].default == 3600
    run = NetworkAccessPolicyCollectionRun._meta.fields_map
    assert run["state"].default == "PENDING"
    for name in ("raw_outputs", "heartbeat_at", "compare_run_id", "agent_job_id", "compare_error"):
        assert name in run
    assert "failed" in NetworkAccessPolicyAgentJob._meta.fields_map
    assert POLICY_RUN_TERMINAL_STATES == {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}
    assert POLICY_RUN_WORKING_STATES == {"COLLECTING", "NORMALIZING", "APPLYING"}


def test_migration_creates_tables_indexes_and_sync_permission():
    import importlib
    module = importlib.import_module("migrations.models.135_20261020100000_network_policy_collection")
    sql = asyncio.run(module.upgrade(None))
    for table in ("network_access_policy_collection_plan", "network_access_policy_collection_item",
                  "network_access_policy_collection_run", "network_access_policy_agent_job"):
        assert f'CREATE TABLE IF NOT EXISTS "{table}"' in sql
    assert "uidx_policy_collection_plan_tenant" in sql
    assert "uidx_policy_collection_run_active" in sql
    assert 'CREATE INDEX IF NOT EXISTS "idx_policy_collection_run_state" ' in sql
    assert 'ON "network_access_policy_collection_run" ("tenant_id", "state");' in sql
    assert "'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED'" in sql
    assert '"heartbeat_at" TIMESTAMPTZ' in sql
    assert "network_access_control:sync" in sql
    assert "network-management-access-control" in sql
    down = asyncio.run(module.downgrade(None))
    assert "network_access_control:sync" not in down
    assert 'DROP TABLE IF EXISTS "network_access_policy_agent_job"' in down


def test_schema_file_matches_index_contract():
    path = ROOT / "services/network_access_control/collection/schemas/policy-normalized-v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema["$id"] == "https://schemas.aioe.local/aioe.network-policy.normalized/v1"
    policy = schema["properties"]["policies"]["items"]
    assert policy["required"] == ["policy_key", "action", "parse_status", "raw_text"]
    assert policy["properties"]["action"] == {"enum": ["permit", "deny"]}
    assert schema["properties"]["device_type"] == {"enum": ["firewall", "switch", "other"]}


def test_sensitive_outputs_use_encrypted_fields_and_all_models_keep_tenant_base():
    from models.base import BaseModel, EncryptedTextField

    for model, sensitive_fields in (
        (NetworkAccessPolicyCollectionPlan, ()),
        (NetworkAccessPolicyCollectionItem, ()),
        (NetworkAccessPolicyCollectionRun, ("raw_outputs",)),
        (NetworkAccessPolicyAgentJob, ("documents", "failed", "transcript")),
    ):
        assert issubclass(model, BaseModel)
        for name in ("id", "ctime", "utime", "deleted", "tenant_id"):
            assert name in model._meta.fields_map
        for name in sensitive_fields:
            field = model._meta.fields_map[name]
            assert isinstance(field, EncryptedTextField)
            assert field.null


def test_list_defaults_are_independent_between_records():
    first = NetworkAccessPolicyCollectionItem(plan_id="plan", app_name="app", action_func="read")
    second = NetworkAccessPolicyCollectionItem(plan_id="plan", app_name="app", action_func="read")
    first.resources.append({"resource_id": "resource", "device_asset_id": "asset"})
    assert second.resources == []
    first_job = NetworkAccessPolicyAgentJob(collection_run_id="run", attempt=1, job_token_hash="hash")
    second_job = NetworkAccessPolicyAgentJob(collection_run_id="run", attempt=2, job_token_hash="hash")
    first_job.resource_ids.append("resource")
    assert second_job.resource_ids == []


def test_schema_validation_accepts_policy_and_rejects_unsafe_documents():
    from copy import deepcopy

    import pytest
    from jsonschema import Draft202012Validator, ValidationError

    schema = json.loads((ROOT / "services/network_access_control/collection/schemas/policy-normalized-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid = {
        "resource_id": "resource", "device_type": "firewall", "policy_type": "firewall",
        "policies": [{"policy_key": "rule1", "action": "deny", "parse_status": "parsed", "raw_text": "deny ip any any"}],
    }
    validator.validate(valid)
    for field, value in (("action", "allow"), ("raw_text", ""), ("policy_order", -1), ("unexpected", True)):
        invalid = deepcopy(valid)
        invalid["policies"][0][field] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    invalid = deepcopy(valid)
    invalid["tenant_id"] = "other-tenant"
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_container_includes_normalized_policy_schema():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY services/network_access_control/collection/schemas/ ./services/network_access_control/collection/schemas/" in dockerfile
