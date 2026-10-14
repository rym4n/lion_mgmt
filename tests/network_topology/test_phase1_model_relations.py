"""Relation checks for the retained topology v2 model graph."""

from tortoise import Tortoise
from tortoise.fields.relational import ForeignKeyFieldInstance

import models
from models import network_topology_v2


def test_all_retained_foreign_keys_resolve_to_retained_models() -> None:
    Tortoise.init_models(["models"], "models")
    retained = {
        "NetworkTopology",
        "NetworkTopologyVersion",
        *(name for name in network_topology_v2.__all__ if isinstance(getattr(network_topology_v2, name), type)),
    }

    for model_name in retained:
        model = getattr(models, model_name)
        assert model._meta.app == "models"
        for field in model._meta.fields_map.values():
            if isinstance(field, ForeignKeyFieldInstance):
                assert field.related_model is not None
                assert field.related_model.__name__ in retained


def test_retained_collection_and_analysis_models_have_no_service_binding() -> None:
    for model_name in (
        "TopologyCollectionManifest",
        "TopologyCollectionSnapshot",
        "TopologySnapshotUploadSession",
        "TopologyAnalysisRun",
    ):
        assert "service_binding_row" not in getattr(
            models, model_name
        )._meta.fields_map
