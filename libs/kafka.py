# coding=utf-8
# author=regulus.y

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from sanic.log import logger


async def aproduce(servers, topic, value, key=None, partition=None, max_request_size=1048576):
    producer = AIOKafkaProducer(bootstrap_servers=servers.split(','), max_request_size=max_request_size)
    await producer.start()
    try:
        _ = await producer.send_and_wait(topic, orjson.dumps(value), key, partition)
    except Exception as err:
        logger.opt(exception=True).error(err)
    finally:
        await producer.stop()


def deserializer(value):
    try:
        return orjson.loads(value.decode())
    except Exception as e:
        logger.error(e)


async def aconsume(servers, topic, group_id, callback_func=None, consume_records=500):
    consumer = AIOKafkaConsumer(topic, bootstrap_servers=servers.split(','), group_id=group_id,
                                enable_auto_commit=False, max_poll_interval_ms=60000,  # max_poll_records=50,
                                value_deserializer=deserializer)
    await consumer.start()
    try:
        while True:
            try:
                msg_set = await consumer.getmany(max_records=int(consume_records))
                for _, msgs in msg_set.items():
                    for msg in msgs:
                        # print(msg.value)
                        if callback_func:
                            await callback_func(msg.value)
                await consumer.commit()
                # Consume messages
                # async for msg in consumer:
                #     # print(msg.value)
                #     if callback_func:
                #         await callback_func(msg.value)
                #     await consumer.commit()
            except KafkaConnectionError as err1:
                logger.opt(exception=True).error(err1)
                await consumer.stop()
                await consumer.start()
            except Exception as err2:
                logger.opt(exception=True).error(err2)
                await consumer.commit()
    finally:
        # Will leave consumer group; perform autocommit if enabled.
        await consumer.stop()
