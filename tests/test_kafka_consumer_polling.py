import asyncio

import pytest

from libs import kafka


class FakeConsumer:
    instance = None

    def __init__(self, *_args, **_kwargs):
        self.getmany_calls = []
        self.commit_calls = 0
        self.poll_count = 0
        FakeConsumer.instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def getmany(self, **kwargs):
        self.getmany_calls.append(kwargs)
        self.poll_count += 1
        if self.poll_count == 1:
            return {}
        return {"partition-0": ["message-1"]}

    async def commit(self):
        self.commit_calls += 1
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_consumer_waits_on_empty_poll_and_only_commits_nonempty_batch(monkeypatch):
    received = []

    async def callback(messages):
        received.append(messages)

    monkeypatch.setattr(kafka, "AIOKafkaConsumer", FakeConsumer)

    with pytest.raises(asyncio.CancelledError):
        await kafka.aconsume("topic", "group", callback, consume_records=25)

    consumer = FakeConsumer.instance
    assert consumer.getmany_calls == [
        {"timeout_ms": 1000, "max_records": 25},
        {"timeout_ms": 1000, "max_records": 25},
    ]
    assert received == [["message-1"]]
    assert consumer.commit_calls == 1


@pytest.mark.asyncio
async def test_consumer_does_not_commit_failed_callback_batch(monkeypatch):
    class FailedCallbackConsumer(FakeConsumer):
        async def getmany(self, **kwargs):
            self.getmany_calls.append(kwargs)
            self.poll_count += 1
            if self.poll_count > 1:
                raise asyncio.CancelledError
            return {"partition-0": ["message-1"]}

    async def callback(_messages):
        raise RuntimeError("processing failed")

    monkeypatch.setattr(kafka, "AIOKafkaConsumer", FailedCallbackConsumer)

    with pytest.raises(asyncio.CancelledError):
        await kafka.aconsume("topic", "group", callback)

    assert FakeConsumer.instance.commit_calls == 0
