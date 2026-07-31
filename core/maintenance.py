# coding=utf-8

DEFAULT_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"


def can_enable_maintenance_mode(username, tenant_id):
    return username == "admin" and str(tenant_id or "") == DEFAULT_TENANT_ID


def should_clear_maintenance_mode(tenant_id):
    return str(tenant_id or "") != DEFAULT_TENANT_ID


def can_switch_tenant(maintenance_mode):
    return not bool(maintenance_mode)
