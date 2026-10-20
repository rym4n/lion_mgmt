"""Network policy auto-collection plan, items, runs and agent jobs."""

from __future__ import annotations

from tortoise import fields

from models.base import BaseModel, EncryptedTextField

__all__ = (
    "POLICY_RUN_STATES",
    "POLICY_RUN_TERMINAL_STATES",
    "POLICY_RUN_WORKING_STATES",
    "NetworkAccessPolicyAgentJob",
    "NetworkAccessPolicyCollectionItem",
    "NetworkAccessPolicyCollectionPlan",
    "NetworkAccessPolicyCollectionRun",
)

POLICY_RUN_STATES = (
    "PENDING", "COLLECTING", "NORMALIZING", "APPLYING",
    "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
)
POLICY_RUN_TERMINAL_STATES = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"})
POLICY_RUN_WORKING_STATES = frozenset({"COLLECTING", "NORMALIZING", "APPLYING"})


class NetworkAccessPolicyCollectionPlan(BaseModel):
    enabled = fields.BooleanField(default=False)
    schedule_cron = fields.CharField(max_length=100, default="0 0 3 * * *")
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
        table = "network_access_policy_collection_plan"


class NetworkAccessPolicyCollectionItem(BaseModel):
    plan_id = fields.CharField(max_length=36)
    app_name = fields.CharField(max_length=255)
    app_version = fields.CharField(max_length=50, null=True)
    action_func = fields.CharField(max_length=255)
    action_input = fields.JSONField(null=True)
    resources = fields.JSONField(default=list)
    enabled = fields.BooleanField(default=True)
    order = fields.IntField(default=0)

    class Meta:
        table = "network_access_policy_collection_item"
        indexes = (("tenant_id", "plan_id"),)


class NetworkAccessPolicyCollectionRun(BaseModel):
    plan_id = fields.CharField(max_length=36)
    trigger = fields.CharField(max_length=16)
    triggered_by = fields.CharField(max_length=255, null=True)
    state = fields.CharField(max_length=20, default="PENDING")
    flow_run_id = fields.CharField(max_length=64, null=True)
    frozen_targets = fields.JSONField(null=True)
    raw_outputs = EncryptedTextField(null=True)
    item_results = fields.JSONField(null=True)
    device_total = fields.IntField(default=0)
    device_succeeded = fields.IntField(default=0)
    agent_job_id = fields.CharField(max_length=36, null=True)
    compare_run_id = fields.CharField(max_length=36, null=True)
    compare_error = fields.CharField(max_length=64, null=True)
    error_code = fields.CharField(max_length=64, null=True)
    error_message = fields.TextField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    heartbeat_at = fields.DatetimeField(null=True)

    class Meta:
        table = "network_access_policy_collection_run"
        indexes = (("tenant_id", "plan_id", "ctime"), ("tenant_id", "state"))


class NetworkAccessPolicyAgentJob(BaseModel):
    collection_run_id = fields.CharField(max_length=36)
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
    failed = EncryptedTextField(null=True)
    transcript = EncryptedTextField(null=True)
    error_code = fields.CharField(max_length=128, null=True)
    error_message = fields.TextField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)

    class Meta:
        table = "network_access_policy_agent_job"
        indexes = (("tenant_id", "collection_run_id"),)
