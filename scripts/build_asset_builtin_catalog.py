#!/usr/bin/env python3
"""Build the versioned asset builtin catalog from an exported CMDB snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CATALOG_KEY = "bklite-builtins"
CATALOG_VERSION = "bklite-2026.07-v1"
EXPECTED_COUNTS = {
    "groups": 30,
    "models": 232,
    "fields": 3745,
    "unique_rules": 1,
    "relations": 185,
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
DIRECTORY_MODELS = (
    {
        "key": "organization",
        "name": "组织",
        "fields": (
            ("inst_name", "组织名称", True, True),
            ("path", "组织路径", False, False),
        ),
    },
    {
        "key": "user",
        "name": "用户",
        "fields": (
            ("inst_name", "用户名称", True, True),
            ("username", "登录账号", True, True),
        ),
    },
)


def canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


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
    if source_type == "table":
        if isinstance(options, list):
            return "table", {"schema_mode": "fixed", "columns": options}
        return "table", {"schema_mode": "dynamic", "columns": []}
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
                field_payload["ref_ci_type_id"] = field["attr_type"]
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

    for directory_model in DIRECTORY_MODELS:
        model_payload = {
            "key": directory_model["key"],
            "name": directory_model["name"],
            "group_key": DIRECTORY_GROUP["key"],
            "icon": "ri:contacts-book-line",
            "source_icon": "",
            "primary_field": "inst_name",
            "sort": len(models) + 1,
            "is_builtin": True,
            "is_locked": True,
            "default_enabled": False,
        }
        model_payload["builtin_hash"] = digest(model_payload)
        models.append(model_payload)
        for field_position, (field_key, label, required, unique) in enumerate(
            directory_model["fields"], start=1
        ):
            field_payload = {
                "builtin_key": f"{directory_model['key']}.{field_key}",
                "model_key": directory_model["key"],
                "field_key": field_key,
                "source_field_key": field_key,
                "label": label,
                "data_type": "text",
                "source_data_type": "synthetic",
                "field_group": "基本信息",
                "field_group_sort": 1,
                "options": {
                    "validation_type": "unrestricted",
                    "widget_type": "single_line",
                    "custom_regex": "",
                },
                "enum_options": [],
                "required": required,
                "unique": unique,
                "editable": True,
                "is_derived": False,
                "default_value": None,
                "user_prompt": "",
                "sort": field_position,
                "is_builtin": True,
                "is_locked": True,
                "owner": "product",
            }
            field_payload["builtin_hash"] = digest(field_payload)
            fields.append(field_payload)

    source_relations: dict[str, dict] = {}
    for path in sorted(associations_dir.glob("*.json")):
        for relation in load_data(path):
            if relation.get("is_pre") is not True:
                continue
            source_relations.setdefault(relation["model_asst_id"], relation)

    relations = []
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
        payload = {
            "key": relation["model_asst_id"],
            "name": RELATION_NAMES.get(semantic_key, semantic_key),
            "semantic_key": semantic_key,
            "source_model_keys": [source_key],
            "target_model_keys": [target_key],
            "source_to_target": source_to_target,
            "target_to_source": target_to_source,
            "relation_fields": [],
            "is_builtin": True,
            "is_locked": True,
            "owner": "product",
        }
        payload["builtin_hash"] = digest(payload)
        relations.append(payload)

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
    catalog = {
        "groups": groups,
        "models": models,
        "fields": fields,
        "unique_rules": unique_rules,
        "relations": relations,
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
        "source_snapshot_at": "2026-07-22",
        "schema_version": 1,
        "group_count": len(catalog["groups"]),
        "model_count": len(catalog["models"]),
        "field_count": len(catalog["fields"]),
        "unique_rule_count": len(catalog["unique_rules"]),
        "relation_count": len(catalog["relations"]),
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
