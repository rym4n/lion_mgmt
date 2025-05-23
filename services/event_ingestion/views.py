# coding=utf-8
import ast
import hashlib
import io
import os
import random
import re
import string
import time
import zipfile

import aiofile
import orjson
import yaml
from sanic import Blueprint, response
from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import get_client_ip, fetch_latest_messages
from models.event import EventsIngestion, EventRelatedScript
from models.playbook import Playbook
from services.event_ingestion import supervisor
from settings import VECTOR_CFG, KAFKA_CONFIG

event_ingestion_bp = Blueprint('event_ingestion', url_prefix='v1/event/ingestion')
_all__ = ["event_ingestion_bp"]

# transforms配置
transforms_config = {
    # 键值对消息案例：id=2022 name=test
    "key-value": r'''
parsed = split(string!(.message), "{}")
parsed = join!(parsed, " ")
parsed = parse_key_value!(parsed, key_value_delimiter: "{}")
. = merge(., parsed)
{}''',
    # CEF消息案例：CEF:0|security|threatmanager|1.0|100|detected a in message|10|src=10.0.0.1 act=blocked dst=1.1.1.1
    "CEF": r'''
parsed = parse_regex!(.message, r'^CEF:(?P<version>\d+)\|(?P<device_vendor>(?:\\\||[^|])*)\|(?P<device_product>(?:\\\||[^|])*)\|(?P<device_version>(?:\\\||[^|])*)\|(?P<deviceEventClassId>(?:\\\||[^|])*)\|(?P<Name>(?:\\\||[^|])*)\|(?P<Severity>(?:\\\||[^|])*)\|(?P<Extension>.*)$')
. = merge!(., parsed)
kv_pairs = parse_regex!(parsed.Extension, r'(?P<key_values>(?:\w+=\S+\s*)+)')
parsed.Extension = parse_key_value!(kv_pairs.key_values, key_value_delimiter:"=")
. = merge!(., parsed.Extension)
del(.Extension)
{}''',
    "Grok": '''
parsed = parse_regex!(.message, r'({})')
. = merge!(., parsed)
{}''',
    "JSON": '''
message_str = to_string!(.message)
parsed = parse_json!(message_str)
. = merge!(., parsed)
{}''',
    "Delimiter": '''
parsed = split(string!(.message), "{}")
.message = parsed
{}'''
}

clear_data_config = {
    "syslog": r'''
.datetime_str = format_timestamp(now(), "%Y%m%d%H%M%S", "Asia/Shanghai") ?? "19700101000000"
del(.host)
del(.port)
del(.source_type)
del(.timestamp)
''',
    "kafka": r'''
.datetime_str = format_timestamp(now(), "%Y%m%d%H%M%S", "Asia/Shanghai") ?? "19700101000000"
del(.headers)
del(.message_key)
del(.offset)
del(.partition)
del(.source_type)
del(.timestamp)
del(.topic)
            ''',
}

CEF_REGEX = re.compile(
    r"^CEF:(?P<version>\d+)\|(?P<device_vendor>(?:\\\||[^|])*)\|"
    r"(?P<device_product>(?:\\\||[^|])*)\|(?P<device_version>(?:\\\||[^|])*)\|"
    r"(?P<deviceEventClassId>(?:\\\||[^|])*)\|(?P<Name>(?:\\\||[^|])*)\|"
    r"(?P<Severity>(?:\\\||[^|])*)\|(?P<Extension>.*)$"
)


# 生成配置文件
# noinspection PyTypeChecker
async def build_kafka_file_configuration_file(output_path, sources,
                                              source_id, topic):
    config = {
        "sources": sources,
        "sinks": {
            f"{topic}_sample_sinks": {
                "type": "kafka",
                "inputs": [source_id],
                "bootstrap_servers": KAFKA_CONFIG,
                "topic": topic,
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
                                       enabled_status, create, updator, file_path, client_ip, linked_execution,
                                       syslog_config, vector_name):
    events_ingestion = await EventsIngestion.create(event_ingestion_name=event_ingestion_name,
                                                    event_ingestion_describe=event_ingestion_describe,
                                                    event_source_type=event_source_type, enabled_status=enabled_status,
                                                    creator=create, updator=updator, file_path=file_path,
                                                    linked_execution=linked_execution,
                                                    syslog_config=syslog_config, vector_name=vector_name)
    logger.info(f"Client {client_ip} has added a new role {events_ingestion.id}.")
    return events_ingestion.json


@event_ingestion_bp.post('/')
@requires_permission('event:c')
async def event_ingestion(request):
    """
    创建新的事件接入(需要事件创建权限)
    请求参数：
        event_name: 事件名称 (str)
        event_describe: 事件描述 (str)
        event_source_type: 事件来源类型 (str) 目前支持kafka、socket
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
    client_ip = get_client_ip(request)

    syslog_config = None
    if syslog_port and communication_type:
        syslog_config = communication_type + str(syslog_port)
        existing_ports = await EventsIngestion.filter(deleted=False).values("syslog_config")
        if {"syslog_config": syslog_config} in existing_ports:
            raise CustomError(ErrorCode.DataAlreadyExists)

    letters = string.ascii_letters  # 包含 a-zA-Z
    vector_name = ''.join(random.choices(letters, k=12))

    if vector_name:
        vector_names = await EventsIngestion.filter(deleted=False).values("vector_name")
        if {"vector_name": vector_name} in vector_names:
            raise CustomError(ErrorCode.DataAlreadyExists)

    output_path = os.path.join(VECTOR_CFG, vector_name + ".yaml")

    topic = vector_name

    if event_source_type in ["syslog", "socket"]:
        sources = {vector_name: {
            "type": event_source_type,
            "address": f"0.0.0.0:{syslog_port}",
            "mode": communication_type
        }}
        await build_kafka_file_configuration_file(output_path=output_path, sources=sources, source_id=vector_name,
                                                  topic=topic)
        event_source_type = "syslog"
    elif event_source_type == "kafka":
        if sasl_mechanism:
            sources = {vector_name: {
                "type": "kafka",
                "bootstrap_servers": ",".join(event_source_address),
                "group_id": kafka_group_id,
                "topics": kafka_topic,
                "sasl": {
                    "enabled": True,
                    "mechanism": sasl_mechanism,
                    "username": sasl_username,
                    "password": sasl_password
                }}}
        else:
            sources = {vector_name: {
                "type": "kafka",
                "bootstrap_servers": ",".join(event_source_address),
                "group_id": kafka_group_id,
                "topics": kafka_topic,
            }}
        await build_kafka_file_configuration_file(output_path=output_path, sources=sources, source_id=vector_name,
                                                  topic=topic)
    else:
        raise CustomError(ErrorCode.ParameterError)

    event_ingestion_dbdata = await creat_event_ingestion_dbdata(event_ingestion_name=event_name,
                                                                event_ingestion_describe=event_describe,
                                                                event_source_type=event_source_type,
                                                                enabled_status=enabled_status,
                                                                create=request.ctx.session['account_info'],
                                                                file_path=output_path.replace("\\", "/"),
                                                                client_ip=client_ip,
                                                                updator=request.ctx.session['account_info'],
                                                                linked_execution=linked_execution,
                                                                syslog_config=syslog_config,
                                                                vector_name=vector_name)

    return response_ok(event_ingestion_dbdata)


@event_ingestion_bp.post('/list')
@requires_permission('event:r')
async def get_all_events_ingestion(request):
    """
    获取所有事件接入列表(需要事件查看权限)

    请求参数:
        page: 页码 (int)
        size: 页面大小 (int)
        name: 事件名称 (str) 非必填
    """
    page = request.json.get('page', 1)
    size = request.json.get('size', 10)
    offset = (page - 1) * size
    limit = size

    filters = {"deleted": False}
    if request.json.get("name"):
        filters["event_ingestion_name__icontains"] = request.json.get("name")

    events = await EventsIngestion.filter(**filters).offset(offset).select_related("creator", "updator").limit(
        limit).all()
    total = await EventsIngestion.filter(**filters).count()

    return response_ok({
        "events": [event.json for event in events],
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
        event.updator = request.ctx.session["account_info"]
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
        transforms_type: 转换类型 (str) 目前支持Grok、CEF、key-value、JSON、Delimiter(分割符)       非必填
        transforms_config: 转换配置 (str) 只有Grok时需要填写，默认情况下为None       非必填
        key_value_delimiter: 键值对分隔符 (str) 只有key-value时需要填写，默认情况下为None       非必填
        key_delimiter_value: 键值分隔符 (str) 只有key-value时需要填写，默认情况下为None       非必填
        sasl_mechanism: sasl机制 (str)   事件来源类型为kafka时才传，默认为None       非必填
        sasl_username: sasl用户名 (str)   事件来源类型为kafka时才传，默认为None       非必填
        sasl_password: sasl密码 (str)   事件来源类型为kafka时才传，默认为None       非必填
        enabled_status: 上游接入(int) 启用为 1 ，禁用为 0      非必填
        linked_execution: 联动执行 (int) 启用为 1 ，禁用为 0      非必填
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
    key_delimiter_value = data.get('key_delimiter_value', None)
    key_value_delimiter = data.get('key_value_delimiter', None)
    event = await EventsIngestion.get(id=event_id)

    async with aiofile.AIOFile(event.file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = yaml.safe_load(content)

    if event_source_address:
        data["sources"][event.vector_name]["bootstrap_servers"] = ",".join(event_source_address)
    if communication_type:
        data["sources"][event.vector_name]["mode"] = communication_type
    if kafka_group_id:
        data["sources"][event.vector_name]["group_id"] = kafka_group_id
    if transforms_type:
        data["sinks"][f"{event.vector_name}_sample_sinks"]["inputs"] = [
            f"{event.vector_name}_transforms"]
        data.setdefault("transforms", {}).setdefault(f"{event.vector_name}_transforms", {})["type"] = "remap"
        data["transforms"][f"{event.vector_name}_transforms"]["inputs"] = [event.vector_name]
        data["transforms"][f"{event.vector_name}_transforms"]["drop_on_error"] = False
        if transforms_type in ["Grok", "Delimiter"]:
            data["transforms"][f"{event.vector_name}_transforms"]["source"] = transforms_config[transforms_type].format(
                transformsconfig, clear_data_config[event.event_source_type])
        elif transforms_type == "key-value":
            data["transforms"][f"{event.vector_name}_transforms"]["source"] = transforms_config[transforms_type].format(
                key_value_delimiter, key_delimiter_value, clear_data_config[event.event_source_type])
        else:
            data["transforms"][f"{event.vector_name}_transforms"]["source"] = transforms_config[transforms_type].format(
                clear_data_config[event.event_source_type])
    if syslog_port:
        data["sources"][event.vector_name]["address"] = f"0.0.0.0:{syslog_port}"
        event.syslog_config = event.syslog_config[:3] + str(syslog_port)
    if sasl_mechanism:
        data["sources"][event.vector_name].setdefault("sasl", {})["enabled"] = True
        data["sources"][event.vector_name]["sasl"]["mechanism"] = sasl_mechanism
    if sasl_username:
        data["sources"][event.vector_name]["sasl"]["username"] = sasl_username
    if sasl_password:
        data["sources"][event.vector_name]["sasl"]["password"] = sasl_password

    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    if event_describe:
        event.event_ingestion_describe = event_describe
    if transforms_type:
        event.transforms_type = transforms_type
    if enabled_status in [0, 1]:
        file_path = event.file_path
        if enabled_status == 0:
            os.rename(file_path, file_path + "_unenabled")
            event.file_path = file_path + "_unenabled"
        else:
            os.rename(file_path, file_path.replace("_unenabled", ""))
            event.file_path = file_path.replace("_unenabled", "")
        event.enabled_status = enabled_status
    if linked_execution in [0, 1]:
        if linked_execution == 0:
            event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_id).all()
            for event_related_script in event_related_scripts:
                for script_id in event_related_script.script_id:
                    process_name = hashlib.md5(f"{event_id}_{script_id}".encode()).hexdigest()
                    supervisor.stop_process(process_name)
        else:
            event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_id).all()
            for event_related_script in event_related_scripts:
                for script_id in event_related_script.script_id:
                    process_name = hashlib.md5(f"{event_id}_{script_id}".encode()).hexdigest()
                    supervisor.start_process(process_name)

        event.linked_execution = linked_execution

    event.updator = request.ctx.session["account_info"]

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
    event = await EventsIngestion.get_or_none(id=event_id).select_related("creator", "updator")
    if not event:
        raise CustomError(ErrorCode.NoEventAccess)

    file_path = os.path.abspath(event.file_path)
    if not os.path.exists(file_path):
        raise CustomError(ErrorCode.NoEventAccess)

    async with aiofile.AIOFile(file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = yaml.safe_load(content)

    return response_ok({"config_data": data, "event": event.json})


# noinspection PyTypeChecker
@event_ingestion_bp.post('/sample')
@requires_permission('event:r')
async def get_event_ingestion_kafka_sample(request):
    """
    获取事件接入处理后的日志样例(需要事件查看权限)
    请求参数:
         id: 事件id (str)
         size: 每页条数 (int)，默认10
    """
    data = request.json
    event_id = data.get('id')
    size = data.get('size', 10)
    if size < 1:
        raise CustomError(ErrorCode.ParameterError)

    event = await EventsIngestion.get_or_none(id=event_id, deleted=False)
    if not event:
        raise CustomError(ErrorCode.NoEventAccess)

    messages = await fetch_latest_messages([KAFKA_CONFIG], event.vector_name, size)

    return response_ok(messages)


@event_ingestion_bp.post('/binding')
@requires_permission('event:c')
async def binding_event_ingestion_script(request):
    """
    绑定事件接入和剧本(需要事件创建权限)
    请求参数:
        conditions: 条件 (list)
            conditions: [
                {
                    type: "type",
                    name: "name",
                    value: "value",
                    connect_type: "&&" or "||" or None
                },
                {
                    type: "type",
                    name: "name",
                    value: "value",
                    connect_type: "&&" or "||" or None
                }
            ]
        目前支持的判断条件类型有：
            等于 == ，不等于!= ，大于 > ，小于 < ，大于等于 >= ，小于等于 <=
            字段中存在字符串 contains ，字段中不包含字符串 !contains
            字段中存在集合 includes ，字段中不存在集合 !includes     value为集合，如"[\"abc\",\"lmd\"]"
            字段存在 exists ，字段不存在 !exists        value为空
            字段为空 is_empty ，字段不为空 !is_empty    value为空
            正则表达式 match  字段匹配正则表达式       value为正则表达式，如"^abc.*$"
        event_ingestion_id: 事件接入ID (str)
        script_ids: 剧本ID (list)
        event_name: 事件名称 (str)  用户选择的事件名称，传default
        自定义名称案例：to_string!(.name) + "_" + to_string(.datetime_str) + "234"
    """
    conditions = request.json.get('conditions')
    event_ingestion_id = request.json.get('event_ingestion_id')
    script_ids = request.json.get('script_ids')
    event_name = request.json.get('event_name')
    event = await EventsIngestion.get_or_none(id=event_ingestion_id).prefetch_related("creator")
    if not event:
        raise CustomError(ErrorCode.NotFound)

    event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_ingestion_id, deleted=False).all()
    if not event_related_scripts:
        event_related_scripts = []
    for event_related_script in event_related_scripts:
        has_common = bool(set(script_ids) & set(event_related_script.script_id))
        if has_common:
            raise CustomError(ErrorCode.ScriptExistsError)

    for script_id in script_ids:
        script = await Playbook.get_or_none(id=script_id, deleted=False)
        if not script:
            raise CustomError(ErrorCode.NotFound)

    timestamp_int = str(int(time.time()))
    topic = f"{event.vector_name}_{timestamp_int}"

    conditional_statements = ""

    if conditions:
        for index, condition in enumerate(conditions):
            if bool(re.fullmatch(r"[^\w\s]+", condition["type"])):
                conditional_statements += f""".{condition["name"]} {condition['type']} "{condition['value']}" """
            elif condition["type"] == "contains":
                conditional_statements += f"""{condition['type']}(string!(.{condition['name']}), "{condition['value']}")"""
            elif condition["type"] in ["includes", "!includes"]:
                conditional_statements += f"""{condition['type']}({condition['value']}, string!(.{condition['name']}))"""
            elif condition["type"] in ["exists", "is_empty", "!exists", "!is_empty"]:
                conditional_statements += f"""{condition['type']}(string!(.{condition['name']}))"""
            elif condition["type"] == "match":
                conditional_statements += f"""match(string!(.{condition['name']}), "{condition['value']}")"""
            else:
                raise CustomError(ErrorCode.ParameterError)
            connect_type = condition["connect_type"] if condition.get("connect_type") in ["&&", "||"] else None
            if connect_type:
                conditional_statements += f" {connect_type} "

    async with aiofile.AIOFile(event.file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = yaml.safe_load(content)

    condition_list = [
        transform.get("condition")
        for transform in data.get("transforms", {}).values()
        if "condition" in transform
    ]

    if conditional_statements in condition_list:
        raise CustomError(ErrorCode.ConditionalExistsError)

    data.setdefault("transforms", {})[f"{topic}_transforms"] = {
        "type": "filter",
        "inputs": [f"{event.vector_name}_transforms"] if event.transforms_type else [
            event.vector_name],
        "condition": conditional_statements
    }
    if event_name == "default":
        event_name = '"{}" + "_" + to_string(.datetime_str)'.format(event.event_ingestion_name)
    else:
        event_name = "{}".format(event_name)
    data.setdefault("transforms", {})[f"{topic}_event_transforms"] = {
        "type": "remap",
        "inputs": [f"{topic}_transforms"],
        "source": """
.log_data = to_string(encode_json(.))
.event_id = uuid_v4()
.id = .event_id
.ctime = format_timestamp(now(), "%Y-%m-%dT%H:%M:%S%:z", "Asia/Shanghai") ?? "1970-01-01T00:00:00+08:00"
.utime = format_timestamp(now(), "%Y-%m-%dT%H:%M:%S%:z", "Asia/Shanghai") ?? "1970-01-01T00:00:00+08:00"
.deleted = false
.event_name = {}
.event_process_status = "待处理"
.event_source = "kafka"
.created_id = "{}"
.event_ingestion_id = "{}"
del(.datetime_str)
""".format(event_name, event.creator.id, event_ingestion_id)
    }

    data["sinks"][f"{topic}_sinks"] = {
        "inputs": [f"{topic}_event_transforms"],
        "type": "kafka",
        "bootstrap_servers": KAFKA_CONFIG,
        "topic": topic,
        "encoding": {
            "codec": "json"
        }}

    endpoint = "postgresql://{}:{}@{}:{}/{}".format(os.environ.get("RY_DB_USER"), os.environ.get("RY_DB_PASS"),
                                                    os.environ.get("RY_DB_HOST"), os.environ.get("RY_DB_PORT"),
                                                    os.environ.get("RY_DB_NAME"))
    data["sinks"][f"{topic}_event_sinks"] = {
        "inputs": [f"{topic}_event_transforms"],
        "type": "postgres",
        "endpoint": endpoint,
        "table": "events"
    }

    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    for script_id in script_ids:
        process_name = hashlib.md5(f"{event_ingestion_id}_{script_id}".encode()).hexdigest()
        command = f"python start.py -m auto -f {script_id} -t {topic}"
        supervisor.add_process(process_name, command)

    events_ingestion = await EventRelatedScript.create(condition=conditions, event_ingestion_id=event_ingestion_id,
                                                       script_id=script_ids, topic=topic)

    return response_ok(events_ingestion.json)


@event_ingestion_bp.post('/unbundled')
@requires_permission('event:u')
async def unbundled_event_ingestion_script(request):
    """
    解绑事件接入和剧本(需要事件创建权限)
    请求参数:
        event_ingestion_id: 事件接入ID (str)
        script_ids: 剧本ID (list)
    """
    event_ingestion_id = request.json.get('event_ingestion_id')
    script_ids = request.json.get('script_ids')

    event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_ingestion_id, deleted=False).all()

    for script_id in script_ids:
        for ers in event_related_scripts:
            if script_id in ers.script_id:  # ers.script_id 是列表
                if len(ers.script_id) > 1:  # 剧本id大于1代表多个剧本绑定到同一个判断条件，只需删除数据库数据即可
                    ers.script_id.remove(script_id)
                    await ers.save()
                    break
                else:  # 剧本id小于等于1代表单个剧本绑定到同一个判断条件，需删除数据库数据和vector配置文件
                    event = await EventsIngestion.get_or_none(id=event_ingestion_id)
                    if not event:
                        raise CustomError(ErrorCode.NotFound)

                    async with aiofile.AIOFile(event.file_path, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        data = yaml.safe_load(content)

                    key_to_delete_transforms = f"{ers.topic}_transforms"
                    key_to_delete_sinks = f"{ers.topic}_sinks"

                    if key_to_delete_transforms in data["transforms"]:
                        del data["transforms"][key_to_delete_transforms]

                    if key_to_delete_sinks in data["sinks"]:
                        del data["sinks"][key_to_delete_sinks]

                    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
                        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
                        await f.write(yaml_str)

                    ers.deleted = True
                    await ers.save()
                    break
        process_name = hashlib.md5(f"{event_ingestion_id}_{script_id}".encode()).hexdigest()
        supervisor.stop_process(process_name)
        supervisor.remove_process(process_name)

    return response_ok("剧本解绑成功")


@event_ingestion_bp.post('/export')
@requires_permission('event:r')
async def export_event_ingestion_script(request):
    """
    导出事件接入(需要事件查看权限)
    请求参数:
        ids: 事件接入ID (list)
    """
    event_ids = request.json.get('ids', [])
    if not isinstance(event_ids, list) or not event_ids:
        raise CustomError(ErrorCode.ParameterError)

    events = await EventsIngestion.filter(id__in=event_ids).prefetch_related("creator", "updator")

    def sanitize_filename(name):
        return "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in name)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for event in events:
            # 使用 aiofile 异步读取文件
            async with aiofile.AIOFile(event.file_path, 'r', encoding='utf-8') as afp:
                content = await afp.read()
                data = yaml.safe_load(content)

            event_data = event.json
            event_data["vector_config"] = str(data)
            event_data.pop("file_path", None)

            filename = f"{sanitize_filename(event.event_ingestion_name or 'unnamed')}.json"
            zipf.writestr(filename, orjson.dumps(event_data))

    buffer.seek(0)
    return response.raw(buffer.read(), headers={
        "Content-Type": "application/zip",
        "Content-Disposition": f"attachment; filename=event_configs.zip"
    })


@event_ingestion_bp.post('/import')
@requires_permission('event:r')
async def import_event_ingestion_script(request):
    """
    导入事件
    请求参数:
        files: 上传的文件
    """
    files = request.files["files"]
    if not files:
        raise CustomError(ErrorCode.ParameterError)
    file_results = []
    for file in files:
        vector_config = orjson.loads(file.body.decode('utf-8'))
        if await EventsIngestion.filter(event_ingestion_name=vector_config.get("event_ingestion_name"),
                                        deleted=False).exists():
            file_results.append({f"{vector_config['event_ingestion_name']}": "已存在相同名称的事件接入"})
            continue

        if await EventsIngestion.filter(syslog_config=vector_config.get("syslog_config"), deleted=False).exists():
            file_results.append({f"{vector_config['event_ingestion_name']}": "当前已存在端口一致的事件接入了"})
            continue

        letters = string.ascii_letters  # 包含 a-zA-Z
        vector_name = ''.join(random.choices(letters, k=12))

        raw_config = vector_config["vector_config"]
        if isinstance(raw_config, str):
            raw_config_dict = ast.literal_eval(raw_config)
        else:
            raw_config_dict = raw_config
        json_str = orjson.dumps(raw_config_dict).decode()
        json_str = json_str.replace(vector_config["vector_name"], vector_name)
        data = orjson.loads(json_str)

        path = f"{VECTOR_CFG}/{vector_name}.yaml"
        async with aiofile.AIOFile(path, 'w', encoding='utf-8') as f:
            yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
            await f.write(yaml_str)

        await EventsIngestion.create(event_ingestion_name=vector_config.get("event_ingestion_name"),
                                     event_ingestion_describe=vector_config.get("event_ingestion_describe"),
                                     event_source_type=vector_config.get("event_source_type"),
                                     transforms_type=vector_config.get("transforms_type"),
                                     enabled_status=vector_config.get("enabled_status"),
                                     creator=request.ctx.session['account_info'],
                                     updator=request.ctx.session['account_info'], file_path=path,
                                     linked_execution=vector_config.get("linked_execution"),
                                     syslog_config=vector_config.get("syslog_config"), vector_name=vector_name)

        file_results.append({f"{vector_config['event_ingestion_name']}": "上传成功"})

    return response_ok(file_results)


@event_ingestion_bp.post('/parse')
@requires_permission('event:r')
async def parse_log(request):
    """
    解析日志（需要事件查看权限）

    请求参数:
        msg: 日志内容 (str)
        type: 解析类型 (str): JSON、key-value、CEF、Grok、Delimiter
        value: 附加参数 (str)
            - key-value: 格式 "=,:", 代表键值对与键值之间的分隔符
            - Grok: Grok 正则
            - Delimiter: 分隔符
    """
    msg = request.json.get('msg')
    parse_type = request.json.get('type')
    value = request.json.get('value', None)
    if not msg or not parse_type:
        return response_ok("参数错误")

    if parse_type == "JSON":
        return response_ok(orjson.loads(msg))
    elif parse_type == "key-value":
        if not value or "," not in value:
            raise CustomError(ErrorCode.ParameterError)
        key_value_list = msg.split(value.split(",")[0])
        result = {}
        for kv in key_value_list:
            if not kv:
                continue
            key_value = kv.split(value.split(",")[1])
            if len(key_value) == 2:
                result[key_value[0]] = key_value[1]
        return response_ok(result)
    elif parse_type == "CEF":
        new_msg = CEF_REGEX.match(msg)
        if not new_msg:
            return response_ok("解析失败，请检查日志是否符合CEF标准")
        return response_ok(new_msg.groupdict())
    elif parse_type == "Grok":
        if not value:
            return response_ok("解析失败，请检查是否存在正则表达式")
        new_msg = re.match(value, msg)
        if not new_msg:
            return response_ok("解析失败，请检查日志是否符合Grok的正则表达式")
        return response_ok(new_msg.groupdict())
    elif parse_type == "Delimiter":
        if not value:
            raise CustomError(ErrorCode.ParameterError)
        if value not in msg:
            return response_ok("解析失败，请检查日志是否包含分隔符")
        return response_ok(msg.split(value))
