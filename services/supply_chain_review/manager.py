# coding=utf-8
from __future__ import annotations

import datetime
import re
import uuid
from contextlib import asynccontextmanager
from typing import Iterable

import orjson

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.supply_chain_review import (
    SupplyChainAttachmentRef,
    FIELD_DATA_TYPES,
    RELATION_CARDINALITIES,
    SupplyChainBoundaryLog,
    SupplyChainActualPolicy,
    SupplyChainCi,
    SupplyChainCiFieldDef,
    SupplyChainCiRelation,
    SupplyChainCiRelationType,
    SupplyChainCiType,
    SupplyChainCollectionRun,
    SupplyChainCollectionTask,
    SupplyChainExceptionDeviceLog,
    SupplyChainNetworkTopology,
    SupplyChainNdaRecord,
    SupplyChainNotificationEvent,
    SupplyChainParseMappingTemplate,
    SupplyChainPolicyChange,
    SupplyChainPolicyCompareItem,
    SupplyChainPolicyCompareRun,
    SupplyChainPresetPolicy,
    SupplyChainProjectAuthorization,
    SupplyChainRemediationRecord,
    SupplyChainReviewIssue,
    SupplyChainDisposalRule,
    SupplyChainDisposalTask,
    SupplyChainUnknownTerminal,
)
from models.user import Tenant
from tortoise.expressions import Q
from tortoise.transactions import in_transaction
from pypinyin import lazy_pinyin


KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
CI_TYPE_CATEGORIES = {"asset", "master_data", "org"}
CI_TYPE_STATUSES = {"active", "disabled"}
CI_STATUSES = {"active", "disabled", "archived"}
RISK_STATUSES = {"normal", "low", "medium", "high"}
SECRECY_LEVELS = {"low", "medium", "high", "critical"}
AUTHORIZATION_STATUSES = {"active", "expired", "revoked"}
ACCESS_CONTROL_STATUSES = {"normal", "abnormal", "revoked"}
NDA_SIGN_STATUSES = {"signed", "unsigned", "expired"}
UNKNOWN_TERMINAL_STATUSES = {"pending", "confirmed", "converted"}
UNKNOWN_DISCOVERY_SOURCES = {"switch_mac", "arp", "scan"}
BOUNDARY_LOG_CATEGORIES = {"file_operation", "mail_outbound", "system_access"}
EXCEPTION_LOG_TYPES = {"insert", "remove", "use"}
EXCEPTION_ABNORMAL_TYPES = {"none", "unauthorized_terminal", "unauthorized_person", "off_hours", "unregistered"}
EXCEPTION_LOG_STATUSES = {"pending", "confirmed", "ignored", "converted"}
COLLECTION_DOMAINS = {"network", "personnel_nda", "boundary", "exception_device"}
COLLECTION_EXECUTION_MODES = {"playbook_action", "tool_action", "timed_task", "event_ingestion", "http_api", "file_parse", "manual"}
COLLECTION_RUN_STATUSES = {"pending", "running", "success", "failed", "partial"}
AFTER_SUCCESS_ACTIONS = {"none", "policy_compare", "risk_evaluate"}
POLICY_ACTIONS = {"permit", "deny"}
POLICY_COMPARE_TYPES = {"matched", "added", "missing", "changed", "unparsed"}
POLICY_HANDLE_STATUSES = {"pending", "confirmed", "ignored", "converted"}
POLICY_CHANGE_TYPES = {"added", "modified", "deleted", "synced"}
MAX_POLICY_SYNC_ITEMS = 5000
TOPOLOGY_STATUSES = {"active", "archived"}
ISSUE_STATUSES = {"pending", "in_progress", "pending_review", "closed", "returned"}
ISSUE_TRANSITIONS = {
    "pending": {"in_progress", "closed"},
    "in_progress": {"pending_review"},
    "pending_review": {"closed", "returned"},
    "returned": {"in_progress"},
    "closed": set(),
}
DISPOSAL_TRIGGER_TYPES = {"person_resigned", "nda_expired", "nda_unsigned", "authorization_expired", "access_control_abnormal"}
DISPOSAL_TASK_STATUSES = {"pending_confirm", "executing", "success", "failed", "skipped", "cancelled"}
DISPOSAL_ACTIONS = {
    "revoke_project_authorization",
    "revoke_access_control",
    "disable_account",
    "unbind_terminal",
    "reassign_exception_device",
    "cancel_exception_device",
    "create_review_issue",
}
NOTIFICATION_STATUSES = {"pending", "sent", "failed"}
LEDGER_FILTER_FIELDS = [
    "project_area_id",
    "accessory_project_id",
    "personnel_ci_id",
    "endpoint_ci_id",
    "network_device_ci_id",
    "exception_device_ci_id",
    "status",
    "sign_status",
    "access_control_status",
    "log_category",
    "risk_flag",
    "abnormal_type",
]

LEDGER_CONFIGS = {
    "personnel-authorization": {
        "model": SupplyChainProjectAuthorization,
        "permission_resource": "authorization",
        "required": ["personnel_ci_id"],
        "fields": [
            "personnel_ci_id",
            "project_area_id",
            "accessory_project_id",
            "secrecy_level",
            "project_role",
            "authorized_from",
            "authorized_to",
            "access_control_status",
            "status",
            "remark",
        ],
        "enums": {
            "secrecy_level": SECRECY_LEVELS,
            "access_control_status": ACCESS_CONTROL_STATUSES,
            "status": AUTHORIZATION_STATUSES,
        },
        "keyword": ["project_role", "remark"],
        "order": ["-utime"],
    },
    "nda-record": {
        "model": SupplyChainNdaRecord,
        "permission_resource": "nda",
        "required": ["personnel_ci_id"],
        "fields": [
            "personnel_ci_id",
            "project_area_id",
            "accessory_project_id",
            "nda_type",
            "sign_status",
            "signed_at",
            "expires_at",
            "evidence_attachment_ids",
            "source_type",
            "source_record_id",
            "last_sync_at",
            "remark",
        ],
        "enums": {"sign_status": NDA_SIGN_STATUSES, "source_type": {"hr", "esign", "manual"}},
        "keyword": ["nda_type", "source_record_id", "remark"],
        "order": ["-utime"],
    },
    "unknown-terminal": {
        "model": SupplyChainUnknownTerminal,
        "permission_resource": "terminal",
        "required": ["mac_address"],
        "fields": [
            "accessory_project_id",
            "mac_address",
            "ip_address",
            "network_device_ci_id",
            "port",
            "first_seen_at",
            "last_seen_at",
            "discovery_source",
            "status",
            "endpoint_ci_id",
            "review_issue_id",
            "remark",
        ],
        "enums": {"status": UNKNOWN_TERMINAL_STATUSES, "discovery_source": UNKNOWN_DISCOVERY_SOURCES},
        "keyword": ["mac_address", "ip_address", "port", "remark"],
        "order": ["-last_seen_at", "-utime"],
    },
    "boundary-log": {
        "model": SupplyChainBoundaryLog,
        "permission_resource": "boundary",
        "required": ["log_category"],
        "fields": [
            "accessory_project_id",
            "log_category",
            "operator_account",
            "client_address",
            "personnel_ci_id",
            "endpoint_ci_id",
            "occurred_at",
            "source_type",
            "risk_flag",
            "detail",
        ],
        "enums": {"log_category": BOUNDARY_LOG_CATEGORIES},
        "keyword": ["operator_account", "client_address", "source_type"],
        "order": ["-occurred_at", "-utime"],
    },
    "exception-device-log": {
        "model": SupplyChainExceptionDeviceLog,
        "permission_resource": "exception",
        "required": ["exception_device_ci_id"],
        "fields": [
            "accessory_project_id",
            "exception_device_ci_id",
            "endpoint_ci_id",
            "personnel_ci_id",
            "log_type",
            "detail",
            "occurred_at",
            "source_type",
            "abnormal_type",
            "status",
            "review_issue_id",
        ],
        "enums": {
            "log_type": EXCEPTION_LOG_TYPES,
            "abnormal_type": EXCEPTION_ABNORMAL_TYPES,
            "status": EXCEPTION_LOG_STATUSES,
            "source_type": {"file_parse", "manual"},
        },
        "keyword": ["source_type"],
        "order": ["-occurred_at", "-utime"],
    },
}

PROCESS_FILTER_FIELDS = [
    "project_area_id",
    "accessory_project_id",
    "domain",
    "status",
    "enabled",
    "device_ci_id",
    "device_key",
    "device_type",
    "policy_type",
    "collection_run_id",
    "compare_run_id",
    "compare_type",
    "handle_status",
    "change_type",
    "effective_status",
]

PROCESS_CONFIGS = {
    "collection-task": {
        "model": SupplyChainCollectionTask,
        "resource": "collection",
        "required": ["domain", "name"],
        "fields": [
            "accessory_project_id",
            "domain",
            "name",
            "execution_mode",
            "playbook_id",
            "playbook_version_id",
            "action_id",
            "action_name",
            "timed_task_id",
            "event_ingestion_id",
            "target_type",
            "target_ci_ids",
            "credential_ref",
            "input_params",
            "parser_template_id",
            "mapping_template_id",
            "schedule_enabled",
            "cron_expression",
            "after_success_action",
            "enabled",
        ],
        "enums": {
            "domain": COLLECTION_DOMAINS,
            "execution_mode": COLLECTION_EXECUTION_MODES,
            "after_success_action": AFTER_SUCCESS_ACTIONS,
        },
        "keyword": ["name", "action_name"],
        "order": ["-utime"],
    },
    "collection-run": {
        "model": SupplyChainCollectionRun,
        "resource": "collection",
        "required": ["domain", "run_id"],
        "fields": [
            "task_id",
            "accessory_project_id",
            "domain",
            "run_id",
            "playbook_run_id",
            "action_run_id",
            "timed_run_id",
            "event_batch_id",
            "status",
            "started_at",
            "finished_at",
            "raw_payload_ref",
            "parsed_result_ref",
            "total_count",
            "success_count",
            "failed_count",
            "error_message",
        ],
        "enums": {"domain": COLLECTION_DOMAINS, "status": COLLECTION_RUN_STATUSES},
        "keyword": ["run_id", "error_message"],
        "order": ["-ctime"],
    },
    "parse-template": {
        "model": SupplyChainParseMappingTemplate,
        "resource": "collection",
        "required": ["domain", "name"],
        "fields": ["domain", "name", "source_format", "parser_type", "parser_rules", "mapping_rules", "enabled"],
        "enums": {"domain": COLLECTION_DOMAINS},
        "keyword": ["name"],
        "order": ["-utime"],
    },
    "preset-policy": {
        "model": SupplyChainPresetPolicy,
        "resource": "policy",
        "required": ["rule_name"],
        "fields": [
            "accessory_project_id",
            "device_ci_id",
            "rule_name",
            "source_zone",
            "source_address",
            "target_zone",
            "target_address",
            "protocol",
            "port",
            "action",
            "business_purpose",
            "approval_status",
            "effective_status",
            "valid_from",
            "valid_to",
            "risk_level",
        ],
        "enums": {"action": POLICY_ACTIONS, "risk_level": RISK_STATUSES - {"normal"}},
        "keyword": ["rule_name", "source_address", "target_address", "business_purpose"],
        "order": ["-utime"],
    },
    "actual-policy": {
        "model": SupplyChainActualPolicy,
        "resource": "policy",
        "required": ["device_key", "policy_key"],
        "fields": [
            "accessory_project_id",
            "device_ci_id",
            "device_key",
            "device_name",
            "device_type",
            "collection_run_id",
            "raw_rule_id",
            "policy_key",
            "policy_type",
            "rule_name",
            "source_zone",
            "source_address",
            "target_zone",
            "target_address",
            "protocol",
            "port",
            "action",
            "enabled",
            "raw_text",
            "policy_data",
            "parse_status",
        ],
        "enums": {"action": POLICY_ACTIONS, "parse_status": {"parsed", "unparsed"}},
        "keyword": [
            "device_key",
            "device_name",
            "policy_key",
            "rule_name",
            "raw_rule_id",
            "source_address",
            "target_address",
        ],
        "order": ["-utime"],
    },
    "policy-change": {
        "model": SupplyChainPolicyChange,
        "resource": "policy",
        "required": ["change_type"],
        "fields": [
            "accessory_project_id",
            "device_ci_id",
            "device_key",
            "actual_policy_id",
            "policy_key",
            "batch_id",
            "policy_name",
            "change_type",
            "revision",
            "before_data",
            "after_data",
            "sync_source",
            "diff_text",
            "detected_at",
            "risk_level",
            "external_approve_ref",
            "external_approve_result",
            "review_issue_id",
        ],
        "enums": {"change_type": POLICY_CHANGE_TYPES, "risk_level": RISK_STATUSES - {"normal"}},
        "keyword": [
            "device_key",
            "policy_key",
            "policy_name",
            "batch_id",
            "diff_text",
            "external_approve_ref",
        ],
        "order": ["-detected_at", "-utime"],
    },
    "policy-compare-run": {
        "model": SupplyChainPolicyCompareRun,
        "resource": "policy",
        "required": [],
        "fields": [
            "accessory_project_id",
            "collection_run_id",
            "total",
            "matched",
            "added",
            "missing",
            "changed",
            "unparsed",
            "started_at",
            "finished_at",
        ],
        "keyword": [],
        "order": ["-ctime"],
    },
    "network-topology": {
        "model": SupplyChainNetworkTopology,
        "resource": "topology",
        "required": ["name"],
        "fields": ["project_area_id", "accessory_project_id", "name", "version", "graph_data", "change_summary", "status"],
        "enums": {"status": TOPOLOGY_STATUSES},
        "keyword": ["name", "version", "change_summary"],
        "order": ["-utime"],
    },
    "review-issue": {
        "model": SupplyChainReviewIssue,
        "resource": "issue",
        "required": ["domain", "title"],
        "fields": [
            "accessory_project_id",
            "domain",
            "source_object_type",
            "source_object_id",
            "issue_no",
            "title",
            "description",
            "risk_level",
            "remediation_requirement",
            "responsible_person",
            "due_date",
            "status",
            "attachment_ids",
        ],
        "enums": {"risk_level": RISK_STATUSES - {"normal"}, "status": ISSUE_STATUSES},
        "keyword": ["issue_no", "title", "description", "responsible_person"],
        "order": ["-utime"],
    },
    "remediation-record": {
        "model": SupplyChainRemediationRecord,
        "resource": "issue",
        "required": ["review_issue_id", "action_type"],
        "fields": ["review_issue_id", "action_type", "content", "attachment_ids", "operator", "operated_at", "from_status", "to_status"],
        "keyword": ["content", "operator"],
        "order": ["-operated_at", "-ctime"],
    },
    "disposal-rule": {
        "model": SupplyChainDisposalRule,
        "resource": "disposal",
        "required": ["trigger_type"],
        "fields": [
            "project_area_id",
            "accessory_project_id",
            "trigger_type",
            "enabled",
            "execution_mode",
            "actions",
            "playbook_id",
            "action_id",
            "input_mapping",
            "require_review_issue",
            "risk_level",
        ],
        "enums": {"trigger_type": DISPOSAL_TRIGGER_TYPES, "risk_level": RISK_STATUSES - {"normal"}},
        "keyword": ["trigger_type"],
        "order": ["-utime"],
    },
    "disposal-task": {
        "model": SupplyChainDisposalTask,
        "resource": "disposal",
        "required": ["trigger_type"],
        "fields": [
            "rule_id",
            "trigger_type",
            "project_area_id",
            "accessory_project_id",
            "personnel_ci_id",
            "related_ci_ids",
            "actions",
            "review_issue_id",
        ],
        "enums": {"trigger_type": DISPOSAL_TRIGGER_TYPES},
        "keyword": ["trigger_type", "result_message"],
        "order": ["-utime"],
    },
    "attachment-ref": {
        "model": SupplyChainAttachmentRef,
        "resource": "attachment",
        "required": ["object_type", "object_id", "file_id", "file_name"],
        "fields": ["object_type", "object_id", "file_id", "file_name", "file_type", "file_size", "uploaded_by", "uploaded_at", "remark"],
        "keyword": ["object_type", "file_name", "remark"],
        "order": ["-uploaded_at", "-ctime"],
    },
    "notification-event": {
        "model": SupplyChainNotificationEvent,
        "resource": "notify",
        "required": ["event_type", "title"],
        "fields": [
            "event_type",
            "title",
            "content",
            "risk_level",
            "target_type",
            "target_id",
            "project_area_id",
            "accessory_project_id",
            "link",
            "receivers",
            "receiver_roles",
            "status",
        ],
        "enums": {"status": NOTIFICATION_STATUSES},
        "keyword": ["event_type", "title", "content"],
        "order": ["-ctime"],
    },
}


def require_dict(data) -> dict:
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return data


def ledger_config(domain: str) -> dict:
    config = LEDGER_CONFIGS.get(domain)
    if not config:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return config


def process_config(domain: str) -> dict:
    config = PROCESS_CONFIGS.get(domain)
    if not config:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return config


def normalize_page(data: dict) -> tuple[int, int, int]:
    page = max(1, int(data.get("page", 1) or 1))
    size = max(1, min(100, int(data.get("size", data.get("page_size", 20)) or 20)))
    return page, size, (page - 1) * size


def validate_key(value: str | None, field: str = "key") -> str:
    value = (value or "").strip()
    if not KEY_RE.match(value):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


def operator_name(request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("username") or account.get("name") or account.get("id")
    return getattr(account, "username", None) or getattr(account, "name", None) or getattr(account, "id", None)


def tenant_id(request) -> str:
    value = getattr(request.ctx, "session", {}).get("tenant_id")
    if not value:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


def _looks_like_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _model_ref_filter(value: str) -> dict:
    return {"id": value} if _looks_like_uuid(value) else {"key": value}


def _model_ref_list_filter(values: Iterable[str]) -> dict:
    refs = list(values)
    if refs and all(_looks_like_uuid(value) for value in refs):
        return {"id__in": refs}
    return {"key__in": refs}


def _ci_model_id(ci) -> str:
    return getattr(ci, "ci_type_id", None) or getattr(ci, "ci_type_key")


def _field_model_id(field) -> str:
    return getattr(field, "ci_type_id", None) or getattr(field, "ci_type_key")


def _field_ref_model_id(field):
    return getattr(field, "ref_ci_type_id", None) or getattr(field, "ref_ci_type", None)


def _relation_source_model_ids(relation_type) -> list[str]:
    if isinstance(relation_type, dict):
        return relation_type.get("source_ci_type_ids") or relation_type.get("source_ci_types") or []
    return getattr(relation_type, "source_ci_type_ids", None) or relation_type.source_ci_types or []


def _relation_target_model_ids(relation_type) -> list[str]:
    if isinstance(relation_type, dict):
        return relation_type.get("target_ci_type_ids") or relation_type.get("target_ci_types") or []
    return getattr(relation_type, "target_ci_type_ids", None) or relation_type.target_ci_types or []


async def get_ci_type_or_404(tenant: str, key_or_id: str) -> SupplyChainCiType:
    ci_type = await SupplyChainCiType.filter(Q(id=key_or_id) | Q(key=key_or_id), tenant_id=tenant, deleted=False).first()
    if not ci_type:
        raise CustomError(ErrorCode.NOT_FOUND)
    return ci_type


async def get_relation_type_or_404(tenant: str, key_or_id: str) -> SupplyChainCiRelationType:
    relation_type = await SupplyChainCiRelationType.filter(Q(id=key_or_id) | Q(key=key_or_id), tenant_id=tenant, deleted=False).first()
    if not relation_type:
        raise CustomError(ErrorCode.NOT_FOUND)
    return relation_type


async def get_ci_or_404(tenant: str, ci_id: str) -> SupplyChainCi:
    ci = await SupplyChainCi.filter(id=ci_id, tenant_id=tenant, deleted=False).first()
    if not ci:
        raise CustomError(ErrorCode.NOT_FOUND)
    return ci


async def fields_for_type(tenant: str, ci_type_id_or_key: str) -> list[SupplyChainCiFieldDef]:
    ci_type = await get_ci_type_or_404(tenant, ci_type_id_or_key)
    return await SupplyChainCiFieldDef.filter(
        tenant_id=tenant,
        ci_type_id=ci_type.id,
        deleted=False,
    ).order_by("sort", "ctime").all()


def enum_values(field: SupplyChainCiFieldDef) -> set:
    values = set()
    enum_options = field.get("enum_options", []) if isinstance(field, dict) else field.enum_options
    for option in enum_options or []:
        if isinstance(option, dict):
            value = option.get("value")
        else:
            value = option
        if value is not None:
            values.add(value)
    return values


def relation_field_defs(relation_type: SupplyChainCiRelationType) -> list[dict]:
    field_defs = []
    for item in relation_type.relation_fields or []:
        if not isinstance(item, dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
        field_defs.append(item)
    return field_defs


def field_attr(field, name: str, default=None):
    if isinstance(field, dict):
        return field.get(name, default)
    return getattr(field, name, default)


def serialize_ci_field(field: SupplyChainCiFieldDef, primary_field: str) -> dict:
    payload = dict(field.json)
    payload["is_primary"] = field.field_key == primary_field
    return payload


async def validate_field_value(tenant: str, field, value):
    data_type = field_attr(field, "data_type")
    if value in (None, "") or (data_type == "attachment" and value == []):
        if field_attr(field, "required", False):
            raise CustomError(ErrorCode.PARAM_ERROR)
        return
    if data_type == "number" and not isinstance(value, (int, float)):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "bool" and not isinstance(value, bool):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "enum" and enum_values(field) and value not in enum_values(field):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "attachment" and not isinstance(value, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "ref":
        ref_ci_type_id = field_attr(field, "ref_ci_type_id")
        if not ref_ci_type_id:
            raise CustomError(ErrorCode.PARAM_ERROR)
        target = await SupplyChainCi.filter(
            id=value,
            tenant_id=tenant,
            ci_type_id=ref_ci_type_id,
            deleted=False,
        ).first()
        if not target:
            raise CustomError(ErrorCode.PARAM_ERROR)


async def validate_dynamic_attributes(tenant: str, field_defs: list, attributes: dict) -> None:
    field_by_key = {}
    for field in field_defs:
        field_key = field.field_key if hasattr(field, "field_key") else field.get("field_key")
        if not field_key:
            raise CustomError(ErrorCode.PARAM_ERROR)
        field_by_key[field_key] = field
        default_value = field.default_value if hasattr(field, "default_value") else field.get("default_value")
        if field_key not in attributes and default_value is not None:
            attributes[field_key] = default_value
        await validate_field_value(tenant, field, attributes.get(field_key))
    if set(attributes.keys()) - set(field_by_key.keys()):
        raise CustomError(ErrorCode.PARAM_ERROR)


async def validate_ci_attributes(tenant: str, ci_type_id: str, attributes: dict, current_ci_id: str | None = None) -> None:
    field_defs = await fields_for_type(tenant, ci_type_id)
    await validate_dynamic_attributes(tenant, field_defs, attributes)
    for field in field_defs:
        if not field.unique:
            continue
        value = attributes.get(field.field_key)
        if value in (None, ""):
            continue
        existing = await SupplyChainCi.filter(tenant_id=tenant, ci_type_id=ci_type_id, deleted=False).all()
        for item in existing:
            if current_ci_id and item.id == current_ci_id:
                continue
            if (item.attributes or {}).get(field.field_key) == value:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)


def payload_ci_type(data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    key = validate_key(data.get("key")) if data.get("key") else None
    category = data.get("category") or "asset"
    status = data.get("status") or "active"
    raw_sort = data.get("sort", 0)
    if raw_sort is None or raw_sort == "":
        raw_sort = 0
    try:
        if isinstance(raw_sort, bool) or (
            isinstance(raw_sort, float) and not raw_sort.is_integer()
        ):
            raise ValueError
        sort = int(raw_sort)
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR) from None
    if (
        category not in CI_TYPE_CATEGORIES
        or status not in CI_TYPE_STATUSES
        or sort < 0
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return {
        "key": key,
        "name": (data.get("name") or key or "").strip(),
        "icon": data.get("icon"),
        "color": data.get("color"),
        "category": category,
        "primary_field": "",
        "status": status,
        "is_locked": bool(data.get("is_locked", False)),
        "sort": sort,
        "updater": operator,
    }


def payload_field(data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    ci_type_id = data.get("ci_type_id") or data.get("ci_type_key")
    if not ci_type_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    payload = payload_dynamic_field(data, allow_missing_key=True)
    return {
        "ci_type_id": ci_type_id,
        "updater": operator,
        **payload,
    }


def payload_dynamic_field(data: dict, *, allow_missing_key: bool = False) -> dict:
    data = require_dict(data)
    field_key = (
        validate_key(data.get("field_key"), "field_key")
        if data.get("field_key")
        else None if allow_missing_key else validate_key(None, "field_key")
    )
    data_type = data.get("data_type")
    if data_type not in FIELD_DATA_TYPES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "ref" and not (data.get("ref_ci_type_id") or data.get("ref_ci_type")):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return {
        "field_key": field_key,
        "label": (data.get("label") or field_key).strip(),
        "data_type": data_type,
        "enum_options": data.get("enum_options") or [],
        "ref_ci_type_id": data.get("ref_ci_type_id") or data.get("ref_ci_type"),
        "required": bool(data.get("required", False)),
        "unique": bool(data.get("unique", False)),
        "default_value": data.get("default_value"),
        "sort": int(data.get("sort", 0) or 0),
        "is_locked": bool(data.get("is_locked", False)),
    }


def _field_key_base(label: str) -> str:
    transliterated = "".join(
        lazy_pinyin(label, errors=lambda characters: list(characters))
    )
    base = re.sub(r"[^a-zA-Z0-9]+", "_", transliterated).strip("_").lower()
    if not base:
        base = "field"
    if not base[0].isalpha():
        base = f"field_{base}"
    return base[:64].rstrip("_") or "field"


def _has_duplicate_field_label(fields: Iterable, label: str, exclude_id=None) -> bool:
    normalized_label = label.strip().casefold()
    return any(
        field.id != exclude_id
        and str(field.label or "").strip().casefold() == normalized_label
        for field in fields
    )


async def _allocate_field_key(
    tenant: str,
    ci_type_id: str,
    label: str,
    connection,
) -> str:
    base = _field_key_base(label)
    candidate = base
    suffix = 1
    while await SupplyChainCiFieldDef.filter(
        tenant_id=tenant,
        ci_type_id=ci_type_id,
        field_key=candidate,
    ).using_db(connection).exists():
        suffix += 1
        suffix_text = f"_{suffix}"
        candidate = f"{base[:64 - len(suffix_text)]}{suffix_text}"
    return candidate


async def _allocate_relation_key(tenant: str, name: str, connection) -> str:
    base = _field_key_base(name)
    candidate = base
    suffix = 1
    while await SupplyChainCiRelationType.filter(
        tenant_id=tenant, key=candidate
    ).using_db(connection).exists():
        suffix += 1
        suffix_text = f"_{suffix}"
        candidate = f"{base[:64 - len(suffix_text)]}{suffix_text}"
    return candidate


def _relation_name_overlaps(relation_type, name: str, model_ids: set[str]) -> bool:
    if str(relation_type.name or "").strip().casefold() != name.strip().casefold():
        return False
    relation_model_ids = set(_relation_source_model_ids(relation_type)) | set(
        _relation_target_model_ids(relation_type)
    )
    return not model_ids or not relation_model_ids or bool(model_ids & relation_model_ids)


def payload_relation_type(data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    key = validate_key(data.get("key")) if data.get("key") else None
    cardinality = data.get("cardinality") or "n-n"
    if cardinality not in RELATION_CARDINALITIES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    source_ci_type_ids = list(dict.fromkeys(data.get("source_ci_type_ids") or data.get("source_ci_types") or []))
    target_ci_type_ids = list(dict.fromkeys(data.get("target_ci_type_ids") or data.get("target_ci_types") or []))
    return {
        "key": key,
        "name": (data.get("name") or key).strip(),
        "source_ci_type_ids": source_ci_type_ids,
        "target_ci_type_ids": target_ci_type_ids,
        "cardinality": cardinality,
        "relation_fields": [payload_dynamic_field(item) for item in data.get("relation_fields") or []],
        "is_locked": bool(data.get("is_locked", False)),
        "updater": operator,
    }


def ensure_expected_existence(existing, expected_exists: bool | None) -> None:
    if expected_exists is None or (existing is not None) == expected_exists:
        return
    raise CustomError(
        ErrorCode.DATA_ALREADY_EXISTS if existing is not None else ErrorCode.NOT_FOUND
    )


LOCKED_CI_TYPE_STRUCTURE_FIELDS = (
    "key",
    "name",
    "icon",
    "color",
    "category",
    "primary_field",
    "status",
)


def ensure_locked_ci_type_unlock_only(
    existing: SupplyChainCiType,
    data: dict,
    payload: dict,
) -> None:
    """Only an explicit, structure-preserving unlock may mutate a locked model."""
    if data.get("is_locked") is not False:
        raise CustomError(ErrorCode.FORBIDDEN)

    for field_name in LOCKED_CI_TYPE_STRUCTURE_FIELDS:
        if field_name not in data:
            continue
        if field_name == "primary_field":
            requested_value = (
                validate_key(data.get(field_name), field_name)
                if data.get(field_name)
                else ""
            )
        else:
            requested_value = payload[field_name]
        if requested_value != getattr(existing, field_name):
            raise CustomError(ErrorCode.FORBIDDEN)


async def lock_tenant_for_structure_write(tenant: str, connection) -> Tenant:
    tenant_row = (
        await Tenant.filter(id=tenant, deleted=False)
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if not tenant_row:
        raise CustomError(ErrorCode.NOT_FOUND)
    return tenant_row


@asynccontextmanager
async def structure_write_transaction(tenant: str, connection=None):
    """Serialize model-structure writes per tenant.

    A supplied connection is owned by an outer structure transaction which has
    already acquired the tenant row. Reusing it must not start another
    transaction or acquire the tenant lock a second time.
    """
    if connection is not None:
        yield connection
        return
    async with in_transaction() as transaction_connection:
        await lock_tenant_for_structure_write(tenant, transaction_connection)
        yield transaction_connection


async def lock_ci_type_keys_for_structure(
    tenant: str,
    ci_type_keys: Iterable[str],
    connection,
    *,
    include_all: bool = False,
    include_deleted: bool = False,
    require_all: bool = True,
) -> list[SupplyChainCiType]:
    keys = {key for key in ci_type_keys if key}
    if not keys and not include_all:
        return []
    filters = {
        "tenant_id": tenant,
    }
    if not include_deleted:
        filters["deleted"] = False
    if not include_all:
        filters.update(_model_ref_list_filter(sorted(keys)))
    ci_types = (
        await SupplyChainCiType.filter(**filters)
        .using_db(connection)
        .order_by("key")
        .select_for_update()
        .all()
    )
    resolved_refs = {
        value
        for ci_type in ci_types
        for value in (getattr(ci_type, "id", None), ci_type.key)
        if value
    }
    if require_all and keys - resolved_refs:
        raise CustomError(ErrorCode.NOT_FOUND)
    if any(ci_type.is_locked for ci_type in ci_types):
        raise CustomError(ErrorCode.FORBIDDEN)
    return ci_types


async def save_ci_type(
    tenant: str,
    data: dict,
    operator: str | None,
    *,
    expected_exists: bool | None = None,
    allow_unlock: bool = True,
    allow_sort_update: bool = False,
    connection=None,
) -> SupplyChainCiType:
    payload = payload_ci_type(data, operator)
    sort_was_provided = allow_sort_update and "sort" in data
    if not data.get("id") and not payload["key"]:
        payload["key"] = f"m_{uuid.uuid4().hex}"
    async with structure_write_transaction(tenant, connection) as connection:
        if data.get("id"):
            query = SupplyChainCiType.filter(
                Q(id=data["id"]) | Q(key=data["id"]),
                tenant_id=tenant,
                deleted=False,
            )
        else:
            query = SupplyChainCiType.filter(
                tenant_id=tenant,
                key=payload["key"],
            )
        existing = (
            await query.using_db(connection)
            .order_by("key")
            .select_for_update()
            .first()
        )
        if data.get("id") and not existing:
            raise CustomError(ErrorCode.NOT_FOUND)
        if existing and not payload["key"]:
            payload["key"] = existing.key
        if not payload["name"]:
            payload["name"] = existing.name if existing else payload["key"]
        if data.get("id") and existing.key != payload["key"]:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if not data.get("id") and existing and getattr(existing, "deleted", False):
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        ensure_expected_existence(existing, expected_exists)
        if existing:
            if existing.is_locked:
                if not allow_unlock:
                    raise CustomError(ErrorCode.FORBIDDEN)
                ensure_locked_ci_type_unlock_only(existing, data, payload)
                existing.is_locked = False
                existing.updater = operator
                await existing.save(
                    using_db=connection,
                    update_fields=["is_locked", "updater", "utime"],
                )
                return existing
            for key in ("category", "color", "icon"):
                if key not in data:
                    payload.pop(key, None)
            if not sort_was_provided:
                payload.pop("sort", None)
            payload.pop("primary_field", None)
            for key, value in payload.items():
                setattr(existing, key, value)
            await existing.save(
                using_db=connection,
                update_fields=[*payload, "utime"],
            )
            return existing
        if not sort_was_provided:
            last_model = (
                await SupplyChainCiType.filter(
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .order_by("-sort", "-ctime")
                .first()
            )
            payload["sort"] = max(0, int(getattr(last_model, "sort", 0) or 0)) + 1
        return await SupplyChainCiType.create(
            using_db=connection,
            tenant_id=tenant,
            creator=operator,
            **payload,
        )


async def reorder_ci_types(
    tenant: str,
    ci_type_ids: list[str] | None,
    operator: str | None,
    *,
    connection=None,
) -> list[SupplyChainCiType]:
    if (
        not isinstance(ci_type_ids, list)
        or any(
            not isinstance(ci_type_id, str) or not ci_type_id
            for ci_type_id in ci_type_ids
        )
        or len(ci_type_ids) != len(set(ci_type_ids))
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    async with structure_write_transaction(tenant, connection) as connection:
        active_models = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .order_by("sort", "key")
            .select_for_update()
            .all()
        )
        models_by_id = {str(model.id): model for model in active_models}
        if len(ci_type_ids) != len(active_models) or set(ci_type_ids) != set(
            models_by_id
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)

        ordered_models = [models_by_id[ci_type_id] for ci_type_id in ci_type_ids]
        for position, model in enumerate(ordered_models, start=1):
            if model.sort == position:
                continue
            model.sort = position
            model.updater = operator
            await model.save(
                using_db=connection,
                update_fields=["sort", "updater", "utime"],
            )
    return ordered_models


async def save_field(
    tenant: str,
    data: dict,
    operator: str | None,
    *,
    expected_exists: bool | None = None,
    connection=None,
) -> SupplyChainCiFieldDef:
    payload = payload_field(data, operator)
    payload["label"] = str(payload.get("label") or "").strip()
    if not payload["label"]:
        raise CustomError(ErrorCode.PARAM_ERROR)
    requested_model_ref = payload["ci_type_id"]
    make_primary = data.get("is_primary") is True
    async with structure_write_transaction(tenant, connection) as connection:
        ci_type = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_filter(payload["ci_type_id"]),
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not ci_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        if ci_type.is_locked:
            raise CustomError(ErrorCode.FORBIDDEN)
        payload["ci_type_id"] = ci_type.id
        payload["ci_type_key"] = ci_type.key
        if payload.get("ref_ci_type_id"):
            ref_ci_type = (
                await SupplyChainCiType.filter(
                    tenant_id=tenant,
                    deleted=False,
                    **_model_ref_filter(payload["ref_ci_type_id"]),
                )
                .using_db(connection)
                .first()
            )
            if not ref_ci_type:
                raise CustomError(ErrorCode.NOT_FOUND)
            payload["ref_ci_type_id"] = ref_ci_type.id
            payload["ref_ci_type"] = ref_ci_type.key
        else:
            payload["ref_ci_type"] = None

        existing = None
        if data.get("id"):
            existing = (
                await SupplyChainCiFieldDef.filter(
                    id=data["id"],
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not existing:
                raise CustomError(ErrorCode.NOT_FOUND)
            if _field_model_id(existing) not in {payload["ci_type_id"], ci_type.key}:
                raise CustomError(ErrorCode.PARAM_ERROR)
        if not existing and payload.get("field_key"):
            existing = (
                await SupplyChainCiFieldDef.filter(
                    tenant_id=tenant,
                    field_key=payload["field_key"],
                    deleted=False,
                    **(
                        {"ci_type_id": payload["ci_type_id"]}
                        if _looks_like_uuid(requested_model_ref)
                        else {"ci_type_key": ci_type.key}
                    ),
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
        ensure_expected_existence(existing, expected_exists)
        active_fields = await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            ci_type_id=payload["ci_type_id"],
            deleted=False,
        ).using_db(connection).select_for_update().all()
        if _has_duplicate_field_label(
            active_fields,
            payload["label"],
            getattr(existing, "id", None),
        ):
            raise CustomError(ErrorCode.ASSET_FIELD_NAME_EXISTS)
        if existing:
            payload["field_key"] = existing.field_key
        elif not payload.get("field_key"):
            payload["field_key"] = await _allocate_field_key(
                tenant,
                payload["ci_type_id"],
                payload["label"],
                connection,
            )
        referenced_ci_type_keys = {
            key
            for key in (
                payload.get("ref_ci_type"),
                getattr(existing, "ref_ci_type", None),
            )
            if key and key != ci_type.key
        }
        await lock_ci_type_keys_for_structure(
            tenant,
            referenced_ci_type_keys,
            connection,
            include_deleted=True,
            require_all=False,
        )

        existing_was_primary = bool(
            existing and existing.field_key == ci_type.primary_field
        )
        if make_primary and not existing_was_primary and payload["data_type"] != "text":
            raise CustomError(ErrorCode.PARAM_ERROR)
        if existing_was_primary and payload["data_type"] not in {
            "text",
            existing.data_type,
        }:
            raise CustomError(ErrorCode.PARAM_ERROR)

        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            await existing.save(using_db=connection)
            item = existing
        else:
            item = await SupplyChainCiFieldDef.create(
                using_db=connection,
                tenant_id=tenant,
                creator=operator,
                **payload,
            )

        # `false` never clears the model primary field. Selecting another field replaces
        # it, and renaming the current primary follows the new key to avoid a stale link.
        if (
            make_primary or existing_was_primary
        ) and ci_type.primary_field != item.field_key:
            ci_type.primary_field = item.field_key
            ci_type.updater = operator
            await ci_type.save(
                using_db=connection,
                update_fields=["primary_field", "updater", "utime"],
            )
    return item


async def reorder_fields(
    tenant: str,
    ci_type_ref: str | None,
    field_ids: list[str] | None,
    operator: str | None,
    *,
    connection=None,
) -> list[SupplyChainCiFieldDef]:
    if (
        not ci_type_ref
        or not isinstance(field_ids, list)
        or any(not isinstance(field_id, str) or not field_id for field_id in field_ids)
        or len(field_ids) != len(set(field_ids))
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    async with structure_write_transaction(tenant, connection) as connection:
        ci_type = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_filter(ci_type_ref),
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not ci_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        if ci_type.is_locked:
            raise CustomError(ErrorCode.FORBIDDEN)

        active_fields = (
            await SupplyChainCiFieldDef.filter(
                tenant_id=tenant,
                ci_type_id=ci_type.id,
                deleted=False,
            )
            .using_db(connection)
            .order_by("sort", "ctime")
            .select_for_update()
            .all()
        )
        fields_by_id = {str(field.id): field for field in active_fields}
        if len(field_ids) != len(active_fields) or set(field_ids) != set(fields_by_id):
            raise CustomError(ErrorCode.PARAM_ERROR)

        ordered_fields = [fields_by_id[field_id] for field_id in field_ids]
        for position, field in enumerate(ordered_fields, start=1):
            if field.sort == position:
                continue
            field.sort = position
            field.updater = operator
            await field.save(
                using_db=connection,
                update_fields=["sort", "updater", "utime"],
            )
    return ordered_fields


async def delete_field(
    tenant: str,
    field_id: str,
    *,
    connection=None,
) -> dict:
    async with structure_write_transaction(tenant, connection) as connection:
        candidate = (
            await SupplyChainCiFieldDef.filter(
                id=field_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .first()
        )
        if not candidate:
            raise CustomError(ErrorCode.NOT_FOUND)
        ci_type = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                **_model_ref_filter(_field_model_id(candidate)),
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not ci_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        if ci_type.is_locked:
            raise CustomError(ErrorCode.FORBIDDEN)
        referenced_ci_type_keys = {
            _field_ref_model_id(candidate)
        } - {None, ci_type.id}
        await lock_ci_type_keys_for_structure(
            tenant,
            referenced_ci_type_keys,
            connection,
            include_deleted=True,
            require_all=False,
        )
        field = (
            await SupplyChainCiFieldDef.filter(
                id=field_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not field:
            raise CustomError(ErrorCode.NOT_FOUND)
        if _field_model_id(field) not in {ci_type.id, ci_type.key}:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if field.field_key == ci_type.primary_field:
            raise CustomError(ErrorCode.FORBIDDEN)
        field.deleted = True
        await field.save(using_db=connection, update_fields=["deleted", "utime"])
    return {"id": field.id}


async def set_package_primary_field(
    tenant: str,
    ci_type_key: str,
    primary_field: str | None,
    operator: str | None,
    *,
    connection=None,
) -> SupplyChainCiType:
    ci_type_key = validate_key(ci_type_key, "ci_type_key")
    primary_field = (
        validate_key(primary_field, "primary_field") if primary_field else ""
    )
    async with structure_write_transaction(tenant, connection) as connection:
        ci_type = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                key=ci_type_key,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not ci_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        if ci_type.primary_field == primary_field:
            return ci_type
        if ci_type.is_locked:
            raise CustomError(ErrorCode.FORBIDDEN)
        if primary_field:
            field = (
                await SupplyChainCiFieldDef.filter(
                    tenant_id=tenant,
                    ci_type_key=ci_type_key,
                    field_key=primary_field,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not field or field.data_type != "text":
                raise CustomError(ErrorCode.PARAM_ERROR)
        if ci_type.primary_field != primary_field:
            ci_type.primary_field = primary_field
            ci_type.updater = operator
            await ci_type.save(
                using_db=connection,
                update_fields=["primary_field", "updater", "utime"],
            )
    return ci_type


async def save_relation_type(
    tenant: str,
    data: dict,
    operator: str | None,
    *,
    expected_exists: bool | None = None,
    connection=None,
) -> SupplyChainCiRelationType:
    payload = payload_relation_type(data, operator)
    async with structure_write_transaction(tenant, connection) as connection:
        async def resolve_model_refs(refs: list[str]) -> tuple[list[str], list[str]]:
            if not refs:
                return [], []
            models = await SupplyChainCiType.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_list_filter(refs),
            ).using_db(connection).all()
            by_ref = {value: model for model in models for value in (model.id, model.key)}
            if set(refs) - set(by_ref):
                raise CustomError(ErrorCode.NOT_FOUND)
            return [by_ref[ref].id for ref in refs], [by_ref[ref].key for ref in refs]

        payload["source_ci_type_ids"], payload["source_ci_types"] = await resolve_model_refs(
            payload["source_ci_type_ids"]
        )
        payload["target_ci_type_ids"], payload["target_ci_types"] = await resolve_model_refs(
            payload["target_ci_type_ids"]
        )
        for field in payload["relation_fields"]:
            ref = field.get("ref_ci_type_id")
            if not ref:
                field["ref_ci_type"] = None
                continue
            ref_ids, ref_keys = await resolve_model_refs([ref])
            field["ref_ci_type_id"] = ref_ids[0]
            field["ref_ci_type"] = ref_keys[0]
        if data.get("id"):
            query = SupplyChainCiRelationType.filter(
                Q(id=data["id"]) | Q(key=data["id"]),
                tenant_id=tenant,
                deleted=False,
            )
        elif payload["key"]:
            query = SupplyChainCiRelationType.filter(
                tenant_id=tenant,
                key=payload["key"],
                deleted=False,
            )
        else:
            query = None
        existing = None
        if query is not None:
            existing = (
                await query.using_db(connection)
                .order_by("key")
                .select_for_update()
                .first()
            )
        if data.get("id") and not existing:
            raise CustomError(ErrorCode.NOT_FOUND)
        if existing:
            payload["key"] = existing.key
        elif not payload["key"]:
            payload["key"] = await _allocate_relation_key(
                tenant, payload["name"], connection
            )
        model_ids = set(payload["source_ci_type_ids"]) | set(
            payload["target_ci_type_ids"]
        )
        relation_types = (
            await SupplyChainCiRelationType.filter(tenant_id=tenant, deleted=False)
            .using_db(connection)
            .select_for_update()
            .all()
        )
        for relation_type in relation_types:
            if existing and relation_type.id == existing.id:
                continue
            if _relation_name_overlaps(relation_type, payload["name"], model_ids):
                raise CustomError(ErrorCode.ASSET_RELATION_NAME_EXISTS)
        ensure_expected_existence(existing, expected_exists)
        await ensure_relation_type_structure_unlocked(
            tenant,
            payload,
            existing,
            connection=connection,
        )
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            await existing.save(using_db=connection)
            return existing
        return await SupplyChainCiRelationType.create(
            using_db=connection,
            tenant_id=tenant,
            creator=operator,
            **payload,
        )


async def _save_ci_for_type(
    tenant: str,
    data: dict,
    operator: str | None,
    ci_type: SupplyChainCiType,
    existing: SupplyChainCi | None = None,
    *,
    connection=None,
) -> SupplyChainCi:
    ci_type_key = ci_type.key
    attributes = data.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    await validate_ci_attributes(tenant, ci_type.id, attributes, getattr(existing, "id", None))
    status = data.get("status") or getattr(existing, "status", "active")
    risk_status = data.get("risk_status") or getattr(existing, "risk_status", "normal")
    if status not in CI_STATUSES or risk_status not in RISK_STATUSES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    name_value = data.get("name")
    if name_value in (None, "") and ci_type.primary_field:
        name_value = attributes.get(ci_type.primary_field)
    if name_value in (None, ""):
        name_value = ci_type_key
    name = str(name_value).strip() or ci_type_key
    payload = {
        "ci_type_id": ci_type.id,
        "ci_type_key": ci_type_key,
        "name": name,
        "project_area_id": data.get("project_area_id"),
        "accessory_project_id": data.get("accessory_project_id"),
        "attributes": attributes,
        "status": status,
        "risk_status": risk_status,
        "updater": operator,
    }
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        if connection is None:
            await existing.save()
        else:
            await existing.save(using_db=connection)
        return existing
    return await SupplyChainCi.create(
        using_db=connection,
        tenant_id=tenant,
        creator=operator,
        **payload,
    )


async def save_ci(tenant: str, data: dict, operator: str | None) -> SupplyChainCi:
    data = require_dict(data)
    ci_type_id = data.get("ci_type_id") or data.get("ci_type_key")
    if not ci_type_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data.get("id"):
        ci_type = await get_ci_type_or_404(tenant, ci_type_id)
        async with in_transaction() as connection:
            existing = (
                await SupplyChainCi.filter(
                    id=data["id"],
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not existing:
                raise CustomError(ErrorCode.NOT_FOUND)
            if _ci_model_id(existing) not in {ci_type.id, ci_type.key}:
                raise CustomError(ErrorCode.PARAM_ERROR)
            return await _save_ci_for_type(
                tenant,
                data,
                operator,
                ci_type,
                existing,
                connection=connection,
            )

    # Model deletion takes the same row lock before checking for instances.
    # The lock is model-scoped so unrelated CI creates remain concurrent.
    async with in_transaction() as connection:
        ci_type = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_filter(ci_type_id),
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not ci_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        return await _save_ci_for_type(
            tenant,
            data,
            operator,
            ci_type,
            connection=connection,
        )


async def delete_ci(
    tenant: str,
    ci_id: str,
    *,
    connection=None,
) -> dict:
    """Soft-delete an asset instance and its incident edges atomically.

    Instance lifecycle remains available for locked models. The tenant
    structure lock only serializes the incident-edge cleanup with direct
    relation writes so a concurrent relation cannot survive the deletion.
    """
    async with structure_write_transaction(tenant, connection) as connection:
        ci = (
            await SupplyChainCi.filter(
                id=ci_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not ci:
            raise CustomError(ErrorCode.NOT_FOUND)
        ci.deleted = True
        await ci.save(
            using_db=connection,
            update_fields=["deleted", "utime"],
        )
        await SupplyChainCiRelation.filter(
            Q(source_ci_id=ci.id) | Q(target_ci_id=ci.id),
            tenant_id=tenant,
            deleted=False,
        ).using_db(connection).update(deleted=True)
        return {"id": ci.id}


async def validate_relation_ends(
    tenant: str,
    relation_type: SupplyChainCiRelationType,
    source: SupplyChainCi,
    target: SupplyChainCi,
    current_relation_id: str | None = None,
    *,
    connection=None,
) -> None:
    if _relation_source_model_ids(relation_type) and _ci_model_id(source) not in _relation_source_model_ids(relation_type):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if _relation_target_model_ids(relation_type) and _ci_model_id(target) not in _relation_target_model_ids(relation_type):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if relation_type.cardinality in {"1-1", "1-n"}:
        existing_source_query = SupplyChainCiRelation.filter(
            tenant_id=tenant,
            relation_type_key=relation_type.key,
            source_ci_id=source.id,
            deleted=False,
        )
        if connection is not None:
            existing_source_query = existing_source_query.using_db(connection)
        existing_source = await existing_source_query.first()
        if existing_source and existing_source.id != current_relation_id:
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
    if relation_type.cardinality == "1-1":
        existing_target_query = SupplyChainCiRelation.filter(
            tenant_id=tenant,
            relation_type_key=relation_type.key,
            target_ci_id=target.id,
            deleted=False,
        )
        if connection is not None:
            existing_target_query = existing_target_query.using_db(connection)
        existing_target = await existing_target_query.first()
        if existing_target and existing_target.id != current_relation_id:
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)


async def get_cis_for_structure_update(
    tenant: str,
    ci_ids: Iterable[str],
    connection,
    *,
    include_deleted: bool = False,
) -> dict[str, SupplyChainCi]:
    ids = {ci_id for ci_id in ci_ids if ci_id}
    if not ids:
        raise CustomError(ErrorCode.NOT_FOUND)
    filters = {
        "id__in": sorted(ids),
        "tenant_id": tenant,
    }
    if not include_deleted:
        filters["deleted"] = False
    cis = (
        await SupplyChainCi.filter(**filters)
        .using_db(connection)
        .order_by("id")
        .select_for_update()
        .all()
    )
    ci_by_id = {ci.id: ci for ci in cis}
    if ids - set(ci_by_id):
        raise CustomError(ErrorCode.NOT_FOUND)
    return ci_by_id


async def save_relation(
    tenant: str,
    data: dict,
    operator: str | None,
    *,
    connection=None,
) -> SupplyChainCiRelation:
    data = require_dict(data)
    relation_type_key = validate_key(data.get("relation_type_key"), "relation_type_key")
    source_ci_id = data.get("source_ci_id")
    target_ci_id = data.get("target_ci_id")
    if not source_ci_id or not target_ci_id:
        raise CustomError(ErrorCode.NOT_FOUND)
    if source_ci_id == target_ci_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    attributes = data.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    async with structure_write_transaction(tenant, connection) as connection:
        relation_type = (
            await SupplyChainCiRelationType.filter(
                tenant_id=tenant,
                key=relation_type_key,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not relation_type:
            raise CustomError(ErrorCode.NOT_FOUND)

        if data.get("id"):
            existing = (
                await SupplyChainCiRelation.filter(
                    id=data["id"],
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not existing:
                raise CustomError(ErrorCode.NOT_FOUND)
        else:
            existing = (
                await SupplyChainCiRelation.filter(
                    tenant_id=tenant,
                    relation_type_key=relation_type_key,
                    source_ci_id=source_ci_id,
                    target_ci_id=target_ci_id,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )

        ci_ids = {source_ci_id, target_ci_id}
        if data.get("id"):
            ci_ids.update({existing.source_ci_id, existing.target_ci_id})
        ci_by_id = await get_cis_for_structure_update(
            tenant,
            ci_ids,
            connection,
        )
        source = ci_by_id[source_ci_id]
        target = ci_by_id[target_ci_id]

        if data.get("id"):
            duplicate = (
                await SupplyChainCiRelation.filter(
                    tenant_id=tenant,
                    relation_type_key=relation_type_key,
                    source_ci_id=source.id,
                    target_ci_id=target.id,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if duplicate and duplicate.id != existing.id:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)

        affected_ci_type_keys = {
            _ci_model_id(ci) for ci in ci_by_id.values()
        }
        await lock_ci_type_keys_for_structure(
            tenant,
            affected_ci_type_keys,
            connection,
        )
        await validate_dynamic_attributes(
            tenant,
            relation_field_defs(relation_type),
            attributes,
        )
        await validate_relation_ends(
            tenant,
            relation_type,
            source,
            target,
            getattr(existing, "id", None),
            connection=connection,
        )
        if not existing:
            return await SupplyChainCiRelation.create(
                using_db=connection,
                tenant_id=tenant,
                relation_type_key=relation_type_key,
                source_ci=source,
                target_ci=target,
                attributes=attributes,
                creator=operator,
                updater=operator,
            )
        existing.relation_type_key = relation_type_key
        existing.source_ci = source
        existing.target_ci = target
        existing.attributes = attributes
        existing.updater = operator
        await existing.save(using_db=connection)
        return existing


def _relation_type_model_keys(relation_type: SupplyChainCiRelationType) -> set[str]:
    keys = set(_relation_source_model_ids(relation_type)) | set(
        _relation_target_model_ids(relation_type)
    )
    for field in relation_type.relation_fields or []:
        if isinstance(field, dict):
            ref = field.get("ref_ci_type_id") or field.get("ref_ci_type")
            if ref:
                keys.add(ref)
    return keys


def _relation_type_payload_model_keys(payload: dict) -> set[str]:
    keys = set(payload.get("source_ci_type_ids") or []) | set(
        payload.get("target_ci_type_ids") or []
    )
    for field in payload.get("relation_fields") or []:
        if isinstance(field, dict):
            ref = field.get("ref_ci_type_id") or field.get("ref_ci_type")
            if ref:
                keys.add(ref)
    return keys


def _relation_type_has_unrestricted_end(relation_type) -> bool:
    if isinstance(relation_type, dict):
        source_ci_types = _relation_source_model_ids(relation_type)
        target_ci_types = _relation_target_model_ids(relation_type)
    else:
        source_ci_types = _relation_source_model_ids(relation_type)
        target_ci_types = _relation_target_model_ids(relation_type)
    return not source_ci_types or not target_ci_types


def _relation_type_references_ci_type(relation_type, ci_type_id: str) -> bool:
    return ci_type_id in _relation_type_model_keys(relation_type)


async def ensure_relation_type_structure_unlocked(
    tenant: str,
    *relation_types,
    connection,
) -> None:
    model_keys = set()
    include_all = False
    for relation_type in relation_types:
        if relation_type is None:
            continue
        if isinstance(relation_type, dict):
            model_keys.update(_relation_type_payload_model_keys(relation_type))
        else:
            model_keys.update(_relation_type_model_keys(relation_type))
        include_all = include_all or _relation_type_has_unrestricted_end(
            relation_type
        )
    await lock_ci_type_keys_for_structure(
        tenant,
        model_keys,
        connection,
        include_all=include_all,
    )


async def delete_ci_type(
    tenant: str,
    key_or_id: str,
    *,
    connection=None,
) -> dict:
    async with structure_write_transaction(tenant, connection) as connection:
        ci_type = (
            await SupplyChainCiType.filter(
                Q(id=key_or_id) | Q(key=key_or_id),
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .order_by("key")
            .select_for_update()
            .first()
        )
        if not ci_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        if ci_type.is_locked:
            raise CustomError(ErrorCode.FORBIDDEN)
        active_ci = (
            await SupplyChainCi.filter(
                tenant_id=tenant,
                ci_type_id=ci_type.id,
            )
            .using_db(connection)
            .order_by("id")
            .select_for_update()
            .first()
        )
        if active_ci:
            raise CustomError(ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES)
        relation_types = (
            await SupplyChainCiRelationType.filter(
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .order_by("key")
            .select_for_update()
            .all()
        )
        related_relation_types = [
            relation_type
            for relation_type in relation_types
            if _relation_type_references_ci_type(relation_type, ci_type.id)
            or _relation_type_references_ci_type(relation_type, ci_type.key)
        ]
        if related_relation_types:
            raise CustomError(ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES)
        referencing_fields = (
            await SupplyChainCiFieldDef.filter(
                tenant_id=tenant,
                deleted=False,
                ref_ci_type_id=ci_type.id,
            )
            .using_db(connection)
            .order_by("ci_type_key", "field_key")
            .select_for_update()
            .all()
        )
        if any(_field_model_id(field) not in {ci_type.id, ci_type.key} for field in referencing_fields):
            raise CustomError(ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES)
        # Deleted field definitions remain as tombstones. Detach their inbound
        # references before the target model is hard-deleted so the FK can be
        # released without discarding the field history.
        await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            deleted=True,
            ref_ci_type_id=ci_type.id,
        ).using_db(connection).update(
            ref_ci_type_id=None,
            ref_ci_type=None,
        )
        # Empty models have no historical identity to retain. Remove their own
        # definitions first, then hard-delete the model so names can be reused.
        await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            ci_type_id=ci_type.id,
        ).using_db(connection).delete()
        await ci_type.delete(using_db=connection)
        return {"id": ci_type.id}


async def delete_relation_type(
    tenant: str,
    key_or_id: str,
    *,
    connection=None,
) -> dict:
    async with structure_write_transaction(tenant, connection) as connection:
        relation_type = (
            await SupplyChainCiRelationType.filter(
                Q(id=key_or_id) | Q(key=key_or_id),
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .order_by("key")
            .select_for_update()
            .first()
        )
        if not relation_type:
            raise CustomError(ErrorCode.NOT_FOUND)
        if relation_type.is_locked:
            raise CustomError(ErrorCode.FORBIDDEN)
        await ensure_relation_type_structure_unlocked(
            tenant,
            relation_type,
            connection=connection,
        )
        relation_type.deleted = True
        await relation_type.save(
            using_db=connection,
            update_fields=["deleted", "utime"],
        )
        return {"id": relation_type.id}


async def delete_relation(
    tenant: str,
    relation_id: str,
    *,
    connection=None,
) -> dict:
    async with structure_write_transaction(tenant, connection) as connection:
        relation = (
            await SupplyChainCiRelation.filter(
                id=relation_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not relation:
            raise CustomError(ErrorCode.NOT_FOUND)
        ci_by_id = await get_cis_for_structure_update(
            tenant,
            {relation.source_ci_id, relation.target_ci_id},
            connection,
            include_deleted=True,
        )
        await lock_ci_type_keys_for_structure(
            tenant,
            {_ci_model_id(ci) for ci in ci_by_id.values()},
            connection,
            include_deleted=True,
            require_all=False,
        )
        relation.deleted = True
        await relation.save(
            using_db=connection,
            update_fields=["deleted", "utime"],
        )
        return {"id": relation.id}


async def export_model_package(
    tenant: str,
    ci_type_keys: Iterable[str] | None = None,
    include_related: bool = False,
    include_relations: bool = False,
) -> dict:
    requested_refs = list(dict.fromkeys(ci_type_keys or []))
    requested_models = []
    if requested_refs:
        requested_models = await SupplyChainCiType.filter(
            Q(id__in=requested_refs) | Q(key__in=requested_refs),
            tenant_id=tenant,
            deleted=False,
        ).all()
    requested_keys = [model.key for model in requested_models]
    keys = requested_keys[:]
    relation_types = await SupplyChainCiRelationType.filter(tenant_id=tenant, deleted=False).order_by("key").all()

    if requested_refs:
        relation_types = [
            item for item in relation_types
            if set(item.source_ci_types or []).intersection(requested_keys)
            or set(item.target_ci_types or []).intersection(requested_keys)
        ]
        if include_related:
            related_keys = set(requested_keys)
            for relation_type in relation_types:
                related_keys.update(relation_type.source_ci_types or [])
                related_keys.update(relation_type.target_ci_types or [])
            keys = sorted(related_keys)
        elif not include_relations:
            relation_types = []

    condition = Q(tenant_id=tenant, deleted=False)
    if keys:
        condition &= Q(key__in=keys)
    ci_types = await SupplyChainCiType.filter(condition).order_by("sort", "key").all()
    keys = [item.key for item in ci_types]
    if include_relations and not include_related:
        exported_key_set = set(keys)
        relation_types = [
            item for item in relation_types
            if _relation_type_model_keys(item).issubset(exported_key_set)
        ]
    fields = await SupplyChainCiFieldDef.filter(tenant_id=tenant, ci_type_key__in=keys, deleted=False).order_by("ci_type_key", "sort").all()
    return {
        "version": 1,
        "ci_types": [item.json for item in ci_types],
        "fields": [item.json for item in fields],
        "relation_types": [item.json for item in relation_types],
    }


MODEL_PACKAGE_IDENTITY_FIELDS = {
    "id",
    "tenant_id",
    "ctime",
    "utime",
    "creator",
    "updater",
    "deleted",
    # Runtime model UUIDs are local to one installation. Portable packages
    # keep using their hidden key references and remap them on import.
    "ci_type_id",
    "ref_ci_type_id",
    "source_ci_type_ids",
    "target_ci_type_ids",
}


def normalize_model_package(package: dict) -> dict:
    package = require_dict(package)

    def normalized_items(*keys: str, excluded: set[str] | None = None) -> list[dict]:
        source = []
        for key in keys:
            if package.get(key):
                source = package[key]
                break
        if not isinstance(source, list):
            raise CustomError(ErrorCode.PARAM_ERROR)
        excluded_fields = MODEL_PACKAGE_IDENTITY_FIELDS | (excluded or set())
        items = []
        for item in source:
            if not isinstance(item, dict):
                raise CustomError(ErrorCode.PARAM_ERROR)
            items.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in excluded_fields
                }
            )
        return items

    ci_types = normalized_items("ci_types", "models")
    for item in ci_types:
        item["key"] = validate_key(item.get("key"))
        if "primary_field" in item:
            item["primary_field"] = (
                validate_key(item.get("primary_field"), "primary_field")
                if item.get("primary_field")
                else ""
            )

    fields = normalized_items(
        "fields",
        "ci_fields",
        excluded={"is_primary"},
    )
    for item in fields:
        item["ci_type_key"] = validate_key(item.get("ci_type_key"), "ci_type_key")
        item["field_key"] = validate_key(item.get("field_key"), "field_key")

    relation_types = normalized_items("relation_types")
    for item in relation_types:
        item["key"] = validate_key(item.get("key"))
        for field in item.get("relation_fields") or []:
            if isinstance(field, dict):
                field.pop("ref_ci_type_id", None)

    return {
        "ci_types": ci_types,
        "fields": fields,
        "relation_types": relation_types,
    }


async def import_model_package(
    tenant: str,
    package: dict,
    operator: str | None,
    expected_existing: dict[str, set] | None = None,
) -> dict:
    async with structure_write_transaction(tenant) as connection:
        return await _import_model_package_locked(
            tenant,
            package,
            operator,
            expected_existing,
            connection=connection,
        )


async def _import_model_package_locked(
    tenant: str,
    package: dict,
    operator: str | None,
    expected_existing: dict[str, set] | None = None,
    *,
    connection,
) -> dict:
    created_or_updated = {"ci_types": 0, "fields": 0, "relation_types": 0}
    normalized_package = normalize_model_package(package)
    ci_types = normalized_package["ci_types"]
    fields = normalized_package["fields"]
    relation_types = normalized_package["relation_types"]

    packaged_fields = {
        (
            validate_key(item.get("ci_type_key"), "ci_type_key"),
            validate_key(item.get("field_key"), "field_key"),
        ): item
        for item in fields
    }
    for item in ci_types:
        primary_field = item.get("primary_field")
        if not primary_field:
            continue
        ci_type_key = validate_key(item.get("key"))
        primary_field = validate_key(primary_field, "primary_field")
        packaged_primary = packaged_fields.get((ci_type_key, primary_field))
        if packaged_primary:
            if packaged_primary.get("data_type") != "text":
                raise CustomError(ErrorCode.PARAM_ERROR)
            continue
        existing_field = (
            await SupplyChainCiFieldDef.filter(
                tenant_id=tenant,
                ci_type_key=ci_type_key,
                field_key=primary_field,
                deleted=False,
            )
            .using_db(connection)
            .first()
        )
        if not existing_field or existing_field.data_type != "text":
            raise CustomError(ErrorCode.PARAM_ERROR)

    def expected(resource: str, identity) -> bool | None:
        if expected_existing is None:
            return None
        return identity in expected_existing.get(resource, set())

    lock_after_import = set()
    for item in ci_types:
        item_to_save = item
        if bool(item.get("is_locked", False)):
            lock_after_import.add(item["key"])
            item_to_save = {**item, "is_locked": False}
        await save_ci_type(
            tenant,
            item_to_save,
            operator,
            expected_exists=expected("ci_types", item["key"]),
            allow_unlock=False,
            allow_sort_update=True,
            connection=connection,
        )
        created_or_updated["ci_types"] += 1

    pending_fields = {
        (item.get("ci_type_key"), item.get("field_key")): item for item in fields
    }
    for item in ci_types:
        if "primary_field" not in item:
            continue
        ci_type_key = item.get("key")
        primary_field = item.get("primary_field")
        primary_item = pending_fields.pop((ci_type_key, primary_field), None)
        if primary_item:
            await save_field(
                tenant,
                primary_item,
                operator,
                expected_exists=expected(
                    "fields",
                    (primary_item["ci_type_key"], primary_item["field_key"]),
                ),
                connection=connection,
            )
            created_or_updated["fields"] += 1
        await set_package_primary_field(
            tenant,
            ci_type_key,
            primary_field,
            operator,
            connection=connection,
        )

    for item in pending_fields.values():
        await save_field(
            tenant,
            item,
            operator,
            expected_exists=expected(
                "fields",
                (item["ci_type_key"], item["field_key"]),
            ),
            connection=connection,
        )
        created_or_updated["fields"] += 1
    for item in relation_types:
        await save_relation_type(
            tenant,
            item,
            operator,
            expected_exists=expected("relation_types", item["key"]),
            connection=connection,
        )
        created_or_updated["relation_types"] += 1

    if lock_after_import:
        imported_ci_types = (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                key__in=sorted(lock_after_import),
                deleted=False,
            )
            .using_db(connection)
            .order_by("key")
            .select_for_update()
            .all()
        )
        if lock_after_import - {item.key for item in imported_ci_types}:
            raise CustomError(ErrorCode.NOT_FOUND)
        for ci_type in imported_ci_types:
            ci_type.is_locked = True
            ci_type.updater = operator
            await ci_type.save(
                using_db=connection,
                update_fields=["is_locked", "updater", "utime"],
            )
    return created_or_updated


async def build_relation_graph(tenant: str, root_ci_id: str | None = None, depth: int = 1) -> dict:
    depth = max(1, min(3, int(depth or 1)))
    visited: dict[str, SupplyChainCi] = {}
    edges: list[SupplyChainCiRelation] = []
    frontier = {root_ci_id} if root_ci_id else set()
    if not frontier:
        roots = await SupplyChainCi.filter(tenant_id=tenant, deleted=False).order_by("-utime").limit(50).all()
        visited.update({item.id: item for item in roots})
        frontier = {item.id for item in roots}
    for _ in range(depth):
        if not frontier:
            break
        related = await SupplyChainCiRelation.filter(
            Q(source_ci_id__in=list(frontier)) | Q(target_ci_id__in=list(frontier)),
            tenant_id=tenant,
            deleted=False,
        ).all()
        next_frontier = set()
        edges.extend(related)
        for relation in related:
            next_frontier.add(relation.source_ci_id)
            next_frontier.add(relation.target_ci_id)
        frontier = next_frontier - set(visited.keys())
        cis = await SupplyChainCi.filter(id__in=list(next_frontier), tenant_id=tenant, deleted=False).all()
        visited.update({item.id: item for item in cis})
    if root_ci_id and root_ci_id not in visited:
        root = await get_ci_or_404(tenant, root_ci_id)
        visited[root.id] = root
    return {
        "nodes": [
            {
                "id": item.id,
                "text": item.name,
                "type": item.ci_type_key,
                "properties": item.json,
            }
            for item in visited.values()
        ],
        "edges": [
            {
                "id": item.id,
                "sourceNodeId": item.source_ci_id,
                "targetNodeId": item.target_ci_id,
                "text": item.relation_type_key,
                "properties": item.json,
            }
            for item in edges
        ],
    }


def ledger_payload(domain: str, data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    config = ledger_config(domain)
    payload = {}
    for field in config["fields"]:
        if field in data:
            payload[field] = data[field]
    for field in config["required"]:
        if not payload.get(field):
            raise CustomError(ErrorCode.PARAM_ERROR)
    for field, allowed in config.get("enums", {}).items():
        value = payload.get(field)
        if value and value not in allowed:
            raise CustomError(ErrorCode.PARAM_ERROR)
    if "detail" in payload and not isinstance(payload["detail"], dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if "evidence_attachment_ids" in payload and not isinstance(payload["evidence_attachment_ids"], list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    payload["updater"] = operator
    return payload


async def list_ledger_records(tenant: str, domain: str, data: dict) -> dict:
    config = ledger_config(domain)
    page, size, offset = normalize_page(data)
    condition = await ledger_scope_condition(tenant, config, data)
    for field in LEDGER_FILTER_FIELDS:
        if field not in config["fields"]:
            continue
        if data.get(field) not in (None, ""):
            condition &= Q(**{field: data[field]})
    keyword = data.get("keyword")
    keyword_fields = config.get("keyword", [])
    if keyword and keyword_fields:
        keyword_condition = Q()
        for field in keyword_fields:
            keyword_condition |= Q(**{f"{field}__icontains": keyword})
        condition &= keyword_condition
    queryset = config["model"].filter(condition)
    total = await queryset.count()
    items = await queryset.order_by(*config.get("order", ["-utime"])).offset(offset).limit(size).all()
    return {"total": total, "items": [item.json for item in items], "page": page, "size": size}


async def accessory_project_ids_for_area(tenant: str, project_area_id: str) -> list[str]:
    relations = await SupplyChainCiRelation.filter(
        tenant_id=tenant,
        deleted=False,
        relation_type_key="area_contains_project",
        source_ci_id=project_area_id,
    ).all()
    return [item.target_ci_id for item in relations]


async def ledger_scope_condition(tenant: str, config: dict, data: dict) -> Q:
    condition = Q(tenant_id=tenant, deleted=False)
    if data.get("accessory_project_id") and "accessory_project_id" in config["fields"]:
        return condition & Q(accessory_project_id=data["accessory_project_id"])
    if not data.get("project_area_id"):
        return condition
    if "project_area_id" in config["fields"]:
        return condition & Q(project_area_id=data["project_area_id"])
    if "accessory_project_id" in config["fields"]:
        project_ids = await accessory_project_ids_for_area(tenant, data["project_area_id"])
        return condition & Q(accessory_project_id__in=project_ids or ["__none__"])
    return condition


async def get_ledger_record_or_404(tenant: str, domain: str, record_id: str):
    record = await ledger_config(domain)["model"].filter(id=record_id, tenant_id=tenant, deleted=False).first()
    if not record:
        raise CustomError(ErrorCode.NOT_FOUND)
    return record


async def save_ledger_record(tenant: str, domain: str, data: dict, operator: str | None):
    config = ledger_config(domain)
    payload = ledger_payload(domain, data, operator)
    existing = None
    if data.get("id"):
        existing = await get_ledger_record_or_404(tenant, domain, data["id"])
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        await existing.save()
        return existing
    return await config["model"].create(tenant_id=tenant, creator=operator, **payload)


async def delete_ledger_record(tenant: str, domain: str, record_id: str) -> dict:
    record = await get_ledger_record_or_404(tenant, domain, record_id)
    record.deleted = True
    await record.save()
    return {"id": record.id}


async def build_project_tree(tenant: str) -> dict:
    areas = await SupplyChainCi.filter(tenant_id=tenant, deleted=False, ci_type_key="project_area").order_by("name").all()
    projects = await SupplyChainCi.filter(tenant_id=tenant, deleted=False, ci_type_key="accessory_project").order_by("name").all()
    relations = await SupplyChainCiRelation.filter(
        tenant_id=tenant,
        deleted=False,
        relation_type_key="area_contains_project",
        source_ci_id__in=[item.id for item in areas] or ["__none__"],
    ).all()
    projects_by_id = {item.id: item for item in projects}
    children_by_area: dict[str, list[SupplyChainCi]] = {item.id: [] for item in areas}
    attached_project_ids = set()
    for relation in relations:
        project = projects_by_id.get(relation.target_ci_id)
        if project:
            children_by_area.setdefault(relation.source_ci_id, []).append(project)
            attached_project_ids.add(project.id)
    unassigned = [item for item in projects if item.id not in attached_project_ids]
    tree = [
        {
            **area.json,
            "children": [project.json for project in children_by_area.get(area.id, [])],
        }
        for area in areas
    ]
    if unassigned:
        tree.append({
            "id": "__unassigned__",
            "name": "未关联配件项目",
            "ci_type_key": "virtual",
            "children": [project.json for project in unassigned],
        })
    return {"items": tree, "total": len(tree)}


async def overview_summary(tenant: str, data: dict) -> dict:
    auth_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["personnel-authorization"], data)
    nda_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["nda-record"], data)
    unknown_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["unknown-terminal"], data)
    boundary_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["boundary-log"], data)
    exception_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["exception-device-log"], data)
    disposal_scope = await process_scope_condition(tenant, PROCESS_CONFIGS["disposal-task"], data)
    review_issue_scope = await process_scope_condition(tenant, PROCESS_CONFIGS["review-issue"], data)
    collection_run_scope = await process_scope_condition(tenant, PROCESS_CONFIGS["collection-run"], data)
    project_count = await SupplyChainCi.filter(tenant_id=tenant, deleted=False, ci_type_key="accessory_project").count()
    area_count = await SupplyChainCi.filter(tenant_id=tenant, deleted=False, ci_type_key="project_area").count()
    nda_unsigned = await SupplyChainNdaRecord.filter(nda_scope & Q(sign_status="unsigned")).count()
    nda_expired = await SupplyChainNdaRecord.filter(nda_scope & Q(sign_status="expired")).count()
    authorization_abnormal = await SupplyChainProjectAuthorization.filter(auth_scope & Q(access_control_status="abnormal")).count()
    unknown_pending = await SupplyChainUnknownTerminal.filter(unknown_scope & Q(status="pending")).count()
    boundary_risk = await SupplyChainBoundaryLog.filter(boundary_scope & Q(risk_flag=True)).count()
    exception_abnormal = await SupplyChainExceptionDeviceLog.filter(exception_scope & ~Q(abnormal_type="none")).count()
    disposal_pending = await SupplyChainDisposalTask.filter(disposal_scope & Q(status="pending_confirm")).count()
    issue_pending_review = await SupplyChainReviewIssue.filter(review_issue_scope & Q(status="pending_review")).count()
    collection_failed = await SupplyChainCollectionRun.filter(collection_run_scope & Q(status="failed")).count()
    return {
        "area_count": area_count,
        "accessory_project_count": project_count,
        "risk_counts": {
            "nda_unsigned": nda_unsigned,
            "nda_expired": nda_expired,
            "authorization_abnormal": authorization_abnormal,
            "unknown_terminal_pending": unknown_pending,
            "boundary_risk": boundary_risk,
            "exception_device_abnormal": exception_abnormal,
            "disposal_task_pending_confirm": disposal_pending,
            "review_issue_pending_review": issue_pending_review,
            "collection_run_failed": collection_failed,
        },
    }


async def overview_todo(tenant: str, data: dict) -> dict:
    auth_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["personnel-authorization"], data)
    nda_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["nda-record"], data)
    unknown_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["unknown-terminal"], data)
    boundary_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["boundary-log"], data)
    exception_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["exception-device-log"], data)
    disposal_scope = await process_scope_condition(tenant, PROCESS_CONFIGS["disposal-task"], data)
    review_issue_scope = await process_scope_condition(tenant, PROCESS_CONFIGS["review-issue"], data)
    collection_run_scope = await process_scope_condition(tenant, PROCESS_CONFIGS["collection-run"], data)
    items = [
        {"type": "nda_unsigned", "title": "NDA 未签署", "total": await SupplyChainNdaRecord.filter(nda_scope & Q(sign_status="unsigned")).count()},
        {"type": "nda_expired", "title": "NDA 已过期", "total": await SupplyChainNdaRecord.filter(nda_scope & Q(sign_status="expired")).count()},
        {"type": "authorization_abnormal", "title": "门禁授权异常", "total": await SupplyChainProjectAuthorization.filter(auth_scope & Q(access_control_status="abnormal")).count()},
        {"type": "disposal_task_pending_confirm", "title": "待确认权限回收", "total": await SupplyChainDisposalTask.filter(disposal_scope & Q(status="pending_confirm")).count()},
        {"type": "review_issue_pending_review", "title": "待复核问题", "total": await SupplyChainReviewIssue.filter(review_issue_scope & Q(status="pending_review")).count()},
        {"type": "unknown_terminal_pending", "title": "待确认未知终端", "total": await SupplyChainUnknownTerminal.filter(unknown_scope & Q(status="pending")).count()},
        {"type": "collection_run_failed", "title": "采集失败", "total": await SupplyChainCollectionRun.filter(collection_run_scope & Q(status="failed")).count()},
        {"type": "boundary_risk", "title": "边界风险日志", "total": await SupplyChainBoundaryLog.filter(boundary_scope & Q(risk_flag=True)).count()},
        {"type": "exception_device_abnormal", "title": "例外设备异常", "total": await SupplyChainExceptionDeviceLog.filter(exception_scope & ~Q(abnormal_type="none")).count()},
    ]
    return {"items": items, "total": sum(item["total"] for item in items)}


def process_payload(domain: str, data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    config = process_config(domain)
    payload = {}
    for field in config["fields"]:
        if field in data:
            payload[field] = data[field]
    for field in config["required"]:
        if not payload.get(field):
            raise CustomError(ErrorCode.PARAM_ERROR)
    for field, allowed in config.get("enums", {}).items():
        value = payload.get(field)
        if value and value not in allowed:
            raise CustomError(ErrorCode.PARAM_ERROR)
    for field in [
        "input_params",
        "parser_rules",
        "mapping_rules",
        "graph_data",
        "policy_data",
        "before_data",
        "after_data",
    ]:
        if field in payload and not isinstance(payload[field], dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
    for field in ["target_ci_ids", "actions", "related_ci_ids", "attachment_ids", "receivers", "receiver_roles"]:
        if field in payload and not isinstance(payload[field], list):
            raise CustomError(ErrorCode.PARAM_ERROR)
    for action in payload.get("actions") or []:
        if isinstance(action, str) and action not in DISPOSAL_ACTIONS:
            raise CustomError(ErrorCode.PARAM_ERROR)
    if "input_mapping" in payload and not isinstance(payload["input_mapping"], dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if domain == "disposal-rule":
        payload["execution_mode"] = "manual_confirm"
    if domain == "disposal-task":
        payload["status"] = "pending_confirm"
    if domain == "review-issue" and not payload.get("issue_no"):
        payload["issue_no"] = f"SCR-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    payload["updater"] = operator
    return payload


async def process_scope_condition(tenant: str, config: dict, data: dict) -> Q:
    condition = Q(tenant_id=tenant, deleted=False)
    if data.get("accessory_project_id") and "accessory_project_id" in config["fields"]:
        condition &= Q(accessory_project_id=data["accessory_project_id"])
    elif data.get("project_area_id"):
        area_condition = Q()
        if "project_area_id" in config["fields"]:
            area_condition |= Q(project_area_id=data["project_area_id"])
        if "accessory_project_id" in config["fields"]:
            project_ids = await accessory_project_ids_for_area(tenant, data["project_area_id"])
            area_condition |= Q(accessory_project_id__in=project_ids or ["__none__"])
        condition &= area_condition
    return condition


async def list_process_records(tenant: str, domain: str, data: dict) -> dict:
    config = process_config(domain)
    page, size, offset = normalize_page(data)
    condition = await process_scope_condition(tenant, config, data)
    for field in PROCESS_FILTER_FIELDS:
        if field in config["fields"] and data.get(field) not in (None, ""):
            condition &= Q(**{field: data[field]})
    keyword = data.get("keyword")
    keyword_fields = config.get("keyword", [])
    if keyword and keyword_fields:
        keyword_condition = Q()
        for field in keyword_fields:
            keyword_condition |= Q(**{f"{field}__icontains": keyword})
        condition &= keyword_condition
    queryset = config["model"].filter(condition)
    total = await queryset.count()
    items = await queryset.order_by(*config.get("order", ["-utime"])).offset(offset).limit(size).all()
    return {"total": total, "items": [item.json for item in items], "page": page, "size": size}


async def get_process_record_or_404(tenant: str, domain: str, record_id: str):
    record = await process_config(domain)["model"].filter(id=record_id, tenant_id=tenant, deleted=False).first()
    if not record:
        raise CustomError(ErrorCode.NOT_FOUND)
    return record


async def save_process_record(tenant: str, domain: str, data: dict, operator: str | None):
    config = process_config(domain)
    payload = process_payload(domain, data, operator)
    existing = None
    if data.get("id"):
        existing = await get_process_record_or_404(tenant, domain, data["id"])
        if domain == "disposal-task":
            payload.pop("status", None)
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        await existing.save()
        return existing
    return await config["model"].create(tenant_id=tenant, creator=operator, **payload)


async def save_review_issue(tenant: str, data: dict, operator: str | None):
    return await save_process_record(tenant, "review-issue", data, operator)


async def delete_process_record(tenant: str, domain: str, record_id: str) -> dict:
    record = await get_process_record_or_404(tenant, domain, record_id)
    record.deleted = True
    await record.save()
    return {"id": record.id}


def _policy_text(value, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        raise CustomError(ErrorCode.PARAM_ERROR)
    text = str(value).strip()
    if len(text) > maximum:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return text


def _normalize_actual_policy_payload(
    data: dict,
    operator: str | None,
    *,
    defaults: dict | None = None,
) -> dict:
    data = require_dict(data)
    defaults = defaults or {}

    def first(*keys: str):
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        for key in keys:
            value = defaults.get(key)
            if value not in (None, ""):
                return value
        return None

    device_key = _policy_text(
        first("device_key", "device_id", "device_ci_id"), "device_key", 255
    )
    policy_key = _policy_text(
        first("policy_key", "policy_id", "raw_rule_id", "rule_name", "name"),
        "policy_key",
        255,
    )
    if not device_key or not policy_key:
        raise CustomError(ErrorCode.PARAM_ERROR)

    normalized = {**defaults, **data}
    normalized["device_key"] = device_key
    normalized["policy_key"] = policy_key
    normalized["raw_rule_id"] = _policy_text(
        first("raw_rule_id", "policy_id", "policy_key") or policy_key,
        "raw_rule_id",
        255,
    )
    normalized["rule_name"] = _policy_text(
        first("rule_name", "name", "policy_key") or policy_key,
        "rule_name",
        255,
    )
    normalized["device_name"] = _policy_text(
        first("device_name"), "device_name", 255
    ) or None
    normalized["device_type"] = _policy_text(
        first("device_type"), "device_type", 30
    ).lower() or None
    normalized["policy_type"] = _policy_text(
        first("policy_type"), "policy_type", 50
    ).lower() or None

    action = _policy_text(first("action") or "permit", "action", 20).lower()
    action_aliases = {
        "allow": "permit",
        "accept": "permit",
        "pass": "permit",
        "block": "deny",
        "drop": "deny",
        "reject": "deny",
    }
    normalized["action"] = action_aliases.get(action, action)
    if normalized["action"] not in POLICY_ACTIONS:
        raise CustomError(ErrorCode.PARAM_ERROR)

    enabled = first("enabled")
    if enabled is None:
        enabled = True
    if not isinstance(enabled, bool):
        raise CustomError(ErrorCode.PARAM_ERROR)
    normalized["enabled"] = enabled

    if "policy_data" not in data:
        if "policy_data" in defaults:
            normalized["policy_data"] = defaults["policy_data"]
        else:
            normalized["policy_data"] = {
                key: value
                for key, value in data.items()
                if key
                not in {
                    "id",
                    "revision",
                    "batch_id",
                    "replace",
                    "sync_source",
                }
            }
    elif not isinstance(data["policy_data"], dict):
        raise CustomError(ErrorCode.PARAM_ERROR)

    payload = process_payload("actual-policy", normalized, operator)
    payload["device_key"] = device_key
    payload["policy_key"] = policy_key
    return payload


def _policy_snapshot(record: SupplyChainActualPolicy) -> dict:
    payload = record.json
    return {
        key: payload.get(key)
        for key in (
            "id",
            "accessory_project_id",
            "device_ci_id",
            "device_key",
            "device_name",
            "device_type",
            "collection_run_id",
            "raw_rule_id",
            "policy_key",
            "policy_type",
            "rule_name",
            "source_zone",
            "source_address",
            "target_zone",
            "target_address",
            "protocol",
            "port",
            "action",
            "enabled",
            "raw_text",
            "policy_data",
            "parse_status",
            "revision",
            "last_synced_at",
        )
    }


def _policy_content(snapshot: dict) -> dict:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"id", "revision", "last_synced_at"}
    }


def _policy_diff_text(before_data: dict, after_data: dict) -> str:
    keys = sorted(set(before_data) | set(after_data))
    changed = [key for key in keys if before_data.get(key) != after_data.get(key)]
    return orjson.dumps(
        {"changed_fields": changed}, option=orjson.OPT_SORT_KEYS
    ).decode("utf-8")


async def _append_policy_history(
    connection,
    *,
    tenant: str,
    record: SupplyChainActualPolicy,
    change_type: str,
    before_data: dict,
    after_data: dict,
    batch_id: str,
    sync_source: str,
    operator: str | None,
    detected_at: datetime.datetime,
) -> SupplyChainPolicyChange:
    return await SupplyChainPolicyChange.create(
        using_db=connection,
        tenant_id=tenant,
        accessory_project_id=record.accessory_project_id,
        device_ci_id=record.device_ci_id,
        device_key=record.device_key,
        actual_policy_id=record.id,
        policy_key=record.policy_key,
        batch_id=batch_id,
        policy_name=record.rule_name,
        change_type=change_type,
        revision=record.revision,
        before_data=before_data,
        after_data=after_data,
        sync_source=sync_source,
        diff_text=_policy_diff_text(before_data, after_data),
        detected_at=detected_at,
        risk_level="medium",
        creator=operator,
        updater=operator,
    )


async def _locked_actual_policy(connection, tenant: str, record_id: str):
    return await (
        SupplyChainActualPolicy.filter(
            id=record_id, tenant_id=tenant, deleted=False
        )
        .using_db(connection)
        .select_for_update()
        .first()
    )


async def save_actual_policy(
    tenant: str, data: dict, operator: str | None
) -> SupplyChainActualPolicy:
    data = require_dict(data)
    record_id = _policy_text(data.get("id"), "id", 36)
    now = datetime.datetime.now(datetime.UTC)
    batch_id = _policy_text(data.get("batch_id"), "batch_id", 64) or str(
        uuid.uuid4()
    )
    sync_source = _policy_text(
        data.get("sync_source") or "manual", "sync_source", 100
    )

    async with in_transaction() as connection:
        existing = None
        before_data = {}
        defaults = {}
        if record_id:
            existing = await _locked_actual_policy(connection, tenant, record_id)
            if not existing:
                raise CustomError(ErrorCode.NOT_FOUND)
            if data.get("revision") is not None:
                try:
                    expected_revision = int(data["revision"])
                except (TypeError, ValueError) as exc:
                    raise CustomError(ErrorCode.PARAM_ERROR) from exc
                if expected_revision != existing.revision:
                    raise CustomError(ErrorCode.PARAM_ERROR)
            before_data = _policy_snapshot(existing)
            defaults = before_data

        payload = _normalize_actual_policy_payload(
            data, operator, defaults=defaults
        )
        duplicate_query = SupplyChainActualPolicy.filter(
            tenant_id=tenant,
            device_key=payload["device_key"],
            policy_key=payload["policy_key"],
            deleted=False,
        ).using_db(connection)
        if existing:
            duplicate_query = duplicate_query.exclude(id=existing.id)
        if await duplicate_query.exists():
            raise CustomError(ErrorCode.PARAM_ERROR)

        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            existing.revision += 1
            existing.last_synced_at = now
            await existing.save(using_db=connection)
            record = existing
            change_type = "modified"
        else:
            record = await SupplyChainActualPolicy.create(
                using_db=connection,
                tenant_id=tenant,
                creator=operator,
                revision=1,
                last_synced_at=now,
                **payload,
            )
            change_type = "added"

        after_data = _policy_snapshot(record)
        await _append_policy_history(
            connection,
            tenant=tenant,
            record=record,
            change_type=change_type,
            before_data=before_data,
            after_data=after_data,
            batch_id=batch_id,
            sync_source=sync_source,
            operator=operator,
            detected_at=now,
        )
    return record


async def delete_actual_policy(
    tenant: str, record_id: str, operator: str | None
) -> dict:
    record_id = _policy_text(record_id, "id", 36)
    if not record_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    now = datetime.datetime.now(datetime.UTC)
    async with in_transaction() as connection:
        record = await _locked_actual_policy(connection, tenant, record_id)
        if not record:
            raise CustomError(ErrorCode.NOT_FOUND)
        before_data = _policy_snapshot(record)
        record.deleted = True
        record.revision += 1
        record.last_synced_at = now
        record.updater = operator
        await record.save(using_db=connection)
        await _append_policy_history(
            connection,
            tenant=tenant,
            record=record,
            change_type="deleted",
            before_data=before_data,
            after_data={},
            batch_id=str(uuid.uuid4()),
            sync_source="manual",
            operator=operator,
            detected_at=now,
        )
    return {"id": record.id}


async def sync_actual_policies(
    tenant: str, data: dict, operator: str | None
) -> dict:
    data = require_dict(data)
    policies = data.get("policies")
    if not isinstance(policies, list) or len(policies) > MAX_POLICY_SYNC_ITEMS:
        raise CustomError(ErrorCode.PARAM_ERROR)
    replace = data.get("replace", False)
    if not isinstance(replace, bool):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if not policies and not replace:
        raise CustomError(ErrorCode.PARAM_ERROR)

    batch_id = _policy_text(data.get("batch_id"), "batch_id", 64) or str(
        uuid.uuid4()
    )
    sync_source = _policy_text(
        data.get("sync_source") or "playbook", "sync_source", 100
    )
    shared = {
        key: data[key]
        for key in (
            "accessory_project_id",
            "device_ci_id",
            "device_key",
            "device_id",
            "device_name",
            "device_type",
            "collection_run_id",
            "policy_type",
        )
        if key in data
    }
    normalized_items = []
    identities = set()
    for item in policies:
        if not isinstance(item, dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
        payload = _normalize_actual_policy_payload(
            {**shared, **item}, operator
        )
        identity = (payload["device_key"], payload["policy_key"])
        if identity in identities:
            raise CustomError(ErrorCode.PARAM_ERROR)
        identities.add(identity)
        normalized_items.append(payload)

    replace_device_key = _policy_text(
        data.get("device_key") or data.get("device_id") or data.get("device_ci_id"),
        "device_key",
        255,
    )
    if replace and not replace_device_key:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if replace and any(
        payload["device_key"] != replace_device_key for payload in normalized_items
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    now = datetime.datetime.now(datetime.UTC)
    counts = {"added": 0, "modified": 0, "deleted": 0, "synced": 0}
    device_keys = {payload["device_key"] for payload in normalized_items}
    if replace_device_key:
        device_keys.add(replace_device_key)

    async with in_transaction() as connection:
        existing_rows = []
        if device_keys:
            existing_rows = await (
                SupplyChainActualPolicy.filter(
                    tenant_id=tenant,
                    device_key__in=list(device_keys),
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .all()
            )
        existing_by_identity = {
            (row.device_key, row.policy_key): row for row in existing_rows
        }

        for payload in normalized_items:
            identity = (payload["device_key"], payload["policy_key"])
            record = existing_by_identity.get(identity)
            before_data = _policy_snapshot(record) if record else {}
            if record:
                for key, value in payload.items():
                    setattr(record, key, value)
                record.revision += 1
                record.last_synced_at = now
                await record.save(using_db=connection)
                after_data = _policy_snapshot(record)
                change_type = (
                    "synced"
                    if _policy_content(before_data) == _policy_content(after_data)
                    else "modified"
                )
            else:
                record = await SupplyChainActualPolicy.create(
                    using_db=connection,
                    tenant_id=tenant,
                    creator=operator,
                    revision=1,
                    last_synced_at=now,
                    **payload,
                )
                after_data = _policy_snapshot(record)
                change_type = "added"
                existing_by_identity[identity] = record

            counts[change_type] += 1
            await _append_policy_history(
                connection,
                tenant=tenant,
                record=record,
                change_type=change_type,
                before_data=before_data,
                after_data=after_data,
                batch_id=batch_id,
                sync_source=sync_source,
                operator=operator,
                detected_at=now,
            )

        if replace:
            received_keys = {
                payload["policy_key"]
                for payload in normalized_items
                if payload["device_key"] == replace_device_key
            }
            for record in existing_rows:
                if (
                    record.device_key != replace_device_key
                    or record.policy_key in received_keys
                ):
                    continue
                before_data = _policy_snapshot(record)
                record.deleted = True
                record.revision += 1
                record.last_synced_at = now
                record.updater = operator
                await record.save(using_db=connection)
                counts["deleted"] += 1
                await _append_policy_history(
                    connection,
                    tenant=tenant,
                    record=record,
                    change_type="deleted",
                    before_data=before_data,
                    after_data={},
                    batch_id=batch_id,
                    sync_source=sync_source,
                    operator=operator,
                    detected_at=now,
                )

    return {
        "batch_id": batch_id,
        "received": len(normalized_items),
        **counts,
        "history_count": sum(counts.values()),
    }


async def run_collection_task(tenant: str, task_id: str, operator: str | None) -> dict:
    task = await get_process_record_or_404(tenant, "collection-task", task_id)
    now = datetime.datetime.now(datetime.UTC)
    run = await SupplyChainCollectionRun.create(
        tenant_id=tenant,
        task_id=task.id,
        accessory_project_id=task.accessory_project_id,
        domain=task.domain,
        run_id=f"scr-{uuid.uuid4().hex}",
        status="pending",
        started_at=now,
        creator=operator,
        updater=operator,
    )
    task.last_run_at = now
    task.last_run_status = "pending"
    task.updater = operator
    await task.save()
    return {
        **run.json,
        "execution_payload": {
            "execution_mode": task.execution_mode,
            "playbook_id": task.playbook_id,
            "playbook_version_id": task.playbook_version_id,
            "action_id": task.action_id,
            "action_name": task.action_name,
            "timed_task_id": task.timed_task_id,
            "event_ingestion_id": task.event_ingestion_id,
            "target_type": task.target_type,
            "target_ci_ids": task.target_ci_ids or [],
            "input_params": task.input_params or {},
        },
    }


async def retry_collection_run(tenant: str, run_id: str, operator: str | None) -> dict:
    run = await get_process_record_or_404(tenant, "collection-run", run_id)
    if run.task_id:
        return await run_collection_task(tenant, run.task_id, operator)
    clone = await SupplyChainCollectionRun.create(
        tenant_id=tenant,
        task_id=run.task_id,
        accessory_project_id=run.accessory_project_id,
        domain=run.domain,
        run_id=f"scr-{uuid.uuid4().hex}",
        status="pending",
        started_at=datetime.datetime.now(datetime.UTC),
        creator=operator,
        updater=operator,
    )
    return clone.json


def policy_key(item) -> tuple:
    return (
        item.source_zone or "",
        item.source_address or "",
        item.target_zone or "",
        item.target_address or "",
        item.protocol or "",
        item.port or "",
        item.action or "",
    )


def policy_diff(preset, actual) -> dict:
    keys = ["source_zone", "source_address", "target_zone", "target_address", "protocol", "port", "action"]
    return {
        key: {"preset": getattr(preset, key, None), "actual": getattr(actual, key, None)}
        for key in keys
        if getattr(preset, key, None) != getattr(actual, key, None)
    }


async def create_policy_change_from_compare(tenant: str, actual, change_type: str, diff: dict, operator: str | None) -> None:
    await SupplyChainPolicyChange.create(
        tenant_id=tenant,
        accessory_project_id=actual.accessory_project_id,
        device_ci_id=actual.device_ci_id,
        policy_name=actual.rule_name or actual.raw_rule_id,
        change_type=change_type,
        diff_text=str(diff or actual.json),
        detected_at=datetime.datetime.now(datetime.UTC),
        risk_level="high" if change_type == "added" else "medium",
        creator=operator,
        updater=operator,
    )


async def run_policy_compare(tenant: str, data: dict, operator: str | None) -> dict:
    accessory_project_id = data.get("accessory_project_id")
    collection_run_id = data.get("collection_run_id")
    base_condition = Q(tenant_id=tenant, deleted=False)
    if accessory_project_id:
        base_condition &= Q(accessory_project_id=accessory_project_id)
    preset_policies = await SupplyChainPresetPolicy.filter(base_condition).all()
    actual_condition = base_condition
    if collection_run_id:
        actual_condition &= Q(collection_run_id=collection_run_id)
    actual_policies = await SupplyChainActualPolicy.filter(actual_condition).all()
    run = await SupplyChainPolicyCompareRun.create(
        tenant_id=tenant,
        accessory_project_id=accessory_project_id,
        collection_run_id=collection_run_id,
        started_at=datetime.datetime.now(datetime.UTC),
        creator=operator,
        updater=operator,
    )
    items = []
    presets_by_key = {policy_key(item): item for item in preset_policies}
    presets_by_name = {item.rule_name: item for item in preset_policies if item.rule_name}
    matched_preset_ids = set()
    for actual in actual_policies:
        if actual.parse_status != "parsed":
            items.append(await SupplyChainPolicyCompareItem.create(
                tenant_id=tenant,
                compare_run_id=run.id,
                actual_policy_id=actual.id,
                compare_type="unparsed",
                risk_level="medium",
                creator=operator,
                updater=operator,
            ))
            continue
        preset = presets_by_key.get(policy_key(actual))
        if preset:
            matched_preset_ids.add(preset.id)
            items.append(await SupplyChainPolicyCompareItem.create(
                tenant_id=tenant,
                compare_run_id=run.id,
                preset_policy_id=preset.id,
                actual_policy_id=actual.id,
                compare_type="matched",
                risk_level="low",
                handle_status="confirmed",
                creator=operator,
                updater=operator,
            ))
            continue
        name_match = presets_by_name.get(actual.rule_name)
        if name_match and name_match.id not in matched_preset_ids:
            diff = policy_diff(name_match, actual)
            matched_preset_ids.add(name_match.id)
            await create_policy_change_from_compare(tenant, actual, "modified", diff, operator)
            items.append(await SupplyChainPolicyCompareItem.create(
                tenant_id=tenant,
                compare_run_id=run.id,
                preset_policy_id=name_match.id,
                actual_policy_id=actual.id,
                compare_type="changed",
                risk_level="high",
                diff=diff,
                creator=operator,
                updater=operator,
            ))
            continue
        await create_policy_change_from_compare(tenant, actual, "added", actual.json, operator)
        items.append(await SupplyChainPolicyCompareItem.create(
            tenant_id=tenant,
            compare_run_id=run.id,
            actual_policy_id=actual.id,
            compare_type="added",
            risk_level="high",
            creator=operator,
            updater=operator,
        ))
    for preset in preset_policies:
        if preset.id in matched_preset_ids:
            continue
        items.append(await SupplyChainPolicyCompareItem.create(
            tenant_id=tenant,
            compare_run_id=run.id,
            preset_policy_id=preset.id,
            compare_type="missing",
            risk_level=preset.risk_level or "medium",
            creator=operator,
            updater=operator,
        ))
    counts = {item.compare_type: 0 for item in items}
    for item in items:
        counts[item.compare_type] = counts.get(item.compare_type, 0) + 1
    run.total = len(items)
    run.matched = counts.get("matched", 0)
    run.added = counts.get("added", 0)
    run.missing = counts.get("missing", 0)
    run.changed = counts.get("changed", 0)
    run.unparsed = counts.get("unparsed", 0)
    run.finished_at = datetime.datetime.now(datetime.UTC)
    await run.save()
    return {**run.json, "items": [item.json for item in items]}


async def update_compare_item_status(tenant: str, item_id: str, status: str, operator: str | None) -> dict:
    if status not in POLICY_HANDLE_STATUSES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    item = await SupplyChainPolicyCompareItem.filter(id=item_id, tenant_id=tenant, deleted=False).first()
    if not item:
        raise CustomError(ErrorCode.NOT_FOUND)
    item.handle_status = status
    item.updater = operator
    await item.save()
    return item.json


async def policy_change_external_callback(tenant: str, data: dict, operator: str | None) -> dict:
    change = None
    if data.get("id"):
        change = await SupplyChainPolicyChange.filter(id=data["id"], tenant_id=tenant, deleted=False).first()
    if not change and data.get("external_approve_ref"):
        change = await SupplyChainPolicyChange.filter(
            external_approve_ref=data["external_approve_ref"],
            tenant_id=tenant,
            deleted=False,
        ).first()
    if not change:
        raise CustomError(ErrorCode.NOT_FOUND)
    change.external_approve_ref = data.get("external_approve_ref") or change.external_approve_ref
    change.external_approve_result = data.get("external_approve_result")
    change.updater = operator
    await change.save()
    return change.json


async def discover_unknown_terminals(tenant: str, data: dict, operator: str | None) -> dict:
    records = data.get("items") or data.get("records") or []
    if not isinstance(records, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    saved = []
    for record in records:
        record = require_dict(record)
        mac_address = record.get("mac_address")
        if not mac_address:
            raise CustomError(ErrorCode.PARAM_ERROR)
        existing = await SupplyChainUnknownTerminal.filter(
            tenant_id=tenant,
            deleted=False,
            mac_address=mac_address,
            accessory_project_id=record.get("accessory_project_id") or data.get("accessory_project_id"),
        ).first()
        payload = {
            **record,
            "accessory_project_id": record.get("accessory_project_id") or data.get("accessory_project_id"),
            "last_seen_at": record.get("last_seen_at") or datetime.datetime.now(datetime.UTC),
        }
        if existing:
            payload["id"] = existing.id
        saved.append((await save_ledger_record(tenant, "unknown-terminal", payload, operator)).json)
    return {"total": len(saved), "items": saved}


async def transition_review_issue(tenant: str, data: dict, operator: str | None) -> dict:
    issue = await get_process_record_or_404(tenant, "review-issue", data.get("id") or data.get("review_issue_id"))
    to_status = data.get("to_status")
    if to_status not in ISSUE_STATUSES or to_status not in ISSUE_TRANSITIONS.get(issue.status, set()):
        raise CustomError(ErrorCode.PARAM_ERROR)
    from_status = issue.status
    issue.status = to_status
    issue.updater = operator
    await issue.save()
    record = await SupplyChainRemediationRecord.create(
        tenant_id=tenant,
        review_issue_id=issue.id,
        action_type=data.get("action_type") or "status_transition",
        content=data.get("content"),
        attachment_ids=data.get("attachment_ids") or [],
        operator=operator,
        operated_at=datetime.datetime.now(datetime.UTC),
        from_status=from_status,
        to_status=to_status,
        creator=operator,
        updater=operator,
    )
    return {"issue": issue.json, "record": record.json}


async def risk_to_review_issue(tenant: str, data: dict, operator: str | None) -> dict:
    source_type = data.get("source_object_type")
    source_id = data.get("source_object_id")
    if not source_type or not source_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    payload = {
        "accessory_project_id": data.get("accessory_project_id"),
        "domain": data.get("domain") or source_type,
        "source_object_type": source_type,
        "source_object_id": source_id,
        "title": data.get("title") or f"风险处置：{source_type}",
        "description": data.get("description"),
        "risk_level": data.get("risk_level") or "medium",
        "remediation_requirement": data.get("remediation_requirement"),
        "responsible_person": data.get("responsible_person"),
        "due_date": data.get("due_date"),
        "status": "pending",
    }
    issue = await save_review_issue(tenant, payload, operator)
    return issue.json


async def confirm_disposal_task(tenant: str, data: dict, operator: str | None) -> dict:
    task = await get_process_record_or_404(tenant, "disposal-task", data.get("id") or data.get("task_id"))
    if task.status not in {"pending_confirm", "failed"}:
        raise CustomError(ErrorCode.PARAM_ERROR)
    task.status = "executing"
    task.confirm_by = operator
    task.confirmed_at = datetime.datetime.now(datetime.UTC)
    task.run_id = task.run_id or f"scr-disposal-{uuid.uuid4().hex}"
    task.updater = operator
    await task.save()
    rule = None
    if task.rule_id:
        rule = await SupplyChainDisposalRule.filter(id=task.rule_id, tenant_id=tenant, deleted=False).first()
    return {
        **task.json,
        "execution_payload": {
            "run_id": task.run_id,
            "playbook_id": getattr(rule, "playbook_id", None),
            "action_id": getattr(rule, "action_id", None),
            "actions": task.actions or getattr(rule, "actions", []) or [],
            "input_mapping": getattr(rule, "input_mapping", {}) or {},
            "related_ci_ids": task.related_ci_ids or [],
            "personnel_ci_id": task.personnel_ci_id,
        },
    }


async def cancel_disposal_task(tenant: str, data: dict, operator: str | None) -> dict:
    task = await get_process_record_or_404(tenant, "disposal-task", data.get("id") or data.get("task_id"))
    if task.status in {"success", "cancelled"}:
        raise CustomError(ErrorCode.PARAM_ERROR)
    task.status = "cancelled"
    task.result_message = data.get("result_message") or task.result_message
    task.updater = operator
    await task.save()
    return task.json


async def create_notification_event(tenant: str, data: dict, operator: str | None) -> dict:
    event = await save_process_record(tenant, "notification-event", data, operator)
    return {
        **event.json,
        "notify_payload": {
            "event_type": event.event_type,
            "title": event.title,
            "content": event.content,
            "risk_level": event.risk_level,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "project_area_id": event.project_area_id,
            "accessory_project_id": event.accessory_project_id,
            "link": event.link,
            "receivers": event.receivers or [],
            "receiver_roles": event.receiver_roles or [],
        },
    }


async def create_attachment_ref(tenant: str, data: dict, operator: str | None) -> dict:
    payload = process_payload("attachment-ref", data, operator)
    payload["uploaded_by"] = payload.get("uploaded_by") or operator
    payload["uploaded_at"] = payload.get("uploaded_at") or datetime.datetime.now(datetime.UTC)
    ref = await SupplyChainAttachmentRef.create(tenant_id=tenant, creator=operator, **payload)
    return ref.json
