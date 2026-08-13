# coding=utf-8
"""Portable ticket-flow package serialization and validation."""

import io
import zipfile

import orjson


PACKAGE_FORMAT = "aioe-ticket-flow"
PACKAGE_VERSION = 1
PACKAGE_MANIFEST = "ticket-flows.json"
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_FLOW_COUNT = 100


def build_flow_archive(flows: list[dict], exported_at: str) -> bytes:
    payload = {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_VERSION,
        "exported_at": exported_at,
        "flows": flows,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            PACKAGE_MANIFEST,
            orjson.dumps(payload, option=orjson.OPT_INDENT_2),
        )
    return buffer.getvalue()


def parse_flow_archive(file_bytes: bytes) -> dict:
    if not file_bytes or len(file_bytes) > MAX_PACKAGE_BYTES:
        raise ValueError("invalid package size")

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            info = archive.getinfo(PACKAGE_MANIFEST)
            if info.file_size > MAX_PACKAGE_BYTES:
                raise ValueError("manifest is too large")
            payload = orjson.loads(archive.read(info))
    except (KeyError, zipfile.BadZipFile, orjson.JSONDecodeError) as exc:
        raise ValueError("invalid ticket-flow package") from exc

    if not isinstance(payload, dict):
        raise ValueError("package manifest must be an object")
    if payload.get("format") != PACKAGE_FORMAT:
        raise ValueError("unsupported package format")
    if payload.get("format_version") != PACKAGE_VERSION:
        raise ValueError("unsupported package version")

    flows = payload.get("flows")
    if not isinstance(flows, list) or not flows or len(flows) > MAX_FLOW_COUNT:
        raise ValueError("invalid flow collection")
    for flow in flows:
        _validate_flow(flow)
    return payload


def _validate_flow(flow: object) -> None:
    if not isinstance(flow, dict):
        raise ValueError("flow must be an object")
    if not isinstance(flow.get("name"), str) or not flow["name"].strip():
        raise ValueError("flow name is required")

    ticket_type = flow.get("ticket_type")
    if not isinstance(ticket_type, dict):
        raise ValueError("ticket type is required")
    if not isinstance(ticket_type.get("name"), str) or not ticket_type["name"].strip():
        raise ValueError("ticket type name is required")
    if not isinstance(ticket_type.get("code"), str) or not ticket_type["code"].strip():
        raise ValueError("ticket type code is required")

    versions = flow.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValueError("flow versions are required")
    for version in versions:
        if not isinstance(version, dict):
            raise ValueError("flow version must be an object")
        if not isinstance(version.get("flow_json"), dict):
            raise ValueError("flow graph must be an object")
        form_schema = version.get("form_schema", {})
        if not isinstance(form_schema, (dict, list)):
            raise ValueError("form schema must be an object or list")


def active_snapshot(versions: list):
    for status in (0, 1, 2):
        snapshot = next(
            (
                version
                for version in versions
                if (
                    version.get("status")
                    if isinstance(version, dict)
                    else getattr(version, "status", None)
                )
                == status
            ),
            None,
        )
        if snapshot is not None:
            return snapshot
    return versions[0] if versions else None
