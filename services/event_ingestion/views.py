# coding=utf-8
import asyncio
import os

import aiofile
import yaml
from aiokafka import AIOKafkaConsumer
from sanic import Blueprint
from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.event import EventsIngestion
from settings import SAMPLE_SINK_PATH, VECTOR_CFG

event_ingestion_bp = Blueprint('event_ingestion', url_prefix='v1/event/ingestion')
_all__ = ["event_ingestion_bp"]

# transforms配置
transforms_config = {
    # 键值对消息案例：id=2022 name=test
    "key-value": r'''parsed = parse_regex!(.message, r'(?P<key_values>(?:\w+=\S+\s*)+)')
                    .parsed = parse_key_value!(parsed.key_values, key_value_delimiter:"=")
                    . = merge(., .parsed)
                    ''',
    # CEF消息案例：CEF:0|security|threatmanager|1.0|100|detected a in message|10|src=10.0.0.1 act=blocked dst=1.1.1.1
    "CEF": r'''   parsed = parse_regex!(.message, r'^CEF:(?P<version>\d+)\|(?P<device_vendor>(?:\\\||[^|])*)\|(?P<device_product>(?:\\\||[^|])*)\|(?P<device_version>(?:\\\||[^|])*)\|(?P<deviceEventClassId>(?:\\\||[^|])*)\|(?P<Name>(?:\\\||[^|])*)\|(?P<Severity>(?:\\\||[^|])*)\|(?P<Extension>.*)$')
                . = merge(., parsed)
                kv_pairs = parse_regex!(parsed.Extension, r'(?P<key_values>(?:\w+=\S+\s*)+)')
                parsed.Extension = parse_key_value!(kv_pairs.key_values, key_value_delimiter:"=")
                . = merge(., parsed.Extension)
                del(.Extension)''',
    "Grok": '''  parsed = parse_regex!(.message, r'({})')
    . = merge(., parsed)'''
}


# 生成配置文件
# noinspection PyTypeChecker
async def build_kafka_file_configuration_file(output_path, sources,
                                              source_id, topic):
    log_path = os.path.join("/data", f"{topic}.log")
    log_path = log_path.replace("\\", "/")
    config = {
        "sources": sources,
        "sinks": {
            "sample_sinks": {
                "type": "file",
                "inputs": [source_id],
                "path": log_path,
                "encoding": {
                    "codec": "json"
                }
            }
        }
    }
    yaml_str = yaml.dump(config, default_flow_style=False, allow_unicode=True)

    async with aiofile.AIOFile(output_path, mode='w', encoding='utf-8') as file:
        await file.write(yaml_str)


async def creat_event_ingestion_dbdata(event_ingestion_name, event_ingestion_describe, event_source_type,
                                       enabled_status, create_id, file_path, client_ip, linked_execution,syslog_config):
    events_ingestion = await EventsIngestion.create(event_ingestion_name=event_ingestion_name,
                                                    event_ingestion_describe=event_ingestion_describe,
                                                    event_source_type=event_source_type, enabled_status=enabled_status,
                                                    created_id=create_id, file_path=file_path, linked_execution=linked_execution,
                                                    syslog_config=syslog_config)
    logger.info(f"Client {client_ip} has added a new role {events_ingestion.id}.")
    return events_ingestion.json


@event_ingestion_bp.post('/')
@requires_permission('event:c')
async def event_ingestion(request):
    """
    创建新的事件接入(需要事件创建权限)
    请求参数：
        event_name: 事件名称 (str) 禁止填写中文
        event_describe: 事件描述 (str)
        event_source_type: 事件来源类型 (str) 目前支持syslog、kafka
        syslog_port: 日志端口 (int) 事件来源类型为syslog时才传，默认为None
        event_source_address: 事件来源地址 (list)  事件来源类型为kafka时才传  默认为None
        communication_type: 通信类型 (str)   事件来源类型为syslog时才传，默认为None tcp/udp
        kafka_group_id: kafka消费组ID (str)   事件来源类型为kafka时才传，默认为None
        kafka_topic: kafka主题 (list)   事件来源类型为kafka时才传，默认为None
        sasl_mechanism: sasl机制 (str)   事件来源类型为kafka时才传，默认为None
        sasl_username: sasl用户名 (str)   事件来源类型为kafka时才传，默认为None
        sasl_password: sasl密码 (str)   事件来源类型为kafka时才传，默认为None
        enabled_status: 上游接入(int) 启用为 1 ，禁用为 0 默认启用
        linked_execution: 联动执行 (int) 启用为 1 ，禁用为 0 默认禁用
        created_id: str 创建人ID
    """
    data = request.json  # 只解析一次
    event_name = data.get('event_name')
    event_describe = data.get('event_describe')
    event_source_type = data.get('event_source_type')
    syslog_port = data.get('syslog_port', None)
    event_source_address = data.get('event_source_address', [])
    communication_type = data.get('communication_type', None)
    kafka_group_id = data.get('kafka_group_id', None)
    kafka_topic = data.get('kafka_topic', [])
    sasl_mechanism = data.get('sasl_mechanism', None)
    sasl_username = data.get('sasl_username', None)
    sasl_password = data.get('sasl_password', None)
    enabled_status = data.get('enabled_status', 1)
    linked_execution = data.get('linked_execution', 0)
    create_id = data.get('created_id')
    client_ip = request.client_ip

    syslog_config = None
    if syslog_port and communication_type:
        syslog_config = communication_type + str(syslog_port)
        existing_ports = await EventsIngestion.filter(deleted=False).values("syslog_config")
        if {"syslog_config": syslog_config} in existing_ports:
            raise CustomError(ErrorCode.DataAlreadyExists)

    if event_name:
        existing_names = await EventsIngestion.filter(deleted=False).values("event_ingestion_name")
        if {"event_ingestion_name": event_name} in existing_names:
            raise CustomError(ErrorCode.DataAlreadyExists)

    output_path = os.path.join(VECTOR_CFG, event_name + ".yaml")

    topic = event_name

    if event_source_type == "syslog":
        sources = {event_name: {
            "type": "syslog",
            "address": f"0.0.0.0:{syslog_port}",
            "mode": communication_type
        }}
        await build_kafka_file_configuration_file(output_path=output_path, sources=sources, source_id=event_name,
                                                  topic=topic)
    elif event_source_type == "kafka":
        sources = {event_name: {
            "type": "kafka",
            "bootstrap_servers": ",".join(event_source_address),
            "group_id": kafka_group_id,
            "topics": [kafka_topic],
            "sasl": {
                "enabled": True,
                "mechanism": sasl_mechanism,
                "username": sasl_username,
                "password": sasl_password
            }}}
        await build_kafka_file_configuration_file(output_path=output_path, sources=sources, source_id=event_name,
                                                  topic=topic)
    else:
        raise CustomError(ErrorCode.ParameterError)

    event_ingestion_dbdata = await creat_event_ingestion_dbdata(event_ingestion_name=event_name,
                                                                event_ingestion_describe=event_describe,
                                                                event_source_type=event_source_type,
                                                                enabled_status=enabled_status,
                                                                create_id=create_id,
                                                                file_path=output_path.replace("\\", "/"),
                                                                client_ip=client_ip,
                                                                linked_execution=linked_execution,
                                                                syslog_config=syslog_config)

    return response_ok(event_ingestion_dbdata)


@event_ingestion_bp.post('/list')
@requires_permission('event:r')
async def get_all_events_ingestion(request):
    """
    获取所有事件接入列表(需要事件查看权限)

    请求参数:
        page: 页码 (int)
        size: 页面大小 (int)
        name: 事件名称 (str)
    """
    page = request.json.get('page', 1)
    size = request.json.get('size', 10)
    offset = (page - 1) * size
    limit = size

    filters = {"deleted": False}
    if request.json.get("name"):
        filters["event_ingestion_name__icontains"] = request.json.get("name")

    events = await EventsIngestion.filter(**filters).offset(offset).limit(limit).values()
    total = await EventsIngestion.filter(**filters).count()

    return response_ok({
        "events": events,
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size
    })


@event_ingestion_bp.post('/delete')
@requires_permission('event:d')
async def delete_event_ingestion(request):
    """
    删除事件接入(需要事件删除权限)

    请求参数:
        id: 事件ID (str)
    """
    event_id = request.json.get('id')
    event = await EventsIngestion.get(id=event_id)
    if not event:
        raise CustomError(ErrorCode.NotFound)
    if event.enabled_status == 1:
        return response_ok("事件已启用，禁止删除，请先禁用事件")

    async with in_transaction():
        os.rename(event.file_path, event.file_path + "_deleted")
        event.deleted = True
        event.file_path = event.file_path + "_deleted"
        await event.save()

    return response_ok("事件删除成功")


@event_ingestion_bp.post('/update')
@requires_permission('event:u')
async def update_event_ingestion(request):
    """
    更新事件接入(需要事件更新权限)

    请求参数:
        id: 事件ID (str)  必填
        event_describe: 事件描述 (str)      非必填
        event_source_address: 事件来源地址 (list) 事件来源类型为kafka时才传     非必填
        syslog_port: 日志端口 (int) 端口范围500-2000  事件来源类型为syslog时才传，默认为None       非必填
        communication_type: 通信类型 (str)   事件来源类型为syslog时才传，默认为None       非必填
        kafka_group_id: kafka消费组ID (str)   事件来源类型为kafka时才传，默认为None      非必填
        transforms_type: 转换类型 (str) 目前就三种Grok、CEF、key-value         非必填
        transforms_config: 转换配置 (str) 只有Grok时需要填写，默认情况下为None       非必填
        sasl_mechanism: sasl机制 (str)   事件来源类型为kafka时才传，默认为None       非必填
        sasl_username: sasl用户名 (str)   事件来源类型为kafka时才传，默认为None       非必填
        sasl_password: sasl密码 (str)   事件来源类型为kafka时才传，默认为None       非必填
        enabled_status: 上游接入(int) 启用为 1 ，禁用为 0      非必填
        linked_execution: 联动执行 (int) 启用为 1 ，禁用为 0      非必填
        script_id: 剧本id (list)  关联剧本，非必填
    """
    data = request.json  # 只解析一次
    event_id = data.get('id')
    event_describe = data.get('event_describe', None)
    syslog_port = data.get('syslog_port', None)
    event_source_address = data.get('event_source_address', [])
    communication_type = data.get('communication_type', None)
    kafka_group_id = data.get('kafka_group_id', None)
    transforms_type = data.get('transforms_type', None)
    transformsconfig = data.get('transforms_config', None)
    sasl_mechanism = data.get('sasl_mechanism', None)
    sasl_username = data.get('sasl_username', None)
    sasl_password = data.get('sasl_password', None)
    enabled_status = data.get('enabled_status', None)
    linked_execution = data.get('linked_execution', None)
    script_id = data.get('script_id', [])
    event = await EventsIngestion.get(id=event_id)

    async with aiofile.AIOFile(event.file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = yaml.safe_load(content)

    if event_source_address:
        data["sources"][event.event_ingestion_name]["bootstrap_servers"] = ",".join(event_source_address)
    if communication_type:
        data["sources"][event.event_ingestion_name]["mode"] = communication_type
    if kafka_group_id:
        data["sources"][event.event_ingestion_name]["group_id"] = kafka_group_id
    if transforms_type:
        data.setdefault("transforms", {}).setdefault(f"{event.event_ingestion_name}_transforms", {})["type"] = "remap"
        data["transforms"][f"{event.event_ingestion_name}_transforms"]["inputs"] = [event.event_ingestion_name]
        if transforms_type == "Grok":
            data["transforms"][f"{event.event_ingestion_name}_transforms"]["source"] = transformsconfig
        else:
            data["transforms"][f"{event.event_ingestion_name}_transforms"]["source"] = transforms_config[
                transforms_type]
    if syslog_port:
        data["sources"][event.event_ingestion_name]["address"] = f"0.0.0.0:{syslog_port}"
    if sasl_mechanism:
        data["sources"][event.event_ingestion_name].setdefault("sasl", {})["enabled"] = True
        data["sources"][event.event_ingestion_name]["sasl"]["mechanism"] = sasl_mechanism
    if sasl_username:
        data["sources"][event.event_ingestion_name]["sasl"]["username"] = sasl_username
    if sasl_password:
        data["sources"][event.event_ingestion_name]["sasl"]["password"] = sasl_password

    data["sinks"][f"{event.event_ingestion_name}_sinks"] = {
        "inputs": [f"{event.event_ingestion_name}_transforms"] if transforms_type else [event.event_ingestion_name],
        "type": "kafka",
        "bootstrap_servers": "lion-kafka:9092",
        "topic": event.event_ingestion_name,
        "encoding": {
            "codec": "json"
        }}
    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    if event_describe:
        event.event_ingestion_describe = event_describe
    if transforms_type:
        event.transforms_type = transforms_type
    if enabled_status in [0,1]:
        file_path = event.file_path
        if enabled_status == 0:
            os.rename(file_path, file_path + "_unenabled")
            event.file_path = file_path + "_unenabled"
        else:
            os.rename(file_path, file_path.replace("_unenabled", ""))
            event.file_path = file_path.replace("_unenabled", "")
        event.enabled_status = enabled_status
    if linked_execution in [0,1]:
        event.linked_execution = linked_execution
    if script_id:
        if event.script_id:
            for script in script_id:
                event.script_id.append(script)
        else:
            event.script_id = script_id

    await event.save()

    return response_ok("事件更新成功")


@event_ingestion_bp.post('/detail')
@requires_permission('event:r')
async def get_event_ingestion_detail(request):
    """
    获取事件接入详情(需要事件查看权限)

    请求参数:
        id: 事件ID (str)
    """
    event_id = request.json.get('id')
    event = await EventsIngestion.get(id=event_id)
    if not event:
        raise CustomError(ErrorCode.NotFound)

    file_path = os.path.abspath(event.file_path)
    if not os.path.exists(file_path):
        raise CustomError(ErrorCode.NotFound)

    async with aiofile.AIOFile(file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = yaml.safe_load(content)

    return response_ok({"config_data": data, "event": event.json})


# noinspection PyTypeChecker
@event_ingestion_bp.post('/finished/sample')
@requires_permission('event:r')
async def get_event_ingestion_kafka_sample(request):
    """
    获取事件接入处理后的日志样例(需要事件查看权限)
    请求参数:
         event_name: 主题名称 (str)
    """
    topic_name = request.json.get('event_name')
    consumer = AIOKafkaConsumer(
        topic_name,
        bootstrap_servers=["lion-kafka:9092"],
        auto_offset_reset="earliest"
    )
    await consumer.start()
    try:
        msg = await asyncio.wait_for(consume_message(consumer), timeout=2.0)
        return response_ok(msg.value.decode("utf-8"))
    except asyncio.TimeoutError:
        return response_ok("Timeout: No message received within 2 seconds")
    finally:
        await consumer.stop()


async def consume_message(consumer):
    async for msg in consumer:
        return msg


# noinspection PyTypeChecker
@event_ingestion_bp.post('/original/sample')
@requires_permission('event:r')
async def get_event_ingestion_syslog_sample(request):
    """
    获取事件接入原始日志样例(需要事件查看权限)
    请求参数:
         event_name: 事件名称 (str)
    """
    event_name = request.json.get('event_name')
    log_path = os.path.join(SAMPLE_SINK_PATH, f"{event_name}.log")
    log_path = log_path.replace("\\", "/")
    async with aiofile.AIOFile(log_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        logs = content.strip().split("\n")
        return response_ok(logs[0])
