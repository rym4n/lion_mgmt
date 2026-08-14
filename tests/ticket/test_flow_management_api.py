# coding=utf-8

from contextlib import asynccontextmanager
from inspect import unwrap
from types import SimpleNamespace

import orjson
import pytest

import libs.security.authorization as authorization
import models
from models import ticket as ticket_models
from services.ticket import views


class _Query:
    def __init__(self, row):
        self.row = row

    async def first(self):
        return self.row


class _Flow:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.saved = False

    @property
    def json(self):
        return {
            "id": self.id,
            "name": self.name,
            "remark": self.remark,
            "ticket_type_id": self.ticket_type_id,
        }

    async def save(self):
        self.saved = True


class _FlowModel:
    def __init__(self, source, clone=None):
        self.source = source
        self.clone = clone
        self.filter_calls = []
        self.create_calls = []

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        return _Query(self.source)

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.clone


class _VersionModel(_FlowModel):
    pass


class _PermissionModel:
    calls = []

    @classmethod
    async def create(cls, **kwargs):
        cls.calls.append(kwargs)


class _RoleModel:
    @staticmethod
    def filter(**_kwargs):
        return _Query(None)


def _request(payload):
    return SimpleNamespace(
        json=payload,
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "user-1"}),
    )


def _payload(response):
    return orjson.loads(response.body)


@pytest.mark.asyncio
async def test_flow_basic_info_updates_metadata_without_touching_versions(monkeypatch):
    flow = _Flow(
        id="flow-1",
        name="旧名称",
        remark="旧备注",
        ticket_type_id="type-1",
    )
    flow_model = _FlowModel(flow)
    monkeypatch.setattr(views, "TicketFlow", flow_model)

    response = await unwrap(views.flow_basic_info_view)(
        _request({"flow_id": "flow-1", "name": "新名称", "remark": "新备注"})
    )

    assert flow.saved is True
    assert flow.name == "新名称"
    assert flow.remark == "新备注"
    assert flow_model.filter_calls == [
        {"id": "flow-1", "tenant_id": "tenant-1", "deleted": False}
    ]
    assert _payload(response)["data"]["name"] == "新名称"


@pytest.mark.asyncio
async def test_flow_clone_copies_selected_version_and_creator_permission(monkeypatch):
    source_flow = _Flow(
        id="flow-1",
        name="来源流程",
        remark="来源备注",
        ticket_type_id="type-1",
    )
    cloned_flow = _Flow(
        id="flow-2",
        name="克隆流程",
        remark="来源备注",
        ticket_type_id="type-1",
    )
    source_version = SimpleNamespace(
        flow_json={"nodes": [{"id": "start"}]},
        form_schema={"fields": [{"key": "reason"}]},
    )
    cloned_version = SimpleNamespace(json={"id": "version-2", "status": 0})
    flow_model = _FlowModel(source_flow, cloned_flow)
    version_model = _VersionModel(source_version, cloned_version)
    _PermissionModel.calls = []

    @asynccontextmanager
    async def transaction():
        yield "connection"

    async def no_superadmin():
        return None

    monkeypatch.setattr(views, "TicketFlow", flow_model)
    monkeypatch.setattr(views, "TicketFlowVersion", version_model)
    monkeypatch.setattr(views, "in_transaction", transaction)
    monkeypatch.setattr(ticket_models, "TicketFlowPermission", _PermissionModel)
    monkeypatch.setattr(models, "Role", _RoleModel)
    monkeypatch.setattr(authorization, "get_superadmin_role", no_superadmin)

    response = await unwrap(views.flow_clone_view)(
        _request(
            {
                "flow_id": "flow-1",
                "version_id": "version-1",
                "name": "克隆流程",
            }
        )
    )

    assert version_model.filter_calls == [
        {
            "id": "version-1",
            "ticket_flow_id": "flow-1",
            "tenant_id": "tenant-1",
            "deleted": False,
        }
    ]
    version_create = version_model.create_calls[0]
    assert version_create["ticket_flow_id"] == "flow-2"
    assert version_create["flow_json"] == source_version.flow_json
    assert version_create["flow_json"] is not source_version.flow_json
    assert version_create["form_schema"] == source_version.form_schema
    assert version_create["form_schema"] is not source_version.form_schema
    assert _PermissionModel.calls == [
        {
            "using_db": "connection",
            "tenant_id": "tenant-1",
            "flow_id": "flow-2",
            "users_id": "user-1",
            "permissions": ["r", "u", "d", "s"],
        }
    ]
    assert _payload(response)["data"]["version"]["id"] == "version-2"
