# coding=utf-8
# author=regulus.y
import traceback

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from sanic.log import logger


async def aproduce(servers, topic, value, key=None, partition=None, max_request_size=1048576):
    async with AIOKafkaProducer(bootstrap_servers=servers.split(','), max_request_size=max_request_size) as producer:
        try:
            _ = await producer.send_and_wait(topic, orjson.dumps(value), key, partition)
        except Exception as err:
            logger.error(err)


async def aconsume(servers, topic, group_id, callback_func=None, consume_records=500):
    async with AIOKafkaConsumer(topic, bootstrap_servers=servers.split(','), group_id=group_id, enable_auto_commit=True,
                                max_poll_interval_ms=60000, value_deserializer=orjson.loads) as consumer:
        while True:
            try:
                msg_set = await consumer.getmany(max_records=int(consume_records))
                for _, msgs in msg_set.items():
                    if callback_func:
                        await callback_func(msgs)
                # await consumer.commit()
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
                # await consumer.commit()
