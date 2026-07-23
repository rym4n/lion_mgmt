# coding=utf-8
import asyncio
import datetime
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from core.listeners import AssetCatalogListener, LISTENER_TUPLE
from libs.error_code.exception import CustomError
from services.supply_chain_review import builtin_catalog, manager
from scripts import build_asset_builtin_catalog


class _Query:
    def __init__(self, row=None):
        self.row = row
        self.connection = None

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        return self

    def order_by(self, *_fields):
        return self

    async def first(self):
        if isinstance(self.row, list):
            return self.row[0] if self.row else None
        return self.row

    async def all(self):
        if self.row is None:
            return []
        return self.row if isinstance(self.row, list) else [self.row]

    async def exists(self):
        return bool(self.row)

    async def count(self):
        if self.row is None:
            return 0
        return len(self.row) if isinstance(self.row, list) else 1


class _Record(SimpleNamespace):
    def __init__(self, _json=None, **kwargs):
        super().__init__(**kwargs)
        self._json = _json
        self.save_calls = []

    @property
    def json(self):
        if self._json is not None:
            return dict(self._json)
        return {
            key: value
            for key, value in vars(self).items()
            if key not in {"_json", "save_calls"}
        }

    async def save(self, **kwargs):
        self.save_calls.append(kwargs)


class _Transaction:
    def __init__(self):
        self.connection = object()

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _catalog():
    builtin_catalog.load_builtin_catalog.cache_clear()
    return builtin_catalog.load_builtin_catalog()


def test_runtime_image_copies_builtin_catalog_assets():
    repository = Path(__file__).resolve().parents[1]
    dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "COPY services/supply_chain_review/builtin_catalog/ "
        "./services/supply_chain_review/builtin_catalog/"
    ) in dockerfile
    assert (
        repository
        / "services/supply_chain_review/builtin_catalog/bklite-2026.07-v1/catalog.json"
    ).is_file()


def test_builtin_catalog_has_exact_v1_counts_digest_and_references():
    catalog = _catalog()

    assert catalog["metadata"]["digest"] == (
        "sha256:408a4b533d66bde953e05fd50ba2f05739cc64d7c84f276a42601b89f3ea4736"
    )
    assert {
        name: len(catalog[name]) for name in builtin_catalog.CATALOG_FILES
    } == builtin_catalog.CATALOG_COUNTS
    assert sum(item["is_derived"] for item in catalog["fields"]) == 0
    assert Counter(
        (item["source_to_target"], item["target_to_source"])
        for item in catalog["relations"]
    ) == Counter(builtin_catalog.RELATION_QUANTITY_COUNTS)

    model_keys = {item["key"] for item in catalog["models"]}
    assert "rex-test" not in model_keys
    assert {item["key"] for item in catalog["groups"]}.isdisjoint(
        {"custom", "it-system", "t"}
    )
    assert "自定义模型" not in {item["name"] for item in catalog["groups"]}
    assert {"organization", "user"} <= model_keys
    assert all(
        item["data_type"] not in {"organization", "user", "table"}
        for item in catalog["fields"]
    )
    assert all(item.get("source_data_type") != "table" for item in catalog["fields"])
    assert {
        item.get("ref_ci_type_id")
        for item in catalog["fields"]
        if item.get("source_data_type") in {"organization", "user"}
    } == {"organization", "user"}
    assert all(
        set(item["source_model_keys"] + item["target_model_keys"]) <= model_keys
        for item in catalog["relations"]
    )
    ref_fields = [item for item in catalog["fields"] if item["data_type"] == "ref"]
    assert len(ref_fields) == 311
    assert Counter(item["ref_ci_type_id"] for item in ref_fields) == Counter(
        {"organization": 209, "user": 102}
    )
    field_names = [
        (
            item["model_key"],
            build_asset_builtin_catalog.normalized_field_label(item["label"]),
        )
        for item in catalog["fields"]
    ]
    assert len(field_names) == len(set(field_names))


def test_soc_foundation_models_and_employee_fields_match_product_definitions():
    catalog = _catalog()
    model_definitions = {
        item["key"]: item
        for item in build_asset_builtin_catalog.DIRECTORY_MODELS
        if item["key"] in {"project", "area", "authorized_device", "user", "file_share"}
    }
    models = {item["key"]: item for item in catalog["models"]}

    assert {
        key: (
            models[key]["name"],
            models[key]["group_key"],
            models[key]["primary_field"],
        )
        for key in model_definitions
    } == {
        "project": ("项目", "directory", "name"),
        "area": ("区域", "directory", "name"),
        "authorized_device": ("授权/例外设备", "directory", "device_name"),
        "user": ("员工", "directory", "person_name"),
        "file_share": ("共享文件", "directory", "share_name"),
    }
    assert "personnel" not in models
    assert "用户" not in {item["name"] for item in catalog["models"]}

    fields_by_model = defaultdict(list)
    for field in catalog["fields"]:
        fields_by_model[field["model_key"]].append(field)

    for model_key, definition in model_definitions.items():
        actual = sorted(fields_by_model[model_key], key=lambda field: field["sort"])
        expected = sorted(definition["fields"], key=lambda field: field["sort"])
        assert [
            (
                field["field_key"],
                field["label"],
                field["data_type"],
                field["required"],
                field["unique"],
                field["sort"],
                field["enum_options"],
            )
            for field in actual
        ] == [
            (
                field["field_key"],
                field["label"],
                field.get("data_type", "text"),
                field.get("required", False),
                field.get("unique", False),
                field["sort"],
                field.get("enum_options", []),
            )
            for field in expected
        ]

    assert len(fields_by_model["project"]) == 8
    assert len(fields_by_model["area"]) == 7
    assert len(fields_by_model["authorized_device"]) == 11
    assert len(fields_by_model["user"]) == 13
    assert len(fields_by_model["file_share"]) == 8
    assert {"inst_name", "username"}.isdisjoint(
        {field["field_key"] for field in fields_by_model["user"]}
    )


def test_builtin_catalog_removes_redundant_fields_without_renaming_survivors():
    catalog = _catalog()
    by_source = defaultdict(list)
    for field in catalog["fields"]:
        by_source[(field["model_key"], field["source_field_key"])].append(
            field["field_key"]
        )

    duplicates = {
        identity: field_keys
        for identity, field_keys in by_source.items()
        if len(field_keys) > 1
    }
    assert duplicates == {}
    fields = {field["builtin_key"]: field for field in catalog["fields"]}
    assert fields["office.type"]["label"] == "资产类型"
    assert fields["peripherals.type"]["label"] == "资产类型"
    assert fields["tongweb.version"]["label"] == "版本"
    assert {
        "office.type__2",
        "office.type_display",
        "office.type_display__2",
        "peripherals.type__2",
        "peripherals.type_display",
        "peripherals.type_display__2",
        "tongweb.version__2",
        "k8s_cluster.organization_display",
    }.isdisjoint(fields)


def test_catalog_builder_deletes_duplicate_and_derived_fields():
    fields = [
        {
            "builtin_key": "server.name",
            "model_key": "server",
            "field_key": "name",
            "label": " Host  Name ",
            "sort": 1,
            "is_derived": False,
        },
        {
            "builtin_key": "server.name_copy",
            "model_key": "server",
            "field_key": "name_copy",
            "label": "host name",
            "sort": 2,
            "is_derived": False,
        },
        {
            "builtin_key": "server.name_display",
            "model_key": "server",
            "field_key": "name_display",
            "label": "HOST NAME",
            "sort": 3,
            "is_derived": True,
        },
        {
            "builtin_key": "server.orphan_display",
            "model_key": "server",
            "field_key": "orphan_display",
            "label": "Orphan Display",
            "sort": 4,
            "is_derived": True,
        },
    ]

    build_asset_builtin_catalog.remove_redundant_fields(fields)

    assert [field["label"] for field in fields] == [" Host  Name "]
    assert all(field["builtin_hash"] for field in fields)


def test_catalog_builder_replaces_legacy_user_with_soc_foundation_models():
    models = [
        {"key": "host", "name": "主机"},
        {"key": "user", "name": "用户"},
    ]
    fields = [
        {"model_key": "host", "field_key": "name"},
        {"model_key": "user", "field_key": "inst_name"},
        {"model_key": "user", "field_key": "username"},
    ]

    build_asset_builtin_catalog.replace_directory_models(models, fields)

    model_by_key = {model["key"]: model for model in models}
    assert set(model_by_key) == {
        "host",
        "organization",
        "project",
        "area",
        "authorized_device",
        "user",
        "file_share",
    }
    assert model_by_key["user"]["name"] == "员工"
    assert model_by_key["user"]["primary_field"] == "person_name"
    assert all(model["builtin_hash"] for model in models if model["key"] != "host")
    assert {field["field_key"] for field in fields if field["model_key"] == "user"} == {
        definition["field_key"]
        for definition in next(
            model
            for model in build_asset_builtin_catalog.DIRECTORY_MODELS
            if model["key"] == "user"
        )["fields"]
    }
    assert {"inst_name", "username"}.isdisjoint(
        {field["field_key"] for field in fields if field["model_key"] == "user"}
    )


def test_reference_fields_and_relation_edges_have_distinct_contracts():
    catalog = _catalog()
    ref_pairs = {
        (field["model_key"], field["ref_ci_type_id"])
        for field in catalog["fields"]
        if field["data_type"] == "ref"
    }
    relation_pairs = {
        (source, target)
        for relation in catalog["relations"]
        for source in relation["source_model_keys"]
        for target in relation["target_model_keys"]
    }

    assert len(catalog["relations"]) == 185
    assert len(ref_pairs) == 262
    assert len(relation_pairs) == 160
    assert ref_pairs.isdisjoint(relation_pairs)
    assert ref_pairs.isdisjoint({(target, source) for source, target in relation_pairs})


def test_builtin_catalog_preserves_composite_unique_rule():
    catalog = _catalog()
    assert [
        (item["model_key"], item["field_keys"]) for item in catalog["unique_rules"]
    ] == [("host", ["ip_addr", "cloud"])]


def test_organization_and_user_fields_require_tenant_stable_ids(monkeypatch):
    monkeypatch.setattr(
        manager.Tenant,
        "filter",
        staticmethod(lambda **kwargs: _Query(kwargs.get("id") == "tenant-a")),
    )
    monkeypatch.setattr(
        manager.Account,
        "filter",
        staticmethod(lambda **kwargs: _Query(kwargs.get("id") == "user-a")),
    )

    asyncio.run(
        manager.validate_field_value(
            "tenant-a",
            {"data_type": "organization"},
            "tenant-a",
        )
    )
    asyncio.run(
        manager.validate_field_value(
            "tenant-a",
            {"data_type": "user"},
            "user-a",
        )
    )

    for field, value in (
        ({"data_type": "organization"}, "研发中心"),
        ({"data_type": "user"}, "张三"),
    ):
        with pytest.raises(CustomError) as exc_info:
            asyncio.run(manager.validate_field_value("tenant-a", field, value))
        assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_composite_unique_rule_is_enforced_for_instances(monkeypatch):
    fields = [
        SimpleNamespace(
            field_key="ip_addr",
            data_type="text",
            required=False,
            unique=False,
            default_value=None,
        ),
        SimpleNamespace(
            field_key="cloud",
            data_type="text",
            required=False,
            unique=False,
            default_value=None,
        ),
    ]

    async def fields_for_type(*_args):
        return fields

    monkeypatch.setattr(manager, "fields_for_type", fields_for_type)
    monkeypatch.setattr(
        manager.SupplyChainCi,
        "filter",
        staticmethod(
            lambda **_kwargs: _Query(
                [
                    _Record(
                        id="ci-existing",
                        attributes={"ip_addr": "10.0.0.1", "cloud": "aliyun"},
                    )
                ]
            )
        ),
    )
    monkeypatch.setattr(
        manager.SupplyChainCiUniqueRule,
        "filter",
        staticmethod(
            lambda **_kwargs: _Query([SimpleNamespace(field_keys=["ip_addr", "cloud"])])
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            manager.validate_ci_attributes(
                "tenant-a",
                "host-model-id",
                {"ip_addr": "10.0.0.1", "cloud": "aliyun"},
            )
        )
    assert exc_info.value.enum is ErrorCode.DATA_ALREADY_EXISTS


@pytest.mark.parametrize(
    ("source_to_target", "target_to_source", "expected_filter_keys"),
    [
        ("one", "one", {"source_ci_id", "target_ci_id"}),
        ("many", "one", {"target_ci_id"}),
        ("one", "many", {"source_ci_id"}),
        ("many", "many", set()),
    ],
)
def test_all_four_directional_relation_quantities_are_enforced(
    monkeypatch,
    source_to_target,
    target_to_source,
    expected_filter_keys,
):
    filters = []

    def relation_filter(**kwargs):
        filters.append(kwargs)
        return _Query()

    monkeypatch.setattr(
        manager.SupplyChainCiRelation,
        "filter",
        staticmethod(relation_filter),
    )
    relation_type = SimpleNamespace(
        key="depends_on",
        source_ci_type_ids=["source-model"],
        target_ci_type_ids=["target-model"],
        source_ci_types=["source"],
        target_ci_types=["target"],
        source_to_target=source_to_target,
        target_to_source=target_to_source,
    )
    asyncio.run(
        manager.validate_relation_ends(
            "tenant-a",
            relation_type,
            SimpleNamespace(id="source-ci", ci_type_id="source-model"),
            SimpleNamespace(id="target-ci", ci_type_id="target-model"),
            connection=object(),
        )
    )

    actual_filter_keys = {
        key
        for item in filters
        for key in ("source_ci_id", "target_ci_id")
        if key in item
    }
    assert actual_filter_keys == expected_filter_keys


def test_activation_preview_apply_revision_expiry_and_idempotency(monkeypatch):
    state = _Record(
        catalog_version=builtin_catalog.CATALOG_VERSION,
        revision=4,
        onboarding_completed=False,
        updater=None,
    )
    model = _Record(
        id="model-1",
        key="ecs",
        group_id="group-1",
        group_key="aliyun",
        status="disabled",
        is_builtin=True,
        updater=None,
    )
    activation = _Record(enabled=False, revision=0, updater=None)
    previews = {}
    events = {}

    async def ensure_catalog(*_args):
        return state

    async def resolve_models(*_args, **_kwargs):
        return [model]

    async def activation_impact(*_args, **_kwargs):
        return {
            "affected_model_count": 1,
            "activated_relation_count": 0,
            "dormant_relation_count": 1,
            "existing_instance_count": 2,
            "blocking_reasons": [],
            "cross_group_dependencies": [],
            "model_keys": ["ecs"],
        }

    async def create_preview(**kwargs):
        previews[kwargs["token"]] = _Record(**kwargs)
        return previews[kwargs["token"]]

    async def no_lock(*_args):
        return None

    async def create_event(**kwargs):
        payload = {key: value for key, value in kwargs.items() if key != "using_db"}
        event = _Record(_json=payload, **payload)
        events[event.request_id] = event
        return event

    monkeypatch.setattr(builtin_catalog, "ensure_builtin_catalog", ensure_catalog)
    monkeypatch.setattr(builtin_catalog, "_resolve_activation_models", resolve_models)
    monkeypatch.setattr(builtin_catalog, "_activation_impact", activation_impact)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinActivationPreview,
        "create",
        staticmethod(create_preview),
    )
    monkeypatch.setattr(builtin_catalog, "in_transaction", _Transaction)
    monkeypatch.setattr(builtin_catalog, "_lock_tenant", no_lock)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinActivationEvent,
        "filter",
        staticmethod(lambda **kwargs: _Query(events.get(kwargs["request_id"]))),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinActivationEvent,
        "create",
        staticmethod(create_event),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinActivationPreview,
        "filter",
        staticmethod(lambda **kwargs: _Query(previews.get(kwargs["token"]))),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogState,
        "filter",
        staticmethod(lambda **_kwargs: _Query(state)),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinActivation,
        "filter",
        staticmethod(lambda **_kwargs: _Query(activation)),
    )

    preview = asyncio.run(
        builtin_catalog.preview_builtin_activation(
            "tenant-a",
            {
                "action": "enable",
                "scope": "model",
                "subject_keys": ["ecs", "ecs"],
                "expected_catalog_version": builtin_catalog.CATALOG_VERSION,
            },
            "admin",
        )
    )
    request = {
        "preview_token": preview["preview_token"],
        "request_id": "request-1",
        "expected_tenant_revision": 4,
    }
    first_result = asyncio.run(
        builtin_catalog.apply_builtin_activation("tenant-a", request, "admin")
    )
    second_result = asyncio.run(
        builtin_catalog.apply_builtin_activation("tenant-a", request, "admin")
    )

    assert previews[preview["preview_token"]].subject_keys == ["ecs"]
    assert first_result == second_result
    assert first_result["tenant_revision"] == 5
    assert model.status == "active"
    assert activation.enabled is True
    assert activation.revision == 5
    assert state.revision == 5
    assert state.onboarding_completed is True
    assert previews[preview["preview_token"]].used is True
    assert len(model.save_calls) == 1

    expired = _Record(
        token="expired-token",
        action="disable",
        scope="model",
        subject_keys=["ecs"],
        catalog_version=builtin_catalog.CATALOG_VERSION,
        tenant_revision=5,
        impact={},
        expires_at=datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=1),
        used=False,
    )
    previews[expired.token] = expired
    with pytest.raises(CustomError) as exc_info:
        asyncio.run(
            builtin_catalog.apply_builtin_activation(
                "tenant-a",
                {
                    "preview_token": expired.token,
                    "request_id": "expired-request",
                    "expected_tenant_revision": 5,
                },
                "admin",
            )
        )
    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_onboarding_can_be_completed_without_enabling_models(monkeypatch):
    state = _Record(
        onboarding_completed=False,
        updater=None,
    )

    async def ensure_catalog(*_args):
        return state

    async def no_lock(*_args):
        return None

    monkeypatch.setattr(builtin_catalog, "ensure_builtin_catalog", ensure_catalog)
    monkeypatch.setattr(builtin_catalog, "in_transaction", _Transaction)
    monkeypatch.setattr(builtin_catalog, "_lock_tenant", no_lock)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogState,
        "filter",
        staticmethod(lambda **_kwargs: _Query(state)),
    )

    result = asyncio.run(
        builtin_catalog.complete_builtin_onboarding("tenant-a", "admin")
    )

    assert state.onboarding_completed is True
    assert state.updater == "admin"
    assert result["onboarding_completed"] is True
    assert len(state.save_calls) == 1
    assert state.save_calls[0]["update_fields"] == [
        "onboarding_completed",
        "updater",
        "utime",
    ]


def test_unchanged_catalog_reconcile_is_audited(monkeypatch):
    metadata = _catalog()["metadata"]
    state = _Record(
        catalog_version=builtin_catalog.CATALOG_VERSION,
        catalog_digest=metadata["digest"],
        revision=3,
        status="ready",
        onboarding_completed=True,
    )
    events = []

    async def no_lock(*_args):
        return None

    async def create_event(**kwargs):
        events.append(kwargs)
        return _Record(**kwargs)

    monkeypatch.setattr(builtin_catalog, "in_transaction", _Transaction)
    monkeypatch.setattr(builtin_catalog, "_lock_tenant", no_lock)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogState,
        "filter",
        staticmethod(lambda **_kwargs: _Query(state)),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogEvent,
        "create",
        staticmethod(create_event),
    )

    result = asyncio.run(
        builtin_catalog._reconcile_builtin_catalog_once(
            "tenant-a",
            "admin",
            "reconcile-request",
        )
    )

    assert result["changed"] is False
    assert result["request_id"] == "reconcile-request"
    assert events[0]["status"] == "unchanged"
    assert events[0]["before_state"] == events[0]["after_state"]


def test_ready_catalog_version_change_adds_missing_definitions_without_overwrite(
    monkeypatch,
):
    metadata = _catalog()["metadata"]
    state = _Record(
        catalog_version="older-factory-version",
        catalog_digest="older-digest",
        revision=3,
        status="ready",
        onboarding_completed=True,
        updater="user-a",
    )
    events = []
    calls = []

    async def no_lock(*_args):
        return None

    async def upsert_groups(*_args, preserve_existing=False):
        calls.append(("groups", preserve_existing))
        return {}, 1

    async def upsert_models(*_args, preserve_existing=False):
        calls.append(("models", preserve_existing))
        return {}, 2

    async def upsert_fields(*_args, preserve_existing=False):
        calls.append(("fields", preserve_existing))
        return 4

    async def upsert_rules(*_args, preserve_existing=False):
        calls.append(("unique_rules", preserve_existing))
        return 0

    async def upsert_relations(*_args, preserve_existing=False):
        calls.append(("relations", preserve_existing))
        return 0

    async def ensure_activations(*_args):
        calls.append(("activations", True))
        return 2

    async def create_event(**kwargs):
        events.append(kwargs)
        return _Record(**kwargs)

    monkeypatch.setattr(builtin_catalog, "in_transaction", _Transaction)
    monkeypatch.setattr(builtin_catalog, "_lock_tenant", no_lock)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogState,
        "filter",
        staticmethod(lambda **_kwargs: _Query(state)),
    )
    monkeypatch.setattr(builtin_catalog, "_upsert_builtin_groups", upsert_groups)
    monkeypatch.setattr(builtin_catalog, "_upsert_builtin_models", upsert_models)
    monkeypatch.setattr(builtin_catalog, "_upsert_builtin_fields", upsert_fields)
    monkeypatch.setattr(builtin_catalog, "_upsert_builtin_unique_rules", upsert_rules)
    monkeypatch.setattr(builtin_catalog, "_upsert_builtin_relations", upsert_relations)
    monkeypatch.setattr(builtin_catalog, "_ensure_activations", ensure_activations)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogEvent,
        "create",
        staticmethod(create_event),
    )

    result = asyncio.run(
        builtin_catalog._reconcile_builtin_catalog_once(
            "tenant-a",
            "admin",
            "metadata-upgrade",
        )
    )

    assert result["changed"] is True
    assert result["changes"] == {
        "groups": 1,
        "models": 2,
        "fields": 4,
        "unique_rules": 0,
        "relations": 0,
        "activations": 2,
        "metadata": 1,
    }
    assert calls == [
        ("groups", True),
        ("models", True),
        ("fields", True),
        ("unique_rules", True),
        ("relations", True),
        ("activations", True),
    ]
    assert state.catalog_version == builtin_catalog.CATALOG_VERSION
    assert state.catalog_digest == metadata["digest"]
    assert state.save_calls[0]["update_fields"] == [
        "catalog_version",
        "catalog_digest",
        "status",
        "updater",
        "utime",
    ]
    assert events[0]["changes"] == result["changes"]


def test_additive_catalog_reconcile_preserves_existing_field_edits(monkeypatch):
    definition = next(
        field
        for field in _catalog()["fields"]
        if field["builtin_key"] == "host.inst_name"
    )
    existing = _Record(
        builtin_key=definition["builtin_key"],
        builtin_hash="installed-factory-hash",
        label="租户自定义实例名称",
        deleted=False,
    )
    model = _Record(id="host-model-id", key="host")

    def field_filter(**kwargs):
        return _Query([existing] if kwargs.get("is_builtin") else [])

    monkeypatch.setattr(
        builtin_catalog.SupplyChainCiFieldDef,
        "filter",
        staticmethod(field_filter),
    )

    changes = asyncio.run(
        builtin_catalog._upsert_builtin_fields(
            "tenant-a",
            {"fields": [definition]},
            {"host": model},
            "system",
            object(),
            preserve_existing=True,
        )
    )

    assert changes == 0
    assert existing.label == "租户自定义实例名称"
    assert existing.save_calls == []


def test_product_builtin_group_can_be_renamed_and_deleted(monkeypatch):
    group = _Record(
        id="group-1",
        key="hardware",
        name="硬件",
        sort=1,
        is_builtin=True,
        is_locked=False,
        deleted=False,
        updater=None,
    )

    async def no_lock(*_args):
        return None

    monkeypatch.setattr(builtin_catalog, "in_transaction", _Transaction)
    monkeypatch.setattr(builtin_catalog, "_lock_tenant", no_lock)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainCiModelGroup,
        "filter",
        staticmethod(lambda **_kwargs: _Query(group)),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainCiType,
        "filter",
        staticmethod(lambda **_kwargs: _Query()),
    )

    updated = asyncio.run(
        builtin_catalog.save_model_group(
            "tenant-a",
            {"id": group.id, "key": group.key, "name": "基础硬件"},
            "admin",
        )
    )
    deleted = asyncio.run(builtin_catalog.delete_model_group("tenant-a", group.id))

    assert updated is group
    assert group.name == "基础硬件"
    assert deleted == {"id": group.id}
    assert group.deleted is True
    assert len(group.save_calls) == 2


def test_failed_catalog_reconcile_marks_state_and_records_failure_type(monkeypatch):
    metadata = _catalog()["metadata"]
    state = _Record(
        catalog_version="old-version",
        catalog_digest="old-digest",
        revision=0,
        status="ready",
        onboarding_completed=False,
        updater=None,
    )
    events = []

    async def fail_reconcile(*_args):
        raise RuntimeError("sensitive details must not enter the event")

    async def no_lock(*_args):
        return None

    async def create_event(**kwargs):
        events.append(kwargs)
        return _Record(**kwargs)

    monkeypatch.setattr(
        builtin_catalog,
        "_reconcile_builtin_catalog_once",
        fail_reconcile,
    )
    monkeypatch.setattr(builtin_catalog, "in_transaction", _Transaction)
    monkeypatch.setattr(builtin_catalog, "_lock_tenant", no_lock)
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogState,
        "filter",
        staticmethod(lambda **_kwargs: _Query(state)),
    )
    monkeypatch.setattr(
        builtin_catalog.SupplyChainBuiltinCatalogEvent,
        "create",
        staticmethod(create_event),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            builtin_catalog.reconcile_builtin_catalog(
                "tenant-a",
                "admin",
                "failed-request",
            )
        )

    assert state.catalog_version == builtin_catalog.CATALOG_VERSION
    assert state.catalog_digest == metadata["digest"]
    assert state.status == "provision_failed"
    assert events[0]["status"] == "failed"
    assert events[0]["failure_reason"] == "RuntimeError"
    assert "sensitive details" not in str(events[0])


def test_startup_reconcile_processes_every_tenant_without_stopping_on_failure(
    monkeypatch,
):
    tenants = [SimpleNamespace(id="tenant-a"), SimpleNamespace(id="tenant-b")]

    async def reconcile(tenant, _operator, request_id=None):
        assert request_id
        if tenant == "tenant-b":
            raise RuntimeError("failed tenant")
        return {"changed": True}

    monkeypatch.setattr(
        builtin_catalog.Tenant,
        "filter",
        staticmethod(lambda **_kwargs: _Query(tenants)),
    )
    monkeypatch.setattr(builtin_catalog, "reconcile_builtin_catalog", reconcile)

    result = asyncio.run(builtin_catalog.reconcile_all_tenant_builtin_catalogs())

    assert result == {
        "total": 2,
        "changed": 1,
        "unchanged": 0,
        "failed": [{"tenant_id": "tenant-b", "failure_reason": "RuntimeError"}],
    }
    assert AssetCatalogListener in LISTENER_TUPLE
