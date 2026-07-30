"""Business handlers behind the v2 config-sync route entry points."""

import orjson
from sanic import json as sanic_json

import settings
from services.network_topology import snapshot as topology_snapshot


async def sync_topology_snapshot(request, **dependencies):
    """Route adapter for direct collection/v2 config-sync requests."""
    return await topology_snapshot.sync_topology_snapshot(request, **dependencies)


async def issue_topology_manifest(
    request,
    *,
    topology_manifest,
    parse_request,
    request_error_type,
    error_response,
    response_ok,
):
    session = getattr(getattr(request, "ctx", None), "session", None)
    if session and not settings.TOPOLOGY_V2_INGRESS_ENABLED:
        return error_response(
            topology_manifest.ManifestError(topology_manifest.MANIFEST_INGRESS_DISABLED)
        )
    try:
        data = parse_request(request)
    except request_error_type as exc:
        error = topology_manifest.ManifestError(
            topology_manifest.MANIFEST_INVALID_REQUEST
        )
        return error_response(error, status=exc.status)
    try:
        authority = topology_manifest.authority_from_user_session(
            request.ctx.session, data
        )
        result = await topology_manifest.issue_manifest(authority, data)
    except topology_manifest.ManifestError as exc:
        return error_response(exc)
    return response_ok({"target_manifest": result.target_manifest})


async def analysis_status(
    request, *, analysis_runs, tenant_id, error_response, response_ok
):
    try:
        data = analysis_runs.parse_status_request(request.json)
        result = await analysis_runs.get_status(tenant_id(request), data["sync_id"])
    except (ValueError, analysis_runs.AnalysisRunError) as exc:
        error = (
            exc
            if isinstance(exc, analysis_runs.AnalysisRunError)
            else analysis_runs.AnalysisRunError(analysis_runs.RUN_INVALID_REQUEST)
        )
        return error_response(error)
    return response_ok(result)


async def reanalyze_snapshot(
    request, *, analysis_runs, tenant_id, actor_id, error_response
):
    try:
        data = analysis_runs.parse_reanalyze_request(request.json)
        result = await analysis_runs.reanalyze_snapshot(
            tenant_id(request),
            data["snapshot_id"],
            topology_id=data["id"],
            target_profile_revision=data["target_profile_revision"],
            publish_as_current=data["publish_as_current"],
            expected_current_graph_revision=data["expected_current_graph_revision"],
            expected_publication_revision=data["expected_publication_revision"],
            actor=actor_id(request),
        )
    except (ValueError, analysis_runs.AnalysisRunError) as exc:
        error = (
            exc
            if isinstance(exc, analysis_runs.AnalysisRunError)
            else analysis_runs.AnalysisRunError(analysis_runs.RUN_INVALID_REQUEST)
        )
        return error_response(error)
    return sanic_json({"code": 0, "data": result}, status=202, dumps=orjson.dumps)


async def cancel_analysis(
    request, *, analysis_runs, tenant_id, actor_id, error_response, response_ok
):
    try:
        data = analysis_runs.parse_cancel_request(request.json)
        result = await analysis_runs.cancel_analysis(
            tenant_id(request),
            data["sync_id"],
            actor=actor_id(request),
            reason=data["reason"],
        )
    except (ValueError, analysis_runs.AnalysisRunError) as exc:
        error = (
            exc
            if isinstance(exc, analysis_runs.AnalysisRunError)
            else analysis_runs.AnalysisRunError(analysis_runs.RUN_INVALID_REQUEST)
        )
        return error_response(error)
    return response_ok(result)
