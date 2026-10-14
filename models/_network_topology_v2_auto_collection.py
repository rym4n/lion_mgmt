"""Topology auto-collection plan, items and runs."""

from __future__ import annotations

from tortoise import fields

from models._network_topology_v2_base import TopologyTenantModel
from models.base import EncryptedTextField

__all__ = (
    "RUN_STATES", "RUN_TERMINAL_STATES", "TopologyCollectionItem",
    "TopologyCollectionPlan", "TopologyCollectionRun", "TopologyAgentJob",
)

RUN_STATES = ("PENDING", "MANIFEST_FROZEN", "COLLECTING", "COLLECTED", "SUBMITTED", "SUCCEEDED", "FAILED", "CANCELLED")
RUN_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


class TopologyCollectionPlan(TopologyTenantModel):
    topology_id = fields.CharField(max_length=36)
    enabled = fields.BooleanField(default=False)
    schedule_cron = fields.CharField(max_length=100, default="0 0 2 * * *")
    concurrency = fields.IntField(default=8)
    per_item_timeout_seconds = fields.IntField(default=300)
    run_timeout_seconds = fields.IntField(default=3600)
    notify_enabled = fields.BooleanField(default=False)
    notify_config = fields.JSONField(null=True)
    timed_id = fields.CharField(max_length=36, null=True)
    last_run_id = fields.CharField(max_length=36, null=True)
    consecutive_failures = fields.IntField(default=0)
    revision = fields.IntField(default=1)
    updater = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "network_topology_collection_plan"
        indexes = (("tenant_id", "topology_id"),)


class TopologyCollectionItem(TopologyTenantModel):
    plan_id = fields.CharField(max_length=36)
    app_name = fields.CharField(max_length=255)
    app_version = fields.CharField(max_length=50, null=True)
    action_func = fields.CharField(max_length=255)
    action_input = fields.JSONField(null=True)
    resources = fields.JSONField(default=list)
    include_new_resources = fields.BooleanField(default=False)
    enabled = fields.BooleanField(default=True)
    order = fields.IntField(default=0)

    class Meta:
        table = "network_topology_collection_item"
        indexes = (("tenant_id", "plan_id"),)


class TopologyCollectionRun(TopologyTenantModel):
    plan_id = fields.CharField(max_length=36)
    topology_id = fields.CharField(max_length=36)
    trigger = fields.CharField(max_length=16)
    triggered_by = fields.CharField(max_length=255, null=True)
    state = fields.CharField(max_length=20, default="PENDING")
    flow_run_id = fields.CharField(max_length=64, null=True)
    manifest_id = fields.CharField(max_length=255, null=True)
    sync_id = fields.CharField(max_length=255, null=True)
    snapshot_id = fields.CharField(max_length=255, null=True)
    analysis_run_id = fields.CharField(max_length=36, null=True)
    snapshot_draft = EncryptedTextField(null=True)
    frozen_targets = fields.JSONField(null=True)
    item_results = fields.JSONField(null=True)
    device_total = fields.IntField(default=0)
    device_succeeded = fields.IntField(default=0)
    result = fields.CharField(max_length=16, null=True)
    graph_revision = fields.BigIntField(null=True)
    error_code = fields.CharField(max_length=64, null=True)
    error_message = fields.TextField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)

    class Meta:
        table = "network_topology_collection_run"
        indexes = (("tenant_id", "topology_id", "state"), ("tenant_id", "plan_id", "ctime"))


class TopologyAgentJob(TopologyTenantModel):
    """Durable agent normalization job and its redacted submission result."""

    analysis_run_id = fields.CharField(max_length=36)
    topology_id = fields.CharField(max_length=36)
    collection_run_id = fields.CharField(max_length=36, null=True)
    attempt = fields.IntField()
    state = fields.CharField(max_length=16, default="QUEUED")
    resource_ids = fields.JSONField(default=list)
    job_token_hash = fields.CharField(max_length=64)
    profile_version = fields.CharField(max_length=255, null=True)
    cache_hits = fields.IntField(default=0)
    cache_misses = fields.IntField(default=0)
    iterations = fields.IntField(default=0)
    token_usage = fields.JSONField(null=True)
    documents = EncryptedTextField(null=True)
    transcript = EncryptedTextField(null=True)
    error_code = fields.CharField(max_length=128, null=True)
    error_message = fields.TextField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)

    class Meta:
        table = "network_topology_agent_job"
        indexes = (("tenant_id", "analysis_run_id"), ("tenant_id", "collection_run_id"))
