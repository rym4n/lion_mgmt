# coding=utf-8

import inspect

from services.mcp import views as mcp_views
from services.warroom import views as warroom_views


def test_warroom_send_reuses_process_cache_registry():
    source = inspect.getsource(warroom_views.send_message)

    assert "caches.set_config" not in source


def test_mcp_routes_do_not_close_shared_application_redis_client():
    handlers = (
        mcp_views._handle_streamable_http,
        mcp_views._handle_sse_mode,
        mcp_views.handle_messages,
    )

    for handler in handlers:
        assert "redis_client.close" not in inspect.getsource(handler)
