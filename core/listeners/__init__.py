from .asset_catalog import AssetCatalogListener
from .base import BaseListener
from .cache import CacheListener
from .command_center import CommandCenterListener
from .event_ingestion import EventIngestionTemplateRepairListener

LISTENER_TUPLE: tuple[type[BaseListener], ...] = (
    CacheListener,
    CommandCenterListener,
    AssetCatalogListener,
    EventIngestionTemplateRepairListener,
)
