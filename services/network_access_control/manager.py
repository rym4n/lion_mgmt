# coding=utf-8
from __future__ import annotations

import datetime
import re
import uuid
import orjson
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.asset_management import AssetInstance, AssetReference
from models.network_access_control import (
    NetworkAccessAttachmentReference,
    NetworkAccessBoundaryLog,
    NetworkAccessCollectionRun,
    NetworkAccessCollectionTask,
    NetworkAccessDisposalRule,
    NetworkAccessDisposalTask,
    NetworkAccessExceptionDeviceLog,
    NetworkAccessNdaRecord,
    NetworkAccessNotificationEvent,
    NetworkAccessParseMappingTemplate,
    NetworkAccessActualPolicy,
    NetworkAccessPolicyChange,
    NetworkAccessPolicyCompareItem,
    NetworkAccessPolicyCompareRun,
    NetworkAccessPresetPolicy,
    NetworkAccessProjectAuthorization,
    NetworkAccessRemediationRecord,
    NetworkAccessReviewIssue,
    NetworkAccessTopology,
    NetworkAccessUnknownTerminal,
)

KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
ASSET_MODEL_STATUSES = {"active", "disabled"}
ASSET_STATUSES = {"active", "disabled", "archived"}
RISK_STATUSES = {"normal", "low", "medium", "high"}
SECRECY_LEVELS = {"low", "medium", "high", "critical"}
AUTHORIZATION_STATUSES = {"active", "expired", "revoked"}
ACCESS_CONTROL_STATUSES = {"normal", "abnormal", "revoked"}
NDA_SIGN_STATUSES = {"signed", "unsigned", "expired"}
UNKNOWN_TERMINAL_STATUSES = {"pending", "confirmed", "converted"}
UNKNOWN_DISCOVERY_SOURCES = {"switch_mac", "arp", "scan"}
BOUNDARY_LOG_CATEGORIES = {"file_operation", "mail_outbound", "system_access"}
EXCEPTION_LOG_TYPES = {"insert", "remove", "use"}
EXCEPTION_ABNORMAL_TYPES = {
    "none",
    "unauthorized_terminal",
    "unauthorized_person",
    "off_hours",
    "unregistered",
}
EXCEPTION_LOG_STATUSES = {"pending", "confirmed", "ignored", "converted"}
COLLECTION_DOMAINS = {"network", "personnel_nda", "boundary", "exception_device"}
COLLECTION_EXECUTION_MODES = {
    "playbook_action",
    "tool_action",
    "timed_task",
    "event_ingestion",
    "http_api",
    "file_parse",
    "manual",
}
COLLECTION_RUN_STATUSES = {"pending", "running", "success", "failed", "partial"}
AFTER_SUCCESS_ACTIONS = {"none", "policy_compare", "risk_evaluate"}
POLICY_ACTIONS = {"permit", "deny"}
POLICY_COMPARE_TYPES = {"matched", "added", "missing", "changed", "unparsed"}
POLICY_HANDLE_STATUSES = {"pending", "confirmed", "ignored", "converted"}
POLICY_CHANGE_TYPES = {"added", "modified", "moved", "deleted"}
POLICY_DETAIL_ALIASES = {
    "source_port": ("source_port", "src_port"),
    "destination_port": (
        "destination_port",
        "target_port",
        "dest_port",
        "dst_port",
        "port",
    ),
    "service": ("service", "service_name", "application", "protocol"),
    "policy_order": (
        "policy_order",
        "rule_order",
        "position",
        "sequence",
        "order",
    ),
}
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
DISPOSAL_TRIGGER_TYPES = {
    "person_resigned",
    "nda_expired",
    "nda_unsigned",
    "authorization_expired",
    "access_control_abnormal",
}
DISPOSAL_TASK_STATUSES = {
    "pending_confirm",
    "executing",
    "success",
    "failed",
    "skipped",
    "cancelled",
}
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
    "personnel_asset_id",
    "endpoint_asset_id",
    "network_device_asset_id",
    "exception_device_asset_id",
    "status",
    "sign_status",
    "access_control_status",
    "log_category",
    "risk_flag",
    "abnormal_type",
]

LEDGER_CONFIGS = {
    "personnel-authorization": {
        "model": NetworkAccessProjectAuthorization,
        "permission_resource": "authorization",
        "required": ["personnel_asset_id"],
        "fields": [
            "personnel_asset_id",
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
        "model": NetworkAccessNdaRecord,
        "permission_resource": "nda",
        "required": ["personnel_asset_id"],
        "fields": [
            "personnel_asset_id",
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
        "enums": {
            "sign_status": NDA_SIGN_STATUSES,
            "source_type": {"hr", "esign", "manual"},
        },
        "keyword": ["nda_type", "source_record_id", "remark"],
        "order": ["-utime"],
    },
    "unknown-terminal": {
        "model": NetworkAccessUnknownTerminal,
        "permission_resource": "terminal",
        "required": ["mac_address"],
        "fields": [
            "accessory_project_id",
            "mac_address",
            "ip_address",
            "network_device_asset_id",
            "port",
            "first_seen_at",
            "last_seen_at",
            "discovery_source",
            "status",
            "endpoint_asset_id",
            "review_issue_id",
            "remark",
        ],
        "enums": {
            "status": UNKNOWN_TERMINAL_STATUSES,
            "discovery_source": UNKNOWN_DISCOVERY_SOURCES,
        },
        "keyword": ["mac_address", "ip_address", "port", "remark"],
        "order": ["-last_seen_at", "-utime"],
    },
    "boundary-log": {
        "model": NetworkAccessBoundaryLog,
        "permission_resource": "boundary",
        "required": ["log_category"],
        "fields": [
            "accessory_project_id",
            "log_category",
            "operator_account",
            "client_address",
            "personnel_asset_id",
            "endpoint_asset_id",
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
        "model": NetworkAccessExceptionDeviceLog,
        "permission_resource": "exception",
        "required": ["exception_device_asset_id"],
        "fields": [
            "accessory_project_id",
            "exception_device_asset_id",
            "endpoint_asset_id",
            "personnel_asset_id",
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
    "device_asset_id",
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
        "model": NetworkAccessCollectionTask,
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
            "target_asset_ids",
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
        "model": NetworkAccessCollectionRun,
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
        "model": NetworkAccessParseMappingTemplate,
        "resource": "collection",
        "required": ["domain", "name"],
        "fields": [
            "domain",
            "name",
            "source_format",
            "parser_type",
            "parser_rules",
            "mapping_rules",
            "enabled",
        ],
        "enums": {"domain": COLLECTION_DOMAINS},
        "keyword": ["name"],
        "order": ["-utime"],
    },
    "preset-policy": {
        "model": NetworkAccessPresetPolicy,
        "resource": "policy",
        "required": ["rule_name"],
        "fields": [
            "accessory_project_id",
            "device_asset_id",
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
        "keyword": [
            "rule_name",
            "source_address",
            "target_address",
            "business_purpose",
        ],
        "order": ["-utime"],
    },
    "actual-policy": {
        "model": NetworkAccessActualPolicy,
        "resource": "policy",
        "required": ["device_key", "policy_key"],
        "fields": [
            "accessory_project_id",
            "device_asset_id",
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
            "accessory_project_id",
            "device_asset_id",
            "device_key",
            "device_name",
            "device_type",
            "collection_run_id",
            "policy_key",
            "policy_type",
            "rule_name",
            "source_zone",
            "raw_rule_id",
            "source_address",
            "target_zone",
            "target_address",
            "protocol",
            "port",
            "action",
            "raw_text",
            "parse_status",
            "creator",
            "updater",
        ],
        "keyword_all_fields": True,
        "order": ["-utime"],
    },
    "policy-change": {
        "model": NetworkAccessPolicyChange,
        "resource": "policy",
        "required": ["change_type"],
        "fields": [
            "accessory_project_id",
            "device_asset_id",
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
        "enums": {
            "change_type": POLICY_CHANGE_TYPES,
            "risk_level": RISK_STATUSES - {"normal"},
        },
        "keyword": [
            "accessory_project_id",
            "device_asset_id",
            "device_key",
            "actual_policy_id",
            "policy_key",
            "policy_name",
            "batch_id",
            "change_type",
            "sync_source",
            "diff_text",
            "external_approve_ref",
            "external_approve_result",
            "review_issue_id",
            "creator",
            "updater",
        ],
        "keyword_all_fields": True,
        "order": ["-detected_at", "-utime"],
    },
    "policy-compare-run": {
        "model": NetworkAccessPolicyCompareRun,
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
        "model": NetworkAccessTopology,
        "resource": "topology",
        "required": ["name"],
        "fields": [
            "project_area_id",
            "accessory_project_id",
            "name",
            "version",
            "graph_data",
            "change_summary",
            "status",
        ],
        "enums": {"status": TOPOLOGY_STATUSES},
        "keyword": ["name", "version", "change_summary"],
        "order": ["-utime"],
    },
    "review-issue": {
        "model": NetworkAccessReviewIssue,
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
        "model": NetworkAccessRemediationRecord,
        "resource": "issue",
        "required": ["review_issue_id", "action_type"],
        "fields": [
            "review_issue_id",
            "action_type",
            "content",
            "attachment_ids",
            "operator",
            "operated_at",
            "from_status",
            "to_status",
        ],
        "keyword": ["content", "operator"],
        "order": ["-operated_at", "-ctime"],
    },
    "disposal-rule": {
        "model": NetworkAccessDisposalRule,
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
        "enums": {
            "trigger_type": DISPOSAL_TRIGGER_TYPES,
            "risk_level": RISK_STATUSES - {"normal"},
        },
        "keyword": ["trigger_type"],
        "order": ["-utime"],
    },
    "disposal-task": {
        "model": NetworkAccessDisposalTask,
        "resource": "disposal",
        "required": ["trigger_type"],
        "fields": [
            "rule_id",
            "trigger_type",
            "project_area_id",
            "accessory_project_id",
            "personnel_asset_id",
            "related_asset_ids",
            "actions",
            "review_issue_id",
        ],
        "enums": {"trigger_type": DISPOSAL_TRIGGER_TYPES},
        "keyword": ["trigger_type", "result_message"],
        "order": ["-utime"],
    },
    "attachment-ref": {
        "model": NetworkAccessAttachmentReference,
        "resource": "attachment",
        "required": ["object_type", "object_id", "file_id", "file_name"],
        "fields": [
            "object_type",
            "object_id",
            "file_id",
            "file_name",
            "file_type",
            "file_size",
            "uploaded_by",
            "uploaded_at",
            "remark",
        ],
        "keyword": ["object_type", "file_name", "remark"],
        "order": ["-uploaded_at", "-ctime"],
    },
    "notification-event": {
        "model": NetworkAccessNotificationEvent,
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


def value_matches_keyword(value, keyword: str) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, dict):
        return any(value_matches_keyword(item, keyword) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(value_matches_keyword(item, keyword) for item in value)
    return keyword in str(value).casefold()


def json_matches_keyword(data: dict, keyword: str) -> bool:
    folded = keyword.strip().casefold()
    if not folded:
        return True
    return value_matches_keyword(data, folded)


def validate_key(value: str | None, field: str = "key") -> str:
    value = (value or "").strip()
    if not KEY_RE.match(value):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


def operator_name(request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("username") or account.get("name") or account.get("id")
    return (
        getattr(account, "username", None)
        or getattr(account, "name", None)
        or getattr(account, "id", None)
    )


def tenant_id(request) -> str:
    value = getattr(request.ctx, "session", {}).get("tenant_id")
    if not value:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


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
    if "evidence_attachment_ids" in payload and not isinstance(
        payload["evidence_attachment_ids"], list
    ):
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
    items = (
        await queryset.order_by(*config.get("order", ["-utime"]))
        .offset(offset)
        .limit(size)
        .all()
    )
    return {
        "total": total,
        "items": [item.json for item in items],
        "page": page,
        "size": size,
    }


async def accessory_project_ids_for_area(
    tenant: str, project_area_id: str
) -> list[str]:
    references = await AssetReference.filter(
        tenant_id=tenant,
        deleted=False,
        field_key="area_contains_project",
        source_asset_id=project_area_id,
    ).all()
    return [item.target_asset_id for item in references]


async def ledger_scope_condition(tenant: str, config: dict, data: dict) -> Q:
    condition = Q(tenant_id=tenant, deleted=False)
    if data.get("accessory_project_id") and "accessory_project_id" in config["fields"]:
        return condition & Q(accessory_project_id=data["accessory_project_id"])
    if not data.get("project_area_id"):
        return condition
    if "project_area_id" in config["fields"]:
        return condition & Q(project_area_id=data["project_area_id"])
    if "accessory_project_id" in config["fields"]:
        project_ids = await accessory_project_ids_for_area(
            tenant, data["project_area_id"]
        )
        return condition & Q(accessory_project_id__in=project_ids or ["__none__"])
    return condition


async def get_ledger_record_or_404(tenant: str, domain: str, record_id: str):
    record = (
        await ledger_config(domain)["model"]
        .filter(id=record_id, tenant_id=tenant, deleted=False)
        .first()
    )
    if not record:
        raise CustomError(ErrorCode.NOT_FOUND)
    return record


async def save_ledger_record(
    tenant: str, domain: str, data: dict, operator: str | None
):
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
    areas = (
        await AssetInstance.filter(
            tenant_id=tenant, deleted=False, asset_model_key="project_area"
        )
        .order_by("name")
        .all()
    )
    projects = (
        await AssetInstance.filter(
            tenant_id=tenant, deleted=False, asset_model_key="accessory_project"
        )
        .order_by("name")
        .all()
    )
    references = await AssetReference.filter(
        tenant_id=tenant,
        deleted=False,
        field_key="area_contains_project",
        source_asset_id__in=[item.id for item in areas] or ["__none__"],
    ).all()
    projects_by_id = {item.id: item for item in projects}
    children_by_area: dict[str, list[AssetInstance]] = {item.id: [] for item in areas}
    attached_project_ids = set()
    for reference in references:
        project = projects_by_id.get(reference.target_asset_id)
        if project:
            children_by_area.setdefault(reference.source_asset_id, []).append(project)
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
        tree.append(
            {
                "id": "__unassigned__",
                "name": "未关联配件项目",
                "asset_model_key": "virtual",
                "children": [project.json for project in unassigned],
            }
        )
    return {"items": tree, "total": len(tree)}


async def overview_summary(tenant: str, data: dict) -> dict:
    auth_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["personnel-authorization"], data
    )
    nda_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["nda-record"], data)
    unknown_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["unknown-terminal"], data
    )
    boundary_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["boundary-log"], data
    )
    exception_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["exception-device-log"], data
    )
    disposal_scope = await process_scope_condition(
        tenant, PROCESS_CONFIGS["disposal-task"], data
    )
    review_issue_scope = await process_scope_condition(
        tenant, PROCESS_CONFIGS["review-issue"], data
    )
    collection_run_scope = await process_scope_condition(
        tenant, PROCESS_CONFIGS["collection-run"], data
    )
    project_count = await AssetInstance.filter(
        tenant_id=tenant, deleted=False, asset_model_key="accessory_project"
    ).count()
    area_count = await AssetInstance.filter(
        tenant_id=tenant, deleted=False, asset_model_key="project_area"
    ).count()
    nda_unsigned = await NetworkAccessNdaRecord.filter(
        nda_scope & Q(sign_status="unsigned")
    ).count()
    nda_expired = await NetworkAccessNdaRecord.filter(
        nda_scope & Q(sign_status="expired")
    ).count()
    authorization_abnormal = await NetworkAccessProjectAuthorization.filter(
        auth_scope & Q(access_control_status="abnormal")
    ).count()
    unknown_pending = await NetworkAccessUnknownTerminal.filter(
        unknown_scope & Q(status="pending")
    ).count()
    boundary_risk = await NetworkAccessBoundaryLog.filter(
        boundary_scope & Q(risk_flag=True)
    ).count()
    exception_abnormal = await NetworkAccessExceptionDeviceLog.filter(
        exception_scope & ~Q(abnormal_type="none")
    ).count()
    disposal_pending = await NetworkAccessDisposalTask.filter(
        disposal_scope & Q(status="pending_confirm")
    ).count()
    issue_pending_review = await NetworkAccessReviewIssue.filter(
        review_issue_scope & Q(status="pending_review")
    ).count()
    collection_failed = await NetworkAccessCollectionRun.filter(
        collection_run_scope & Q(status="failed")
    ).count()
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
    auth_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["personnel-authorization"], data
    )
    nda_scope = await ledger_scope_condition(tenant, LEDGER_CONFIGS["nda-record"], data)
    unknown_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["unknown-terminal"], data
    )
    boundary_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["boundary-log"], data
    )
    exception_scope = await ledger_scope_condition(
        tenant, LEDGER_CONFIGS["exception-device-log"], data
    )
    disposal_scope = await process_scope_condition(
        tenant, PROCESS_CONFIGS["disposal-task"], data
    )
    review_issue_scope = await process_scope_condition(
        tenant, PROCESS_CONFIGS["review-issue"], data
    )
    collection_run_scope = await process_scope_condition(
        tenant, PROCESS_CONFIGS["collection-run"], data
    )
    items = [
        {
            "type": "nda_unsigned",
            "title": "NDA 未签署",
            "total": await NetworkAccessNdaRecord.filter(
                nda_scope & Q(sign_status="unsigned")
            ).count(),
        },
        {
            "type": "nda_expired",
            "title": "NDA 已过期",
            "total": await NetworkAccessNdaRecord.filter(
                nda_scope & Q(sign_status="expired")
            ).count(),
        },
        {
            "type": "authorization_abnormal",
            "title": "门禁授权异常",
            "total": await NetworkAccessProjectAuthorization.filter(
                auth_scope & Q(access_control_status="abnormal")
            ).count(),
        },
        {
            "type": "disposal_task_pending_confirm",
            "title": "待确认权限回收",
            "total": await NetworkAccessDisposalTask.filter(
                disposal_scope & Q(status="pending_confirm")
            ).count(),
        },
        {
            "type": "review_issue_pending_review",
            "title": "待复核问题",
            "total": await NetworkAccessReviewIssue.filter(
                review_issue_scope & Q(status="pending_review")
            ).count(),
        },
        {
            "type": "unknown_terminal_pending",
            "title": "待确认未知终端",
            "total": await NetworkAccessUnknownTerminal.filter(
                unknown_scope & Q(status="pending")
            ).count(),
        },
        {
            "type": "collection_run_failed",
            "title": "采集失败",
            "total": await NetworkAccessCollectionRun.filter(
                collection_run_scope & Q(status="failed")
            ).count(),
        },
        {
            "type": "boundary_risk",
            "title": "边界风险日志",
            "total": await NetworkAccessBoundaryLog.filter(
                boundary_scope & Q(risk_flag=True)
            ).count(),
        },
        {
            "type": "exception_device_abnormal",
            "title": "例外设备异常",
            "total": await NetworkAccessExceptionDeviceLog.filter(
                exception_scope & ~Q(abnormal_type="none")
            ).count(),
        },
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
    for field in [
        "target_asset_ids",
        "actions",
        "related_asset_ids",
        "attachment_ids",
        "receivers",
        "receiver_roles",
    ]:
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
        payload["issue_no"] = (
            f"SCR-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )
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
            project_ids = await accessory_project_ids_for_area(
                tenant, data["project_area_id"]
            )
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
    keyword = str(data.get("keyword") or "").strip()
    queryset = config["model"].filter(condition)
    if keyword and config.get("keyword_all_fields"):
        records = await queryset.order_by(*config.get("order", ["-utime"])).all()
        items = [
            item.json for item in records if json_matches_keyword(item.json, keyword)
        ]
        return {
            "total": len(items),
            "items": items[offset : offset + size],
            "page": page,
            "size": size,
        }
    keyword_fields = config.get("keyword", [])
    if keyword and keyword_fields:
        keyword_condition = Q()
        for field in keyword_fields:
            keyword_condition |= Q(**{f"{field}__icontains": keyword})
        condition &= keyword_condition
    queryset = config["model"].filter(condition)
    total = await queryset.count()
    items = (
        await queryset.order_by(*config.get("order", ["-utime"]))
        .offset(offset)
        .limit(size)
        .all()
    )
    return {
        "total": total,
        "items": [item.json for item in items],
        "page": page,
        "size": size,
    }


async def get_process_record_or_404(tenant: str, domain: str, record_id: str):
    record = (
        await process_config(domain)["model"]
        .filter(id=record_id, tenant_id=tenant, deleted=False)
        .first()
    )
    if not record:
        raise CustomError(ErrorCode.NOT_FOUND)
    return record


async def save_process_record(
    tenant: str, domain: str, data: dict, operator: str | None
):
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
        first("device_key", "device_id", "device_asset_id"), "device_key", 255
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
    normalized["device_name"] = (
        _policy_text(first("device_name"), "device_name", 255) or None
    )
    normalized["device_type"] = (
        _policy_text(first("device_type"), "device_type", 30).lower() or None
    )
    normalized["policy_type"] = (
        _policy_text(first("policy_type"), "policy_type", 50).lower() or None
    )

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

    if "policy_data" in data:
        if not isinstance(data["policy_data"], dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
        policy_data = dict(data["policy_data"])
    elif "policy_data" in defaults:
        policy_data = dict(defaults["policy_data"] or {})
    else:
        policy_data = {
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
    for canonical_key, aliases in POLICY_DETAIL_ALIASES.items():
        if canonical_key in data:
            policy_data[canonical_key] = data[canonical_key]
            continue
        nested_alias = next((alias for alias in aliases if alias in policy_data), None)
        if nested_alias is not None:
            policy_data[canonical_key] = policy_data[nested_alias]
            continue
        top_level_alias = next(
            (alias for alias in aliases if alias != canonical_key and alias in data),
            None,
        )
        if top_level_alias is not None:
            policy_data[canonical_key] = data[top_level_alias]
    normalized["policy_data"] = policy_data

    payload = process_payload("actual-policy", normalized, operator)
    payload["device_key"] = device_key
    payload["policy_key"] = policy_key
    return payload


def _policy_snapshot(record: NetworkAccessActualPolicy) -> dict:
    payload = record.json
    return {
        key: payload.get(key)
        for key in (
            "id",
            "accessory_project_id",
            "device_asset_id",
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
            "source_port",
            "destination_port",
            "service",
            "policy_order",
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


def _policy_content_without_order(snapshot: dict) -> dict:
    content = _policy_content(snapshot)
    content.pop("policy_order", None)
    policy_data = dict(content.get("policy_data") or {})
    for alias in POLICY_DETAIL_ALIASES["policy_order"]:
        policy_data.pop(alias, None)
    content["policy_data"] = policy_data
    return content


def _classify_policy_update(before_data: dict, after_data: dict) -> str | None:
    if _policy_content(before_data) == _policy_content(after_data):
        return None
    if before_data.get("policy_order") != after_data.get(
        "policy_order"
    ) and _policy_content_without_order(before_data) == _policy_content_without_order(
        after_data
    ):
        return "moved"
    return "modified"


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
    record: NetworkAccessActualPolicy,
    change_type: str,
    before_data: dict,
    after_data: dict,
    batch_id: str,
    sync_source: str,
    operator: str | None,
    detected_at: datetime.datetime,
) -> NetworkAccessPolicyChange:
    return await NetworkAccessPolicyChange.create(
        using_db=connection,
        tenant_id=tenant,
        accessory_project_id=record.accessory_project_id,
        device_asset_id=record.device_asset_id,
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
        NetworkAccessActualPolicy.filter(id=record_id, tenant_id=tenant, deleted=False)
        .using_db(connection)
        .select_for_update()
        .first()
    )


async def save_actual_policy(
    tenant: str, data: dict, operator: str | None
) -> NetworkAccessActualPolicy:
    data = require_dict(data)
    record_id = _policy_text(data.get("id"), "id", 36)
    now = datetime.datetime.now(datetime.UTC)
    batch_id = _policy_text(data.get("batch_id"), "batch_id", 64) or str(uuid.uuid4())
    sync_source = _policy_text(data.get("sync_source") or "manual", "sync_source", 100)

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

        payload = _normalize_actual_policy_payload(data, operator, defaults=defaults)
        duplicate_query = NetworkAccessActualPolicy.filter(
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
        else:
            record = await NetworkAccessActualPolicy.create(
                using_db=connection,
                tenant_id=tenant,
                creator=operator,
                revision=1,
                last_synced_at=now,
                **payload,
            )

        after_data = _policy_snapshot(record)
        change_type = (
            _classify_policy_update(before_data, after_data) if existing else "added"
        )
        if change_type:
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


async def sync_actual_policies(tenant: str, data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    policies = data.get("policies")
    if not isinstance(policies, list) or len(policies) > MAX_POLICY_SYNC_ITEMS:
        raise CustomError(ErrorCode.PARAM_ERROR)
    replace = data.get("replace", False)
    if not isinstance(replace, bool):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if not policies and not replace:
        raise CustomError(ErrorCode.PARAM_ERROR)

    batch_id = _policy_text(data.get("batch_id"), "batch_id", 64) or str(uuid.uuid4())
    sync_source = _policy_text(
        data.get("sync_source") or "playbook", "sync_source", 100
    )
    shared = {
        key: data[key]
        for key in (
            "accessory_project_id",
            "device_asset_id",
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
        payload = _normalize_actual_policy_payload({**shared, **item}, operator)
        identity = (payload["device_key"], payload["policy_key"])
        if identity in identities:
            raise CustomError(ErrorCode.PARAM_ERROR)
        identities.add(identity)
        normalized_items.append(payload)

    replace_device_key = _policy_text(
        data.get("device_key") or data.get("device_id") or data.get("device_asset_id"),
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
    counts = {
        "added": 0,
        "modified": 0,
        "moved": 0,
        "deleted": 0,
        "unchanged": 0,
    }
    device_keys = {payload["device_key"] for payload in normalized_items}
    if replace_device_key:
        device_keys.add(replace_device_key)

    async with in_transaction() as connection:
        existing_rows = []
        if device_keys:
            existing_rows = await (
                NetworkAccessActualPolicy.filter(
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
                change_type = _classify_policy_update(before_data, after_data)
            else:
                record = await NetworkAccessActualPolicy.create(
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

            count_key = change_type or "unchanged"
            counts[count_key] += 1
            if change_type:
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
        "synced": counts["unchanged"],
        "history_count": sum(counts[key] for key in POLICY_CHANGE_TYPES),
    }


async def run_collection_task(tenant: str, task_id: str, operator: str | None) -> dict:
    task = await get_process_record_or_404(tenant, "collection-task", task_id)
    now = datetime.datetime.now(datetime.UTC)
    run = await NetworkAccessCollectionRun.create(
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
            "target_asset_ids": task.target_asset_ids or [],
            "input_params": task.input_params or {},
        },
    }


async def retry_collection_run(tenant: str, run_id: str, operator: str | None) -> dict:
    run = await get_process_record_or_404(tenant, "collection-run", run_id)
    if run.task_id:
        return await run_collection_task(tenant, run.task_id, operator)
    clone = await NetworkAccessCollectionRun.create(
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
    keys = [
        "source_zone",
        "source_address",
        "target_zone",
        "target_address",
        "protocol",
        "port",
        "action",
    ]
    return {
        key: {
            "preset": getattr(preset, key, None),
            "actual": getattr(actual, key, None),
        }
        for key in keys
        if getattr(preset, key, None) != getattr(actual, key, None)
    }


async def create_policy_change_from_compare(
    tenant: str, actual, change_type: str, diff: dict, operator: str | None
) -> None:
    await NetworkAccessPolicyChange.create(
        tenant_id=tenant,
        accessory_project_id=actual.accessory_project_id,
        device_asset_id=actual.device_asset_id,
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
    preset_policies = await NetworkAccessPresetPolicy.filter(base_condition).all()
    actual_condition = base_condition
    if collection_run_id:
        actual_condition &= Q(collection_run_id=collection_run_id)
    actual_policies = await NetworkAccessActualPolicy.filter(actual_condition).all()
    run = await NetworkAccessPolicyCompareRun.create(
        tenant_id=tenant,
        accessory_project_id=accessory_project_id,
        collection_run_id=collection_run_id,
        started_at=datetime.datetime.now(datetime.UTC),
        creator=operator,
        updater=operator,
    )
    items = []
    presets_by_key = {policy_key(item): item for item in preset_policies}
    presets_by_name = {
        item.rule_name: item for item in preset_policies if item.rule_name
    }
    matched_preset_ids = set()
    for actual in actual_policies:
        if actual.parse_status != "parsed":
            items.append(
                await NetworkAccessPolicyCompareItem.create(
                    tenant_id=tenant,
                    compare_run_id=run.id,
                    actual_policy_id=actual.id,
                    compare_type="unparsed",
                    risk_level="medium",
                    creator=operator,
                    updater=operator,
                )
            )
            continue
        preset = presets_by_key.get(policy_key(actual))
        if preset:
            matched_preset_ids.add(preset.id)
            items.append(
                await NetworkAccessPolicyCompareItem.create(
                    tenant_id=tenant,
                    compare_run_id=run.id,
                    preset_policy_id=preset.id,
                    actual_policy_id=actual.id,
                    compare_type="matched",
                    risk_level="low",
                    handle_status="confirmed",
                    creator=operator,
                    updater=operator,
                )
            )
            continue
        name_match = presets_by_name.get(actual.rule_name)
        if name_match and name_match.id not in matched_preset_ids:
            diff = policy_diff(name_match, actual)
            matched_preset_ids.add(name_match.id)
            await create_policy_change_from_compare(
                tenant, actual, "modified", diff, operator
            )
            items.append(
                await NetworkAccessPolicyCompareItem.create(
                    tenant_id=tenant,
                    compare_run_id=run.id,
                    preset_policy_id=name_match.id,
                    actual_policy_id=actual.id,
                    compare_type="changed",
                    risk_level="high",
                    diff=diff,
                    creator=operator,
                    updater=operator,
                )
            )
            continue
        await create_policy_change_from_compare(
            tenant, actual, "added", actual.json, operator
        )
        items.append(
            await NetworkAccessPolicyCompareItem.create(
                tenant_id=tenant,
                compare_run_id=run.id,
                actual_policy_id=actual.id,
                compare_type="added",
                risk_level="high",
                creator=operator,
                updater=operator,
            )
        )
    for preset in preset_policies:
        if preset.id in matched_preset_ids:
            continue
        items.append(
            await NetworkAccessPolicyCompareItem.create(
                tenant_id=tenant,
                compare_run_id=run.id,
                preset_policy_id=preset.id,
                compare_type="missing",
                risk_level=preset.risk_level or "medium",
                creator=operator,
                updater=operator,
            )
        )
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


async def update_compare_item_status(
    tenant: str, item_id: str, status: str, operator: str | None
) -> dict:
    if status not in POLICY_HANDLE_STATUSES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    item = await NetworkAccessPolicyCompareItem.filter(
        id=item_id, tenant_id=tenant, deleted=False
    ).first()
    if not item:
        raise CustomError(ErrorCode.NOT_FOUND)
    item.handle_status = status
    item.updater = operator
    await item.save()
    return item.json


async def policy_change_external_callback(
    tenant: str, data: dict, operator: str | None
) -> dict:
    change = None
    if data.get("id"):
        change = await NetworkAccessPolicyChange.filter(
            id=data["id"], tenant_id=tenant, deleted=False
        ).first()
    if not change and data.get("external_approve_ref"):
        change = await NetworkAccessPolicyChange.filter(
            external_approve_ref=data["external_approve_ref"],
            tenant_id=tenant,
            deleted=False,
        ).first()
    if not change:
        raise CustomError(ErrorCode.NOT_FOUND)
    change.external_approve_ref = (
        data.get("external_approve_ref") or change.external_approve_ref
    )
    change.external_approve_result = data.get("external_approve_result")
    change.updater = operator
    await change.save()
    return change.json


async def discover_unknown_terminals(
    tenant: str, data: dict, operator: str | None
) -> dict:
    records = data.get("items") or data.get("records") or []
    if not isinstance(records, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    saved = []
    for record in records:
        record = require_dict(record)
        mac_address = record.get("mac_address")
        if not mac_address:
            raise CustomError(ErrorCode.PARAM_ERROR)
        existing = await NetworkAccessUnknownTerminal.filter(
            tenant_id=tenant,
            deleted=False,
            mac_address=mac_address,
            accessory_project_id=record.get("accessory_project_id")
            or data.get("accessory_project_id"),
        ).first()
        payload = {
            **record,
            "accessory_project_id": record.get("accessory_project_id")
            or data.get("accessory_project_id"),
            "last_seen_at": record.get("last_seen_at")
            or datetime.datetime.now(datetime.UTC),
        }
        if existing:
            payload["id"] = existing.id
        saved.append(
            (
                await save_ledger_record(tenant, "unknown-terminal", payload, operator)
            ).json
        )
    return {"total": len(saved), "items": saved}


async def transition_review_issue(
    tenant: str, data: dict, operator: str | None
) -> dict:
    issue = await get_process_record_or_404(
        tenant, "review-issue", data.get("id") or data.get("review_issue_id")
    )
    to_status = data.get("to_status")
    if to_status not in ISSUE_STATUSES or to_status not in ISSUE_TRANSITIONS.get(
        issue.status, set()
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    from_status = issue.status
    issue.status = to_status
    issue.updater = operator
    await issue.save()
    record = await NetworkAccessRemediationRecord.create(
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
    task = await get_process_record_or_404(
        tenant, "disposal-task", data.get("id") or data.get("task_id")
    )
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
        rule = await NetworkAccessDisposalRule.filter(
            id=task.rule_id, tenant_id=tenant, deleted=False
        ).first()
    return {
        **task.json,
        "execution_payload": {
            "run_id": task.run_id,
            "playbook_id": getattr(rule, "playbook_id", None),
            "action_id": getattr(rule, "action_id", None),
            "actions": task.actions or getattr(rule, "actions", []) or [],
            "input_mapping": getattr(rule, "input_mapping", {}) or {},
            "related_asset_ids": task.related_asset_ids or [],
            "personnel_asset_id": task.personnel_asset_id,
        },
    }


async def cancel_disposal_task(tenant: str, data: dict, operator: str | None) -> dict:
    task = await get_process_record_or_404(
        tenant, "disposal-task", data.get("id") or data.get("task_id")
    )
    if task.status in {"success", "cancelled"}:
        raise CustomError(ErrorCode.PARAM_ERROR)
    task.status = "cancelled"
    task.result_message = data.get("result_message") or task.result_message
    task.updater = operator
    await task.save()
    return task.json


async def create_notification_event(
    tenant: str, data: dict, operator: str | None
) -> dict:
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
    payload["uploaded_at"] = payload.get("uploaded_at") or datetime.datetime.now(
        datetime.UTC
    )
    ref = await NetworkAccessAttachmentReference.create(
        tenant_id=tenant, creator=operator, **payload
    )
    return ref.json
