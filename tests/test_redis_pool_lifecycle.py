# coding=utf-8

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from services.mcp import views as mcp_views
from services.warroom import views as warroom_views
from services.network_topology import worker as topology_worker


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


class _FakePubSub:
    def __init__(self):
        self.channels = []
        self.get_message_calls = []
        self.closed = False

    async def subscribe(self, channel):
        self.channels.append(channel)

    async def get_message(self, **kwargs):
        self.get_message_calls.append(kwargs)
        return None

    async def aclose(self):
        self.closed = True


class _FakeRedis:
    def __init__(self):
        self.pubsubs = []

    def pubsub(self):
        pubsub = _FakePubSub()
        self.pubsubs.append(pubsub)
        return pubsub


@pytest.mark.asyncio
async def test_topology_wakeup_reuses_pubsub_and_closes_it():
    redis = _FakeRedis()
    source = topology_worker.RedisWakeupSource(SimpleNamespace(client=redis))
    stop_event = asyncio.Event()

    await source.wait(0.01, stop_event)
    await source.wait(0.01, stop_event)

    assert len(redis.pubsubs) == 1
    assert redis.pubsubs[0].channels == [topology_worker.WAKEUP_CHANNEL]
    assert [call["timeout"] for call in redis.pubsubs[0].get_message_calls] == [1, 0.01, 0.01]
    await source.aclose()
    assert redis.pubsubs[0].closed
