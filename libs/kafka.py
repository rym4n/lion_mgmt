# coding=utf-8
# author=regulus.y
import traceback

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from sanic.log import logger

from settings import KAFKA_CONFIG, TASK_TOPIC


async def _normalize_flow_task_message(value):
    from services.workflow.input_resolution import normalize_flow_task_message

    return await normalize_flow_task_message(value)


async def aproduce(topic, value, key=None, partition=None):
    if (
        topic == TASK_TOPIC
        and isinstance(value, dict)
        and value.get("run_type") == "flow"
        and value.get("timer") != "remove"
    ):
        value = await _normalize_flow_task_message(value)

    try:
        producer_config = dict(KAFKA_CONFIG)
        # The application sends each task once. Kafka may retry internally when
        # an acknowledgement is lost; idempotence prevents that transport retry
        # from creating a second broker record.
        if topic == TASK_TOPIC:
            producer_config["enable_idempotence"] = True
        async with AIOKafkaProducer(**producer_config) as producer:
            _ = await producer.send_and_wait(topic, orjson.dumps(value), key, partition)
    except Exception as err:
        logger.error(f"{err} | {traceback.format_exc()}")


async def aconsume(topic, group_id, callback_func=None, consume_records=500):
    async with AIOKafkaConsumer(
        topic,
        **KAFKA_CONFIG,
        group_id=group_id,
        enable_auto_commit=False,
        max_poll_interval_ms=60000,
        value_deserializer=orjson.loads,
    ) as consumer:
        while True:
            try:
                msg_set = await consumer.getmany(
                    timeout_ms=1000,
                    max_records=int(consume_records),
                )
                if not any(msgs for msgs in msg_set.values()):
                    continue
                for _, msgs in msg_set.items():
                    if callback_func:
                        await callback_func(msgs)
                await consumer.commit()
                # Consume messages
                # async for msg in consumer:
                #     # print(msg.value)
                #     if callback_func:
                #         await callback_func(msg.value)
                #     await consumer.commit()
            except KafkaConnectionError as err1:
                logger.error(err1)
                await consumer.stop()
                await consumer.start()
            except Exception as err2:
                logger.error(f"{err2} | {traceback.format_exc()}")
