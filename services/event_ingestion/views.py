# coding=utf-8
import asyncio
import os
import socket

import aiofile
import aiofiles
import select
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
from settings import event_vector_sinks_bootstrap_servers

event_ingestion_bp = Blueprint('event_ingestion', url_prefix='v1/event/ingestion')
_all__ = ["event_ingestion_bp"]

UPLOAD_FOLDER = "vector_config_files"  # 本地存储目录
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # 确保目录存在

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
async def build_kafka_file_configuration_file(output_path, bootstrap_servers, sources, transforms_type, grok_patterns,
                                              source_id, topic):
    if transforms_type == "Grok":
        source = transforms_config[transforms_type].format(grok_patterns)
    else:
        source = transforms_config[transforms_type]
    config = {
        "sources": sources,
        "transforms": {
            f"{source_id}_transforms": {
                "type": "remap",
                "inputs": [source_id],
                "source": source
            }
        },
        "sinks": {
            f"{source_id}_sinks": {
                "inputs": [f"{source_id}_transforms"],
                "type": "kafka",
                "encoding": {
                    "codec": "json"
                },
                "bootstrap_servers": bootstrap_servers,
                "topic": topic
            }
        }
    }
    yaml_str = yaml.dump(config, default_flow_style=False, allow_unicode=True)

    async with aiofile.AIOFile(output_path, mode='w', encoding='utf-8') as file:
        await file.write(yaml_str)


async def creat_event_ingestion_dbdata(event_ingestion_name, event_ingestion_describe, event_source_type,
                                       transforms_type, enabled_status, release_status, create_id, file_path,
                                       client_ip):
    events_ingestion = await EventsIngestion.create(event_ingestion_name=event_ingestion_name,
                                                    event_ingestion_describe=event_ingestion_describe,
                                                    event_source_type=event_source_type,
                                                    transforms_type=transforms_type, enabled_status=enabled_status,
                                                    release_status=release_status, created_id=create_id,
                                                    file_path=file_path)
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
        event_source_address: 事件来源地址 (list)
        communication_type: 通信类型 (str)   事件来源类型为syslog时才传，默认为None tcp/udp
        kafka_group_id: kafka消费组ID (str)   事件来源类型为kafka时才传，默认为None
        kafka_topic: kafka主题 (list)   事件来源类型为kafka时才传，默认为None
        transforms_type: 转换类型 (str) 目前就三种Grok、CEF、key-value
        transforms_config: 转换配置 (str) 只有Grok时需要填写，默认情况下为None
        enabled_status: 启用状态(int) 启用为 1 ，禁用为 0
        release_status: 发布状态(int) 发布为 1 ，未发布为 0
        created_id: str 创建人ID
    """
    data = request.json  # 只解析一次
    event_name = data.get('event_name')
    event_describe = data.get('event_describe')
    event_source_type = data.get('event_source_type')
    event_source_address = data.get('event_source_address', [])
    communication_type = data.get('communication_type', None)
    kafka_group_id = data.get('kafka_group_id', None)
    kafka_topic = data.get('kafka_topic', [])
    transforms_type = data.get('transforms_type')
    transforms_config = data.get('transforms_config', None)
    enabled_status = data.get('enabled_status')
    release_status = data.get('release_status')
    create_id = data.get('created_id')
    client_ip = request.client_ip

    if enabled_status == 0:
        output_path = os.path.join(UPLOAD_FOLDER, event_name + ".yamlunenabled")  # 临时文件后缀名改为.yamlunenabled
    elif enabled_status == 1:
        output_path = os.path.join(UPLOAD_FOLDER, event_name + ".yaml")  # 正式文件地址.yaml
    else:
        raise CustomError(ErrorCode.ParameterError)

    bootstrap_servers = "lion-kafka:9092"
    topic = event_name

    if event_source_type == "syslog":
        sources = {event_name: {
            "type": "syslog",
            "address": event_source_address[0],
            "mode": communication_type
        }}
        await build_kafka_file_configuration_file(output_path=output_path, bootstrap_servers=bootstrap_servers,
                                                  sources=sources, transforms_type=transforms_type,
                                                  grok_patterns=transforms_config, source_id=event_name, topic=topic)
    elif event_source_type == "kafka":
        sources = {event_name: {
            "type": "kafka",
            "bootstrap_servers": ",".join(event_source_address),
            "group_id": kafka_group_id,
            "topics": kafka_topic
        }}
        await build_kafka_file_configuration_file(output_path=output_path, bootstrap_servers=bootstrap_servers,
                                                  sources=sources,
                                                  transforms_type=transforms_type, source_id=event_name,
                                                  grok_patterns=transforms_config, topic=topic)
    else:
        raise CustomError(ErrorCode.ParameterError)

    event_ingestion_dbdata = await creat_event_ingestion_dbdata(event_ingestion_name=event_name,
                                                                event_ingestion_describe=event_describe,
                                                                event_source_type=event_source_type,
                                                                transforms_type=transforms_type,
                                                                enabled_status=enabled_status,
                                                                release_status=release_status, create_id=create_id,
                                                                file_path=output_path, client_ip=client_ip)

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
    if name := request.json.get("name"):
        filters["event_ingestion_name__icontains"] = name

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
        await aiofiles.os.rename(event.file_path, event.file_path + "deleted")
        event.deleted = True
        event.file_path = event.file_path + "deleted"
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
        event_source_address: 事件来源地址 (list)     非必填
        communication_type: 通信类型 (str)   事件来源类型为syslog时才传，默认为None       非必填
        kafka_group_id: kafka消费组ID (str)   事件来源类型为kafka时才传，默认为None      非必填
        transforms_type: 转换类型 (str) 目前就三种Grok、CEF、key-value         非必填
        transforms_config: 转换配置 (str) 只有Grok时需要填写，默认情况下为None       非必填
        enabled_status: 启用状态(int) 启用为 1 ，禁用为 0      非必填
    """
    data = request.json  # 只解析一次
    event_id = data.get('id')
    event_describe = data.get('event_describe')
    event_source_address = data.get('event_source_address', [])
    communication_type = data.get('communication_type', None)
    kafka_group_id = data.get('kafka_group_id', None)
    transforms_type = data.get('transforms_type')
    transformsconfig = data.get('transforms_config', None)
    enabled_status = data.get('enabled_status')
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
    if transforms_type == "Grok":
        data["transforms"][f"{event.event_ingestion_name}_transforms"]["source"] = transformsconfig
    else:
        data["transforms"][f"{event.event_ingestion_name}_transforms"]["source"] = transforms_config[transforms_type]

    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    if enabled_status == 0:
        await aiofiles.os.rename(event.file_path, event.file_path + "unenabled")
        event.file_path = event.file_path + "unenabled"
    elif enabled_status == 1:
        await aiofiles.os.rename(event.file_path + "unenabled", event.file_path)
        event.file_path = event.file_path.replace("unenabled", "")
    else:
        raise CustomError(ErrorCode.ParameterError)

    if event_describe:
        event.event_ingestion_describe = event_describe
    if transforms_type:
        event.transforms_type = transforms_type
    if enabled_status:
        event.enabled_status = enabled_status
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

    async with aiofile.AIOFile(event.file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = yaml.safe_load(content)

    return response_ok({"config_data:": data, "event": event.json})


@event_ingestion_bp.post('/kafka/sample')
@requires_permission('event:c')
async def get_event_ingestion_kafka_sample(request):
    """
    获取事件接入kafka样例(需要事件创建权限)
    请求参数:
         topic_name: 主题名称 (str)
         kafka_bootstrap_servers: kafka地址 (list)
    """
    topic_name = request.json.get('topic_name')
    kafka_bootstrap_servers = request.json.get('kafka_bootstrap_servers')
    consumer = AIOKafkaConsumer(
        topic_name,
        bootstrap_servers=kafka_bootstrap_servers,
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


@event_ingestion_bp.post('/syslog/sample')
@requires_permission('event:c')
async def get_event_ingestion_syslog_sample(request):
    """
    获取事件接入syslog样例(需要事件创建权限)
    请求参数:
         host: 地址 (str)
         port: 端口 (int)
         communication_type: 通信类型 (str)
    """
    logs = {}
    communication_type = request.json.get('communication_type')
    host = request.json.get('host')
    port = request.json.get('port')
    sample_size = 1
    timeout = 2
    if communication_type.lower() == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        sock.setblocking(False)
    elif communication_type.lower() == "tcp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(5)
        sock.setblocking(False)
        connections = []

    try:
        while len(logs) < sample_size:
            if communication_type.lower() == "udp":
                ready, _, _ = select.select([sock], [], [], timeout)
                if not ready:
                    break
                data, addr = sock.recvfrom(1024)
                log_message = data.decode("utf-8", errors="ignore")
                logs["data"]=log_message
            elif communication_type.lower() == "tcp":
                readable, _, _ = select.select([sock] + connections, [], [], timeout)
                if not readable:
                    break

                for s in readable:
                    if s is sock:  # 有新的连接
                        conn, addr = sock.accept()
                        conn.setblocking(False)
                        connections.append(conn)
                    else:
                        data = s.recv(1024)
                        if not data:  # 连接关闭
                            connections.remove(s)
                            s.close()
                        else:
                            log_message = data.decode("utf-8", errors="ignore")
                            logs["data"] = log_message
    finally:
        sock.close()
        if communication_type.lower() == "tcp":
            for conn in connections:
                conn.close()
    if logs == {}:
        logs["data"]="No message received within 2 seconds"
    return response_ok(logs)
