from .asset_catalog import AssetCatalogListener
from .base import BaseListener
from .cache import CacheListener
from .command_center import CommandCenterListener
from .dashboard import DashboardMaterializedViewListener
from .event_ingestion import EventIngestionTemplateRepairListener
from .policy_collection import PolicyCollectionListener
from .topology_analysis import TopologyAnalysisListener

LISTENER_TUPLE: tuple[type[BaseListener], ...] = (
    CacheListener,
    DashboardMaterializedViewListener,
    CommandCenterListener,
    AssetCatalogListener,
    EventIngestionTemplateRepairListener,
    TopologyAnalysisListener,
    PolicyCollectionListener,
)
