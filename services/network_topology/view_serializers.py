"""Small response serializers shared by topology route handlers."""

from libs.utils import strip_timezone_to_seconds
from services.network_topology import compatibility_manager as compatibility
from services.network_topology.query_service import graph_statistics
from services.network_topology.view_helpers import (
    _capabilities,
    _version_generation_source,
)


def graph_save_fields(revision_fields):
    return [
        "name",
        "topology_json",
        "layout_json",
        "revision",
        *revision_fields,
        "utime",
    ]


def serialize_topology_items(items, version_counts):
    for item in items:
        graph = item.pop("topology_json", {})
        if item.get("document_schema_version") == compatibility.V2_SCHEMA_VERSION:
            item.update(graph_statistics(graph))
        item["version_count"] = version_counts.get(item["id"], 0)
        item["capabilities"] = _capabilities(item)
        for field in ("last_sync_at", "ctime", "utime"):
            item[field] = strip_timezone_to_seconds(item.get(field))


def serialize_topology_versions(versions):
    for item in versions:
        graph = item.pop("topology_json", {})
        if item.get("document_schema_version") == compatibility.V2_SCHEMA_VERSION:
            item.update(graph_statistics(graph))
        item["version_id"] = item["id"]
        item["generation_source"] = _version_generation_source(
            item.get("document_schema_version")
        )
        item["ctime"] = strip_timezone_to_seconds(item.get("ctime"))


def layout_response(topology):
    return {
        "id": topology.id,
        "revision": topology.revision,
        "source_hash": topology.source_hash,
    }
