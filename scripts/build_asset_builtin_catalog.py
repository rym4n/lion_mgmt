#!/usr/bin/env python3
"""Build the versioned asset builtin catalog from an exported asset snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CATALOG_KEY = "aioe-asset-builtins"
CATALOG_VERSION = "aioe-assets-2026.07-v2"
EXPECTED_COUNTS = {
    "groups": 30,
    "models": 236,
    "fields": 3433,
    "unique_rules": 1,
    "icon_aliases": 28,
}
RELATION_NAMES = {
    "belong": "属于",
    "group": "组成",
    "run": "运行于",
    "install_on": "安装于",
    "contains": "包含",
    "connect": "关联",
}
CARDINALITIES = {
    "1:1": ("one", "one"),
    "1:n": ("many", "one"),
    "n:1": ("one", "many"),
    "n:n": ("many", "many"),
}
CLOUD_GROUPS = {"aliyun", "aws", "azure", "hwcloud", "qcloud", "openstack"}
DIRECTORY_GROUP = {"key": "directory", "name": "基础数据"}
# SOC definitions were captured read-only from 10.3.0.46 on 2026-07-23.
DIRECTORY_MODELS = (
    {
        "key": "organization",
        "name": "组织",
        "icon": "ri:organization-chart",
        "primary_field": "inst_name",
        "fields": (
            {
                "field_key": "inst_name",
                "label": "组织名称",
                "required": True,
                "unique": True,
                "sort": 1,
                "source_data_type": "synthetic",
                "options": {
                    "validation_type": "unrestricted",
                    "widget_type": "single_line",
                    "custom_regex": "",
                },
            },
            {
                "field_key": "path",
                "label": "组织路径",
                "sort": 2,
                "source_data_type": "synthetic",
                "options": {
                    "validation_type": "unrestricted",
                    "widget_type": "single_line",
                    "custom_regex": "",
                },
            },
        ),
    },
    {
        "key": "project",
        "name": "项目",
        "icon": "ri:folder-3-line",
        "primary_field": "name",
        "fields": (
            {
                "field_key": "code",
                "label": "项目编码",
                "required": True,
                "unique": True,
                "sort": 10,
            },
            {
                "field_key": "name",
                "label": "项目名称",
                "required": True,
                "sort": 20,
            },
            {
                "field_key": "project_type",
                "label": "项目类型",
                "data_type": "enum",
                "enum_options": [
                    "项目",
                    "厂区项目",
                    "配件项目",
                    "研发项目",
                    "客户项目",
                    "其他",
                ],
                "sort": 30,
            },
            {"field_key": "owner_department", "label": "责任部门", "sort": 40},
            {
                "field_key": "manager",
                "label": "负责人",
                "data_type": "ref",
                "referenced_asset_model_id": "user",
                "options": {"multiple": False, "reverse_unique": False},
                "sort": 50,
            },
            {
                "field_key": "security_level",
                "label": "安全等级",
                "data_type": "enum",
                "enum_options": ["一般", "重要", "核心", "涉密", "未知"],
                "sort": 60,
            },
            {
                "field_key": "business_status",
                "label": "状态",
                "data_type": "enum",
                "enum_options": ["正常", "停用", "规划中", "未知"],
                "sort": 70,
            },
            {"field_key": "remark", "label": "备注", "sort": 80},
        ),
    },
    {
        "key": "area",
        "name": "区域",
        "icon": "ri:map-pin-2-line",
        "primary_field": "name",
        "fields": (
            {
                "field_key": "code",
                "label": "区域编码",
                "required": True,
                "unique": True,
                "sort": 10,
            },
            {
                "field_key": "name",
                "label": "区域名称",
                "required": True,
                "sort": 20,
            },
            {
                "field_key": "area_type",
                "label": "区域类型",
                "data_type": "enum",
                "enum_options": [
                    "厂区",
                    "楼栋",
                    "楼层",
                    "机房",
                    "实验室",
                    "办公区",
                    "外协区域",
                    "核心内网",
                    "外网出口",
                    "防火墙边界",
                    "终端接入",
                    "监控网",
                    "门禁网",
                    "其他",
                ],
                "sort": 50,
            },
            {
                "field_key": "physical_location",
                "label": "物理位置",
                "sort": 60,
            },
            {
                "field_key": "security_level",
                "label": "安全等级",
                "data_type": "enum",
                "enum_options": ["一般", "重要", "核心", "涉密", "未知"],
                "sort": 70,
            },
            {
                "field_key": "business_status",
                "label": "状态",
                "data_type": "enum",
                "enum_options": ["正常", "停用", "规划中", "未知"],
                "sort": 80,
            },
            {"field_key": "remark", "label": "备注", "sort": 90},
        ),
    },
    {
        "key": "authorized_device",
        "name": "授权/例外设备",
        "icon": "ri:shield-check-line",
        "primary_field": "device_name",
        "fields": (
            {
                "field_key": "device_name",
                "label": "设备名称",
                "required": True,
                "sort": 10,
            },
            {
                "field_key": "auth_method",
                "label": "授权方式",
                "data_type": "enum",
                "enum_options": [
                    "序列号",
                    "有线MAC地址",
                    "无线MAC地址",
                    "USB序列号",
                    "设备指纹",
                    "人工登记",
                    "其他",
                ],
                "sort": 30,
            },
            {
                "field_key": "auth_value",
                "label": "授权认证值",
                "unique": True,
                "sort": 40,
            },
            {
                "field_key": "device_usage",
                "label": "设备用途",
                "data_type": "enum",
                "enum_options": [
                    "常规接入",
                    "例外使用",
                    "调试使用",
                    "只读介质",
                    "其他",
                ],
                "sort": 80,
            },
            {
                "field_key": "authorization_status",
                "label": "授权状态",
                "data_type": "enum",
                "enum_options": ["授权", "未授权", "已撤销", "过期", "异常", "未知"],
                "sort": 90,
            },
            {
                "field_key": "valid_from",
                "label": "授权开始日期",
                "data_type": "date",
                "sort": 100,
            },
            {
                "field_key": "valid_to",
                "label": "授权结束日期",
                "data_type": "date",
                "sort": 110,
            },
            {
                "field_key": "need_alarm",
                "label": "是否告警",
                "data_type": "bool",
                "sort": 120,
            },
            {
                "field_key": "source_order_no",
                "label": "OA/审批单号",
                "sort": 130,
            },
            {"field_key": "control_plan", "label": "管控方案", "sort": 140},
            {"field_key": "remark", "label": "备注", "sort": 150},
        ),
    },
    {
        "key": "user",
        "name": "员工",
        "icon": "ri:contacts-book-line",
        "primary_field": "person_name",
        "fields": (
            {
                "field_key": "employee_no",
                "label": "工号",
                "required": True,
                "unique": True,
                "sort": 10,
            },
            {
                "field_key": "person_name",
                "label": "姓名",
                "required": True,
                "sort": 20,
            },
            {
                "field_key": "employment_status",
                "label": "在职状态",
                "data_type": "enum",
                "enum_options": ["在职", "离职", "外协", "未知"],
                "sort": 30,
            },
            {"field_key": "company", "label": "所属公司", "sort": 40},
            {"field_key": "department", "label": "部门", "sort": 50},
            {
                "field_key": "is_secret",
                "label": "是否涉密",
                "data_type": "bool",
                "sort": 70,
            },
            {
                "field_key": "entry_date",
                "label": "入职日",
                "data_type": "date",
                "sort": 80,
            },
            {
                "field_key": "leave_date",
                "label": "离职日",
                "data_type": "date",
                "sort": 90,
            },
            {
                "field_key": "access_auth_status",
                "label": "门禁授权状态",
                "data_type": "enum",
                "enum_options": ["授权", "未授权", "已撤销", "异常", "未知"],
                "sort": 100,
            },
            {
                "field_key": "nda_sign_status",
                "label": "保密协议签署状态",
                "data_type": "enum",
                "enum_options": ["已签署", "未签署", "过期", "不适用", "未知"],
                "sort": 110,
            },
            {
                "field_key": "nda_signed_at",
                "label": "保密协议签署时间",
                "data_type": "datetime",
                "sort": 120,
            },
            {
                "field_key": "updated_at",
                "label": "更新时间",
                "data_type": "datetime",
                "sort": 130,
            },
            {"field_key": "remark", "label": "备注", "sort": 140},
        ),
    },
    {
        "key": "file_share",
        "name": "共享文件",
        "icon": "ri:folder-shared-line",
        "primary_field": "share_name",
        "fields": (
            {
                "field_key": "share_name",
                "label": "共享名称",
                "required": True,
                "sort": 10,
            },
            {
                "field_key": "file_server",
                "label": "文件服务器",
                "required": True,
                "sort": 20,
            },
            {"field_key": "share_path", "label": "共享路径", "sort": 30},
            {
                "field_key": "owner_department",
                "label": "责任部门",
                "sort": 70,
            },
            {
                "field_key": "permission_baseline",
                "label": "访问权限基线",
                "sort": 90,
            },
            {
                "field_key": "sensitivity_level",
                "label": "数据敏感等级",
                "data_type": "enum",
                "enum_options": ["公开", "内部", "敏感", "高敏", "未知"],
                "sort": 100,
            },
            {
                "field_key": "business_status",
                "label": "状态",
                "data_type": "enum",
                "enum_options": ["正常", "停用", "异常", "未知"],
                "sort": 110,
            },
            {"field_key": "remark", "label": "备注", "sort": 120},
        ),
    },
)


def replace_directory_models(models: list[dict], fields: list[dict]) -> None:
    directory_keys = {definition["key"] for definition in DIRECTORY_MODELS}
    models[:] = [item for item in models if item["key"] not in directory_keys]
    fields[:] = [item for item in fields if item["model_key"] not in directory_keys]

    for definition in DIRECTORY_MODELS:
        model_payload = {
            "key": definition["key"],
            "name": definition["name"],
            "group_key": DIRECTORY_GROUP["key"],
            "icon": definition["icon"],
            "source_icon": "",
            "primary_field": definition["primary_field"],
            "sort": len(models) + 1,
            "is_builtin": True,
            "is_locked": True,
            "default_enabled": False,
        }
        model_payload["builtin_hash"] = digest(model_payload)
        models.append(model_payload)

        for field in definition["fields"]:
            data_type = field.get("data_type", "text")
            field_payload = {
                "builtin_key": f"{definition['key']}.{field['field_key']}",
                "model_key": definition["key"],
                "field_key": field["field_key"],
                "source_field_key": field["field_key"],
                "label": field["label"],
                "data_type": data_type,
                "source_data_type": field.get("source_data_type", data_type),
                "field_group": "基本信息",
                "field_group_sort": 1,
                "options": field.get("options", {}),
                "enum_options": field.get("enum_options", []),
                "required": field.get("required", False),
                "unique": field.get("unique", False),
                "editable": True,
                "is_derived": False,
                "default_value": None,
                "user_prompt": "",
                "sort": field["sort"],
                "is_builtin": True,
                "is_locked": True,
                "owner": "product",
            }
            if field.get("referenced_asset_model_id"):
                field_payload["referenced_asset_model_id"] = field[
                    "referenced_asset_model_id"
                ]
            field_payload["builtin_hash"] = digest(field_payload)
            fields.append(field_payload)


def canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def normalized_field_label(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def remove_redundant_fields(fields: list[dict]) -> list[dict]:
    original_order = {id(field): position for position, field in enumerate(fields)}
    fields_by_model: dict[str, list[dict]] = {}
    for field in fields:
        if not normalized_field_label(field.get("label")):
            raise ValueError(f"missing field label: {field.get('builtin_key')}")
        fields_by_model.setdefault(field["model_key"], []).append(field)

    retained = []
    for model_fields in fields_by_model.values():
        fields_by_label: dict[str, list[dict]] = {}
        for field in model_fields:
            fields_by_label.setdefault(
                normalized_field_label(field["label"]), []
            ).append(field)

        for duplicate_fields in fields_by_label.values():
            ordered_fields = sorted(
                duplicate_fields,
                key=lambda field: (
                    bool(field.get("is_derived")),
                    int(field.get("sort") or 0),
                    field["field_key"],
                ),
            )
            canonical = ordered_fields[0]
            if not canonical.get("is_derived"):
                retained.append(canonical)

    retained.sort(key=lambda field: original_order[id(field)])
    for field in retained:
        field.pop("builtin_hash", None)
        field["builtin_hash"] = digest(field)
    fields[:] = retained
    return fields


def load_data(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"invalid snapshot: {path}")
    return payload["data"]


def mapped_icon(group_key: str) -> str:
    if group_key in CLOUD_GROUPS:
        return "ri:node-tree"
    if group_key == "network_device":
        return "ri:router-line"
    if group_key in {"harware", "hardware_components", "fixed_asset", "idc"}:
        return "ri:archive-drawer-line"
    return "ri:database-2-line"


def mapped_field_type(field: dict) -> tuple[str, dict]:
    source_type = field.get("attr_type")
    options = field.get("option")
    if not isinstance(options, (dict, list)):
        options = {}
    if source_type == "str":
        return "text", options
    if source_type == "int":
        return "number", {"number_format": "integer", "source_options": options}
    if source_type == "float":
        return "number", {"number_format": "decimal", "source_options": options}
    if source_type == "time":
        display_format = (
            options.get("display_format") if isinstance(options, dict) else None
        )
        return ("date" if display_format == "date" else "datetime"), options
    if source_type in {"organization", "user"}:
        return "ref", options
    if source_type in {
        "bool",
        "enum",
        "attachment",
        "tag",
    }:
        return source_type, options
    raise ValueError(f"unsupported field type: {source_type}")


def build_catalog(
    classifications_path: Path,
    models_path: Path,
    associations_dir: Path,
) -> dict[str, list[dict]]:
    classifications = [
        item for item in load_data(classifications_path) if item.get("is_pre") is True
    ]
    source_models = [
        item for item in load_data(models_path) if item.get("is_pre") is True
    ]
    group_keys = {item["classification_id"] for item in classifications}
    model_keys = {item["model_id"] for item in source_models}

    groups = []
    for position, item in enumerate(classifications, start=1):
        payload = {
            "key": item["classification_id"],
            "name": item["classification_name"],
            "sort": position,
            "is_builtin": True,
            "is_locked": True,
        }
        payload["builtin_hash"] = digest(payload)
        groups.append(payload)
    directory_group = {
        **DIRECTORY_GROUP,
        "sort": len(groups) + 1,
        "is_builtin": True,
        "is_locked": True,
    }
    directory_group["builtin_hash"] = digest(directory_group)
    groups.append(directory_group)

    models = []
    fields = []
    unique_rules = []
    for model_position, item in enumerate(source_models, start=1):
        group_key = item["classification_id"]
        if group_key not in group_keys:
            raise ValueError(f"missing group for model {item['model_id']}: {group_key}")
        attrs = json.loads(item.get("attrs") or "[]")
        rules = json.loads(item.get("unique_rules") or "[]")
        group_order: dict[str, int] = {}
        field_occurrences: dict[str, int] = {}
        for field_position, field in enumerate(attrs, start=1):
            if field.get("attr_type") == "table":
                continue
            source_field_key = field["attr_id"]
            occurrence = field_occurrences.get(source_field_key, 0) + 1
            field_occurrences[source_field_key] = occurrence
            suffix = f"__{occurrence}"
            field_key = (
                source_field_key
                if occurrence == 1
                else f"{source_field_key[: 64 - len(suffix)]}{suffix}"
            )
            group_name = str(field.get("attr_group") or "其他")
            group_order.setdefault(group_name, len(group_order) + 1)
            data_type, options = mapped_field_type(field)
            enum_options = []
            if data_type == "enum" and isinstance(options, list):
                enum_options = [
                    {
                        "value": option.get("id"),
                        "label": option.get("name") or option.get("id"),
                    }
                    for option in options
                    if isinstance(option, dict) and option.get("id") is not None
                ]
            field_payload = {
                "builtin_key": f"{item['model_id']}.{field_key}",
                "model_key": item["model_id"],
                "field_key": field_key,
                "source_field_key": source_field_key,
                "label": field.get("attr_name") or field["attr_id"],
                "data_type": data_type,
                "source_data_type": field.get("attr_type"),
                "field_group": group_name,
                "field_group_sort": group_order[group_name],
                "options": options,
                "enum_options": enum_options,
                "required": bool(field.get("is_required", False)),
                "unique": bool(field.get("is_only", False)),
                "editable": bool(field.get("editable", True)),
                "is_derived": bool(field.get("is_display_field", False)),
                "default_value": (
                    None
                    if field.get("attr_type") in {"organization", "user"}
                    and field.get("default_value") == []
                    else field.get("default_value")
                ),
                "user_prompt": field.get("user_prompt") or "",
                "sort": field_position,
                "is_builtin": True,
                "is_locked": True,
                "owner": "product",
            }
            if field.get("attr_type") in {"organization", "user"}:
                field_payload["referenced_asset_model_id"] = field["attr_type"]
            field_payload["builtin_hash"] = digest(field_payload)
            fields.append(field_payload)

        model_payload = {
            "key": item["model_id"],
            "name": item["model_name"],
            "group_key": group_key,
            "icon": mapped_icon(group_key),
            "source_icon": item.get("icn") or "",
            "primary_field": "inst_name",
            "sort": model_position,
            "is_builtin": True,
            "is_locked": True,
            "default_enabled": False,
        }
        model_payload["builtin_hash"] = digest(model_payload)
        models.append(model_payload)

        for rule_position, rule in enumerate(rules, start=1):
            rule_payload = {
                "builtin_key": rule.get("rule_id")
                or f"{item['model_id']}.unique.{rule_position}",
                "model_key": item["model_id"],
                "field_keys": list(rule.get("field_ids") or []),
                "sort": int(rule.get("order") or rule_position),
                "is_builtin": True,
                "is_locked": True,
                "owner": "product",
            }
            rule_payload["builtin_hash"] = digest(rule_payload)
            unique_rules.append(rule_payload)

    replace_directory_models(models, fields)

    source_relations: dict[str, dict] = {}
    for path in sorted(associations_dir.glob("*.json")):
        for relation in load_data(path):
            if relation.get("is_pre") is not True:
                continue
            source_relations.setdefault(relation["model_asst_id"], relation)

    model_names = {item["key"]: item["name"] for item in models}
    next_field_sort = {
        model_key: max(
            (
                int(field.get("sort") or 0)
                for field in fields
                if field["model_key"] == model_key
            ),
            default=0,
        )
        for model_key in model_keys
    }
    reference_fields = []
    for relation in sorted(
        source_relations.values(), key=lambda value: value["model_asst_id"]
    ):
        source_key = relation["src_model_id"]
        target_key = relation["dst_model_id"]
        if source_key not in model_keys or target_key not in model_keys:
            raise ValueError(f"unknown relation endpoint: {relation['model_asst_id']}")
        try:
            source_to_target, target_to_source = CARDINALITIES[relation["mapping"]]
        except KeyError as exc:
            raise ValueError(
                f"unsupported relation mapping: {relation['mapping']}"
            ) from exc
        semantic_key = relation["asst_id"]
        relation_key = relation["model_asst_id"]
        next_field_sort[source_key] += 1
        payload = {
            "builtin_key": f"{source_key}.{relation_key}",
            "model_key": source_key,
            "field_key": relation_key,
            "source_field_key": relation_key,
            "label": (
                f"{RELATION_NAMES.get(semantic_key, semantic_key)}"
                f"{model_names[target_key]}"
            ),
            "data_type": "ref",
            "source_data_type": "association",
            "field_group": "关联信息",
            "field_group_sort": 1000,
            "options": {
                "multiple": source_to_target == "many",
                "reverse_unique": target_to_source == "one",
                "semantic_key": semantic_key,
                "derived_from_association": relation_key,
            },
            "enum_options": [],
            "referenced_asset_model_id": target_key,
            "required": False,
            "unique": False,
            "editable": True,
            "is_derived": False,
            "default_value": None,
            "user_prompt": "",
            "sort": next_field_sort[source_key],
            "is_builtin": True,
            "is_locked": True,
            "owner": "product",
        }
        payload["builtin_hash"] = digest(payload)
        reference_fields.append(payload)
    fields.extend(reference_fields)

    icon_aliases = [
        {"source_icon": source_icon, "icon": target_icon}
        for source_icon, target_icon in sorted(
            {
                item["source_icon"]: item["icon"]
                for item in models
                if item["source_icon"]
            }.items()
        )
    ]
    remove_redundant_fields(fields)
    catalog = {
        "groups": groups,
        "models": models,
        "fields": fields,
        "unique_rules": unique_rules,
        "icon_aliases": icon_aliases,
    }
    actual_counts = {key: len(value) for key, value in catalog.items()}
    if actual_counts != EXPECTED_COUNTS:
        raise ValueError(
            f"catalog count mismatch: {actual_counts} != {EXPECTED_COUNTS}"
        )
    return catalog


def write_catalog(catalog: dict[str, list[dict]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, items in catalog.items():
        (output_dir / f"{name}.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    payload_digest = digest(catalog)
    metadata = {
        "catalog_key": CATALOG_KEY,
        "catalog_version": CATALOG_VERSION,
        "source_snapshot_at": "2026-07-23",
        "schema_version": 2,
        "group_count": len(catalog["groups"]),
        "model_count": len(catalog["models"]),
        "field_count": len(catalog["fields"]),
        "unique_rule_count": len(catalog["unique_rules"]),
        "digest": f"sha256:{payload_digest}",
    }
    (output_dir / "catalog.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classifications", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--associations-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = build_catalog(args.classifications, args.models, args.associations_dir)
    write_catalog(catalog, args.output)


if __name__ == "__main__":
    main()
