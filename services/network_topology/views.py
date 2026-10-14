from datetime import datetime

from sanic import Blueprint
from sanic.request import Request
from sanic_ext import openapi
from tortoise.expressions import Q, RawSQL
from tortoise.functions import Count
from tortoise.transactions import in_transaction

import settings
from core.response import response_ok
from libs.security.authorization import check_permissions, requires_permission
from models.network_topology import NetworkTopology, NetworkTopologyVersion
from services.network_topology import (
    analysis_runs,
    config_sync_views,
    mutation_views,
    query_views,
    upgrade_views,
)
from services.network_topology.collection import plans as collection_plans
from services.network_topology import compatibility_manager as compatibility
from services.network_topology import manifest as topology_manifest
from services.network_topology.analyzer import analyze_topology
from services.network_topology.graph_helpers import (
    _SNAPSHOT_SCHEMA,  # noqa: F401 - public compatibility constant used by callers/tests
    _config_sync_shape,
    _normalize_graph,
    _normalize_layout,
    _parse_layout_request,
    _parse_pagination,
    _parse_revision,
    _parse_topology_id,
    _parse_topology_name,
    _require_topology_node,
    _source_config,
    _validate_snapshot_shape,
)
from services.network_topology.manifest_request import (
    _analysis_error_response,
    _manifest_error_response,
    _manifest_request_data,
    _ManifestRequestError,
)
from services.network_topology.query_service import enrich_v2_payload
from services.network_topology.view_helpers import (
    _actor_id,
    _build_change_summary,
    _capabilities,  # noqa: F401 - compatibility export used by callers/tests
    _compatibility_error,
    _err,
    _legacy_write_error,
    _name_exists,
    _request_data,
    _tenant_id,
    _tenant_topology,
    _topology_payload,
    _topology_state,
    _v2_view_disabled,
)
from services.network_topology.view_serializers import (
    graph_save_fields,
    layout_response,
    serialize_topology_items,
    serialize_topology_versions,
)

topology_bp = Blueprint("network_topology", url_prefix="/network-topology")
__all__ = ["topology_bp"]

_DEFAULT_TOPOLOGY_GRAPH = compatibility.DEFAULT_TOPOLOGY_GRAPH
_DEFAULT_TOPOLOGY_LAYOUT = compatibility.DEFAULT_TOPOLOGY_LAYOUT

_DYNAMIC_SETTING_NAMES = frozenset(
    {
        "TOPOLOGY_LEGACY_SYNC_ENABLED",
        "TOPOLOGY_V2_ANALYSIS_ENABLED",
        "TOPOLOGY_V2_INGRESS_ENABLED",
        "TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED",
        "TOPOLOGY_V2_PUBLISH_ENABLED",
        "TOPOLOGY_V2_VIEW_ENABLED",
    }
)


def __getattr__(name: str):
    if name in _DYNAMIC_SETTING_NAMES:
        return getattr(settings, name)
    raise AttributeError(name)


async def _append_topology_version(
    connection,
    topology: NetworkTopology,
    *,
    change_type: str,
    before: dict | None,
) -> NetworkTopologyVersion:
    after = _topology_state(topology)
    version = await NetworkTopologyVersion.create(
        using_db=connection,
        topology=topology,
        version=topology.revision,
        name=topology.name,
        topology_json=topology.topology_json or {"nodes": [], "edges": []},
        layout_json=topology.layout_json,
        source_hash=topology.source_hash,
        change_type=change_type,
        change_summary=_build_change_summary(
            before,
            after,
            change_type=change_type,
        ),
        document_schema_version=getattr(topology, "document_schema_version", None),
        tenant_id=topology.tenant_id,
    )
    pointer_fields = compatibility.bind_current_legacy_version(topology, version)
    await topology.save(
        using_db=connection,
        update_fields=[*pointer_fields, "utime"],
    )
    return version


async def _save_graph(request: Request):
    try:
        parsed = _parse_graph_save(request)
    except ValueError as exc:
        return _err(str(exc))
    data, tenant_id, topology_id, name, topology_json, layout_json = parsed
    await check_permissions(
        request, "network_topology:u" if topology_id else "network_topology:c"
    )
    if not topology_id:
        return await _create_saved_graph(request, parsed)
    try:
        expected_revision = _parse_revision(data.get("revision"))
    except ValueError as exc:
        return _err(str(exc))
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        error = await _graph_save_error(
            topology, expected_revision, name, tenant_id, topology_id, connection
        )
        if error:
            return error
        before = _topology_state(topology)
        topology.name = name
        topology.topology_json = topology_json
        topology.layout_json = layout_json
        topology.revision += 1
        revision_fields = compatibility.apply_legacy_write_revision(topology)
        await topology.save(
            using_db=connection,
            update_fields=graph_save_fields(revision_fields),
        )
        await _append_topology_version(
            connection,
            topology,
            change_type="manual_save",
            before=before,
        )
    return response_ok(topology.json)


def _parse_graph_save(request):
    data = _request_data(request)
    tenant_id = _tenant_id(request)
    raw_id = data.get("id") or data.get("topology_id")
    topology_id = _parse_topology_id(data) if raw_id else None
    name = _parse_topology_name(data)
    graph, layout = _normalize_graph(
        data.get("topology_json"), data.get("layout_json")
    )
    _require_topology_node(graph)
    return data, tenant_id, topology_id, name, graph, layout


async def _create_saved_graph(request, parsed):
    _, tenant_id, _, name, graph, layout = parsed
    try:
        result = await compatibility.create_compatible_topology(
            tenant_id=tenant_id,
            name=name,
            created_by=_actor_id(request),
            topology_json=graph,
            layout_json=layout,
        )
    except compatibility.CompatibilityError as exc:
        return _compatibility_error(exc)
    return response_ok(result.topology.json)


async def _graph_save_error(
    topology, revision, name, tenant_id, topology_id, connection
):
    if not topology:
        return _err("拓扑数据不存在", status=404)
    if write_error := _legacy_write_error(topology):
        return write_error
    if topology.revision != revision:
        return _err("拓扑已被更新，请重新打开后保存", status=409)
    if await _name_exists(
        name, tenant_id, exclude_id=topology_id, connection=connection
    ):
        return _err("拓扑名称已存在")
    return None


@topology_bp.post("/manual-save")
@openapi.definition(summary="创建或更新网络拓扑图")
@requires_permission("network_topology:c", "network_topology:u")
async def save_manual_topology(request: Request):
    return await _save_graph(request)


@topology_bp.post("/create")
@openapi.definition(summary="新建网络拓扑")
@requires_permission("network_topology:c")
async def create_topology(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        name = _parse_topology_name(data)
        topology_json = layout_json = None
        if "topology_json" in data or "layout_json" in data:
            topology_json, layout_json = _normalize_graph(
                data.get("topology_json"), data.get("layout_json")
            )
            _require_topology_node(topology_json)
    except ValueError as exc:
        return _err(str(exc))
    try:
        result = await compatibility.create_compatible_topology(
            tenant_id=tenant_id,
            name=name,
            created_by=_actor_id(request),
            topology_json=topology_json,
            layout_json=layout_json,
        )
    except compatibility.CompatibilityError as exc:
        return _compatibility_error(exc)
    return response_ok(result.topology.json)


@topology_bp.post("/update")
@openapi.definition(summary="更新网络拓扑基础数据")
@requires_permission("network_topology:u")
async def update_topology(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        topology_id = _parse_topology_id(data)
        expected_revision = _parse_revision(data.get("revision"))
        name = _parse_topology_name(data, required=False)
    except ValueError as exc:
        return _err(str(exc))

    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        try:
            compatibility.require_legacy_write_allowed(topology)
        except compatibility.CompatibilityError as exc:
            return _compatibility_error(exc)
        if update_error := _metadata_update_error(data, topology):
            return update_error
        if topology.revision != expected_revision:
            return _err("拓扑已被更新，请重新打开后保存", status=409)
        if name and await _name_exists(
            name, tenant_id, exclude_id=topology_id, connection=connection
        ):
            return _err("拓扑名称已存在")

        before = _topology_state(topology)
        update_fields = ["revision", "utime"]
        if name is None:
            return _err("没有可更新的数据")
        topology.name = name
        update_fields.append("name")
        topology.revision += 1
        update_fields.extend(compatibility.apply_legacy_write_revision(topology))
        await topology.save(
            using_db=connection,
            update_fields=list(dict.fromkeys(update_fields)),
        )
        await _append_topology_version(
            connection,
            topology,
            change_type="update",
            before=before,
        )
    return response_ok(topology.json)


def _metadata_update_error(data, topology):
    if "topology_json" in data:
        try:
            graph, _layout = _normalize_graph(
                data["topology_json"],
                data.get("layout_json"),
                fallback_layout=topology.layout_json,
            )
            _require_topology_node(graph)
        except ValueError as exc:
            return _err(str(exc))
    if set(data) - {"id", "revision", "name"}:
        return _err("普通更新只允许修改名称")
    return None


async def _sync_v2_topology_snapshot(
    request, data=None, tenant_id=None, topology_id=None
):
    await check_permissions(request, "network_topology:sync")
    if not settings.TOPOLOGY_V2_INGRESS_ENABLED:
        return _err("v2 拓扑同步入口未启用", status=503)
    try:
        if data is None:
            data = _request_data(request)
            tenant_id = _tenant_id(request)
            topology_id = _parse_topology_id(data)
            _config_sync_shape(data)
        _validate_snapshot_shape(data)
    except ValueError as exc:
        return _err(str(exc))
    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    return await config_sync_views.sync_topology_snapshot(request)


async def _sync_legacy_topology_config(request, data, tenant_id, topology_id):
    await check_permissions(request, "network_topology:u")
    if not settings.TOPOLOGY_LEGACY_SYNC_ENABLED:
        return _err("legacy 拓扑同步入口已关闭", status=503)
    try:
        expected_revision = _parse_revision(data.get("revision"), required=False)
        config_text, source_hash = _source_config(data)
    except ValueError as exc:
        return _err(str(exc))
    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    try:
        compatibility.require_legacy_write_allowed(topology)
    except compatibility.CompatibilityError as exc:
        return _compatibility_error(exc)
    if expected_revision is not None and topology.revision != expected_revision:
        return _err("拓扑已被更新，请重新获取后同步", status=409)
    if topology.source_hash == source_hash and data.get("force") is not True:
        return response_ok({**topology.json, "changed": False, "deprecated": True})
    base_revision = topology.revision
    try:
        analyzed_graph = await analyze_topology(config_text, tenant_id=tenant_id)
        topology_json, layout_json = _normalize_graph(
            analyzed_graph, fallback_layout=topology.layout_json
        )
    except (RuntimeError, ValueError) as exc:
        return _err(f"设备配置解析失败：{exc}", status=422)
    return await _commit_legacy_config_sync(
        tenant_id,
        topology_id,
        base_revision,
        config_text,
        source_hash,
        topology_json,
        layout_json,
    )


async def _commit_legacy_config_sync(
    tenant_id, topology_id, base_revision, config_text, source_hash, graph, layout
):
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        try:
            compatibility.require_legacy_write_allowed(topology)
        except compatibility.CompatibilityError as exc:
            return _compatibility_error(exc)
        if topology.revision != base_revision:
            return _err("拓扑在解析期间已被更新，请重试", status=409)
        before = _topology_state(topology)
        topology.source_config = config_text
        topology.source_hash = source_hash
        topology.topology_json = graph
        topology.layout_json = layout
        topology.last_sync_at = datetime.now(settings.TZ)
        topology.revision += 1
        revision_fields = compatibility.apply_legacy_write_revision(topology)
        await topology.save(
            using_db=connection,
            update_fields=[
                "source_config",
                "source_hash",
                "topology_json",
                "layout_json",
                "last_sync_at",
                "revision",
                *revision_fields,
                "utime",
            ],
        )
        await _append_topology_version(
            connection,
            topology,
            change_type="config_sync",
            before=before,
        )
    return response_ok({**topology.json, "changed": True, "deprecated": True})


@topology_bp.post("/config-sync/manifest")
@requires_permission("network_topology:sync")
async def issue_topology_manifest(request: Request):
    return await config_sync_views.issue_topology_manifest(
        request,
        topology_manifest=topology_manifest,
        parse_request=_manifest_request_data,
        request_error_type=_ManifestRequestError,
        error_response=_manifest_error_response,
        response_ok=response_ok,
    )


@topology_bp.post("/config-sync/status")
@requires_permission("network_topology:sync", "network_topology:reanalyze")
async def analysis_status(request: Request):
    return await config_sync_views.analysis_status(
        request,
        analysis_runs=analysis_runs,
        tenant_id=_tenant_id,
        error_response=_analysis_error_response,
        response_ok=response_ok,
    )


@topology_bp.post("/config-sync/reanalyze")
@requires_permission("network_topology:reanalyze")
async def reanalyze_snapshot(request: Request):
    return await config_sync_views.reanalyze_snapshot(
        request,
        analysis_runs=analysis_runs,
        tenant_id=_tenant_id,
        actor_id=_actor_id,
        error_response=_analysis_error_response,
    )


@topology_bp.post("/config-sync/cancel")
@requires_permission("network_topology:sync", "network_topology:reanalyze")
async def cancel_analysis(request: Request):
    return await config_sync_views.cancel_analysis(
        request,
        analysis_runs=analysis_runs,
        tenant_id=_tenant_id,
        actor_id=_actor_id,
        error_response=_analysis_error_response,
        response_ok=response_ok,
    )


@topology_bp.post("/config-sync")
@openapi.definition(
    summary="写入设备配置并更新网络拓扑",
    description="由剧本在完成设备配置采集后调用；本接口只负责解析、存储和渲染数据。",
)
@requires_permission("network_topology:u", "network_topology:sync")
async def sync_topology_config(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        topology_id = _parse_topology_id(data)
        shape = _config_sync_shape(data)
    except ValueError as exc:
        return _err(str(exc))
    if shape == "snapshot":
        return await _sync_v2_topology_snapshot(request, data, tenant_id, topology_id)
    return await _sync_legacy_topology_config(request, data, tenant_id, topology_id)


@topology_bp.post("/view")
@requires_permission("network_topology:r")
async def view_topology_scope(request: Request):
    return await query_views.view_topology_scope(request)


@topology_bp.post("/search")
@requires_permission("network_topology:r")
async def search_topology(request: Request):
    return await query_views.search_topology(request)


@topology_bp.post("/evidence")
@requires_permission("network_topology:r")
async def get_topology_evidence(request: Request):
    return await query_views.get_topology_evidence(request)


@topology_bp.post("/profile")
@requires_permission("network_topology:profile_admin")
async def save_topology_profile(request: Request):
    return await mutation_views.save_topology_profile(request)


@topology_bp.post("/override")
@requires_permission("network_topology:u")
async def save_topology_override(request: Request):
    return await mutation_views.save_topology_override(request)


@topology_bp.post("/upgrade-to-v2")
@requires_permission("network_topology:u")
async def upgrade_to_v2(request: Request):
    return await upgrade_views.upgrade_to_v2(request)


@topology_bp.post("/list")
@openapi.definition(summary="获取网络拓扑列表")
@requires_permission("network_topology:r")
async def list_topology(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        page, page_size = _parse_pagination(data)
    except ValueError as exc:
        return _err(str(exc))
    total, item_data = await _topology_page(data, tenant_id, page, page_size)
    topology_ids = [item["id"] for item in item_data]
    version_count_by_topology = {}
    if topology_ids:
        version_counts = await (
            NetworkTopologyVersion.filter(
                topology_id__in=topology_ids,
                deleted=False,
                tenant_id=tenant_id,
            )
            .annotate(version_count=Count("id"))
            .group_by("topology_id")
            .values("topology_id", "version_count")
        )
        version_count_by_topology = {
            item["topology_id"]: item["version_count"] for item in version_counts
        }
    serialize_topology_items(item_data, version_count_by_topology)
    return response_ok(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": item_data,
        }
    )


async def _topology_page(data, tenant_id, page, page_size):
    queryset = NetworkTopology.filter(deleted=False, tenant_id=tenant_id)
    if not settings.TOPOLOGY_V2_VIEW_ENABLED:
        queryset = queryset.filter(
            Q(document_schema_version__isnull=True)
            | ~Q(document_schema_version=compatibility.V2_SCHEMA_VERSION)
        )
    name = str(data.get("name") or "").strip()
    if name:
        queryset = queryset.filter(name__icontains=name)
    total = await queryset.count()
    items = await (
        queryset.annotate(
            node_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"network_topology\".\"topology_json\"->'nodes') = 'array' "
                'THEN jsonb_array_length("network_topology"."topology_json"->\'nodes\') ELSE 0 END'
            ),
            edge_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"network_topology\".\"topology_json\"->'edges') = 'array' "
                'THEN jsonb_array_length("network_topology"."topology_json"->\'edges\') ELSE 0 END'
            ),
        )
        .order_by("-utime", "-ctime")
        .offset((page - 1) * page_size)
        .limit(page_size)
        .values(
            "id",
            "name",
            "topology_json",
            "node_count",
            "edge_count",
            "revision",
            "document_schema_version",
            "generation_source",
            "last_sync_at",
            "ctime",
            "utime",
        )
    )
    collection_by_topology = await collection_plans.collection_summaries(
        tenant_id, [item["id"] for item in items]
    )
    for item in items:
        item["collection"] = collection_by_topology.get(item["id"])
    return total, items


@topology_bp.post("/detail")
@openapi.definition(summary="获取网络拓扑详情")
@requires_permission("network_topology:r")
async def get_topology(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
    except ValueError as exc:
        return _err(str(exc))
    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    if _v2_view_disabled(topology):
        return _err("v2 拓扑查看入口未启用", status=503)
    payload = _topology_payload(topology)
    if topology.document_schema_version == compatibility.V2_SCHEMA_VERSION:
        payload = enrich_v2_payload(payload, topology.topology_json or {})
    return response_ok(payload)


@topology_bp.post("/versions")
@openapi.definition(summary="获取网络拓扑版本列表")
@requires_permission("network_topology:r")
async def list_topology_versions(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
    except ValueError as exc:
        return _err(str(exc))
    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    if _v2_view_disabled(topology):
        return _err("v2 拓扑查看入口未启用", status=503)

    versions = await (
        NetworkTopologyVersion.filter(
            topology_id=topology_id,
            deleted=False,
            tenant_id=tenant_id,
        )
        .annotate(
            node_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"topology_json\"->'nodes') = 'array' "
                "THEN jsonb_array_length(\"topology_json\"->'nodes') ELSE 0 END"
            ),
            edge_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"topology_json\"->'edges') = 'array' "
                "THEN jsonb_array_length(\"topology_json\"->'edges') ELSE 0 END"
            ),
        )
        .order_by("-version")
        .values(
            "id",
            "topology_id",
            "version",
            "topology_json",
            "document_schema_version",
            "change_type",
            "change_summary",
            "node_count",
            "edge_count",
            "ctime",
        )
    )
    serialize_topology_versions(versions)
    return response_ok(versions)


@topology_bp.post("/version-detail")
@openapi.definition(summary="获取网络拓扑版本详情")
@requires_permission("network_topology:r")
async def get_topology_version_detail(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
        version_id = data.get("version_id")
        if not isinstance(version_id, str) or not version_id.strip():
            raise ValueError("拓扑版本ID不能为空")
        version_id = version_id.strip()
    except ValueError as exc:
        return _err(str(exc))
    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    if _v2_view_disabled(topology):
        return _err("v2 拓扑查看入口未启用", status=503)

    version = await NetworkTopologyVersion.filter(
        id=version_id,
        topology_id=topology_id,
        deleted=False,
        tenant_id=tenant_id,
    ).first()
    if not version:
        return _err("拓扑版本不存在", status=404)
    payload = version.json
    if getattr(version, "document_schema_version", None) == compatibility.V2_SCHEMA_VERSION:
        payload = enrich_v2_payload(payload, version.topology_json or {})
    return response_ok(payload)


@topology_bp.post("/layout")
@openapi.definition(summary="保存网络拓扑节点布局")
@requires_permission("network_topology:u")
async def update_layout(request: Request):
    data = request.json or {}
    if mutation_views.is_v2_layout_request(data):
        return await mutation_views.save_topology_layout(request)
    try:
        data, topology_id, tenant_id, expected_revision = _parse_layout_request(request)
    except ValueError as exc:
        return _err(str(exc))
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if error := _layout_write_error(data, topology, expected_revision):
            return error
        before = _topology_state(topology)
        topology.revision += 1
        revision_fields = compatibility.apply_legacy_write_revision(topology)
        await topology.save(
            using_db=connection,
            update_fields=[
                "layout_json",
                "revision",
                *revision_fields,
                "utime",
            ],
        )
        await _append_topology_version(
            connection,
            topology,
            change_type="layout",
            before=before,
        )
    return response_ok(layout_response(topology))


def _layout_write_error(data, topology, expected_revision):
    if not topology:
        return _err("拓扑数据不存在", status=404)
    if write_error := _legacy_write_error(topology):
        return write_error
    if expected_revision is not None and topology.revision != expected_revision:
        return _err("拓扑已被更新，请重新打开后保存布局", status=409)
    if "source_hash" in data and (data.get("source_hash") or "") != (
        topology.source_hash or ""
    ):
        return _err("拓扑配置已更新，请重新打开后保存布局", status=409)
    try:
        topology.layout_json = _normalize_layout(
            data["layout_json"], topology.topology_json or {}
        )
    except ValueError as exc:
        return _err(str(exc))
    return None


@topology_bp.post("/delete")
@openapi.definition(summary="删除网络拓扑")
@requires_permission("network_topology:d")
async def delete_topology(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
    except ValueError as exc:
        return _err(str(exc))
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        topology.deleted = True
        topology.revision += 1
        await topology.save(
            using_db=connection,
            update_fields=["deleted", "revision", "utime"],
        )
    await collection_plans.delete_plan_for_topology(tenant_id, topology_id)
    return response_ok({"id": topology.id})
