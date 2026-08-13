# coding=utf-8

from pathlib import Path


SOURCE = Path("services/ticket/views.py").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    return SOURCE.split(start, 1)[1].split(end, 1)[0]


def test_flow_list_exposes_active_version_and_history_count():
    block = _block(
        "async def flow_list_view(request: Request):",
        '@ticket_bp.post("/flow/export")',
    )

    assert '"version": (' in block
    assert '"version_count": len(' in block
    assert "TicketFlowVersion.filter(" in block
    assert "active_snapshot(versions_by_flow.get(flow.id, []))" in block


def test_flow_package_endpoints_keep_permission_and_tenant_boundaries():
    export_block = _block(
        '@ticket_bp.post("/flow/export")',
        '@ticket_bp.post("/flow/import")',
    )
    import_block = _block(
        '@ticket_bp.post("/flow/import")',
        '@ticket_bp.get("/flow/submit-options")',
    )

    assert '@requires_permission("ticket_flow:r")' in export_block
    assert 'ticket_flow_ids_for_user(account, tenant_id, "r")' in export_block
    assert "build_flow_archive(" in export_block
    assert '@requires_permission("ticket_flow:u")' in import_block
    assert "parse_flow_archive(file_obj.body)" in import_block
    assert "tenant_id=tenant_id" in import_block
    assert "async with in_transaction() as conn:" in import_block
    assert "using_db=conn" in import_block
    assert "status=0 if is_active else 2" in import_block


def test_existing_flow_save_checks_object_permission_and_is_atomic():
    save_block = _block(
        '@ticket_bp.post("/flow/save")',
        '@ticket_bp.post("/flow/publish")',
    )

    assert "if flow_id:" in save_block
    assert "check_ticket_flow_permission(" in save_block
    assert 'request, account, flow_id, ("u",)' in save_block
    assert "async with in_transaction() as conn:" in save_block
    assert "await flow.save(using_db=conn)" in save_block
    assert "await version.save(using_db=conn)" in save_block


def test_flow_detail_uses_status_priority_instead_of_insert_order():
    detail_block = _block(
        '@ticket_bp.get("/flow/detail/<flow_id>")',
        '@ticket_bp.post("/flow/save")',
    )

    assert ').order_by("-ctime").all()' in detail_block
    assert "version = active_snapshot(versions)" in detail_block
