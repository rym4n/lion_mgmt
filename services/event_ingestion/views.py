# coding=utf-8
import hashlib
import json
import os
import random
import re
import string
import time
from datetime import datetime, timedelta, timezone

import aiofile
import yaml
from sanic import Blueprint
from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import get_client_ip
from models.playbook import Playbook
from models.event import EventsIngestion, EventRelatedScript
from services.event_ingestion import supervisor
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
            f"{topic}_sample_sinks": {
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
        event_source_type: 事件来源类型 (str) 目前支持syslog、kafka、socket(socket传的参数与syslog一致，但是接收的内容没有syslog严格，无需固定日志格式)
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
        name: 事件名称 (str)
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
        transforms_type: 转换类型 (str) 目前就三种Grok、CEF、key-value         非必填
        transforms_config: 转换配置 (str) 只有Grok时需要填写，默认情况下为None       非必填
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
        if transforms_type == "Grok":
            data["transforms"][f"{event.vector_name}_transforms"]["source"] = transforms_config[transforms_type].format(
                transformsconfig)
        else:
            data["transforms"][f"{event.vector_name}_transforms"]["source"] = transforms_config[
                transforms_type]
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
    event = await EventsIngestion.filter(id=event_id).select_related("creator", "updator").first()
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
         id: 事件id (str)
         page: 页码 (int)，从1开始，默认1
         size: 每页条数 (int)，默认10
         keyword: 模糊搜索关键字 (str)，可选
    """
    data = request.json
    event_id = data.get('id')
    page = data.get('page', 1)
    size = data.get('size', 10)
    keyword = data.get('keyword', None)
    if page < 1 or size < 1:
        raise CustomError(ErrorCode.ParameterError)

    event = await EventsIngestion.filter(id=event_id).first()
    if not event:
        raise CustomError(ErrorCode.NotFound)

    log_path = os.path.join(SAMPLE_SINK_PATH, f"{event.vector_name}.log")
    log_path = log_path.replace("\\", "/")
    if not os.path.exists(log_path):
        raise CustomError(ErrorCode.NotFound)

    async with aiofile.AIOFile(log_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        logs = content.strip().split("\n")

    if keyword:
        logs = [log for log in logs if keyword.lower() in log.lower()]

    start = (page - 1) * size
    end = start + size
    alogs = logs[start:end]

    return response_ok({
        "logs": alogs,
        "total": len(logs),
        "page": page,
        "size": size,
    })


# noinspection PyTypeChecker
@event_ingestion_bp.post('/original/sample')
@requires_permission('event:r')
async def get_event_ingestion_syslog_sample(request):
    """
    获取事件接入原始日志样例(需要事件查看权限)
    请求参数:
         id: 事件id (str)
         page: 页码 (int)
         size: 页面大小 (int)
    """
    event_id = request.json.get('id')
    page = request.json.get('page', 1)
    size = request.json.get('size', 10)
    if page < 1 or size < 1:
        raise CustomError(ErrorCode.ParameterError)

    event = await EventsIngestion.filter(id=event_id).first()
    if not event:
        raise CustomError(ErrorCode.NotFound)

    log_path = os.path.join(SAMPLE_SINK_PATH, f"{event.vector_name}.log")
    log_path = log_path.replace("\\", "/")
    if not os.path.exists(log_path):
        raise CustomError(ErrorCode.NotFound)

    async with aiofile.AIOFile(log_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        logs = content.strip().split("\n")
        start = (page - 1) * size
        end = start + size
        alogs = logs[start:end]

        return response_ok({
            "logs": alogs,
            "total": len(logs),
            "page": page,
            "size": size,
        })


@event_ingestion_bp.post('/binding')
@requires_permission('event:c')
async def binding_event_ingestion_script(request):
    """
    绑定事件接入和剧本(需要事件创建权限)
    请求参数:
        judge: 判断条件 (json)  ，
            {
                type: "&&" or "||" or None,         (&&为and，||为or)
                conditions: [
                    {
                        type: "type",
                        name: "name",
                        value: "value"
                    },
                    {
                        type: "type",
                        name: "name",
                        value: "value"
                    }
                ]
            }
        目前支持的判断条件类型有：
            等于 == ，不等于!= ，大于 > ，小于 < ，大于等于 >= ，小于等于 <=
            字段中存在字符串 contains ，字段中不包含字符串 !contains
            字段中存在集合 includes ，字段中不存在集合 !includes     value为集合，如"[\"abc\",\"lmd\"]"
            字段存在 exists ，字段不存在 !exists        value为空
            字段为空 is_empty ，字段不为空 !is_empty    value为空
            正则表达式 match  字段匹配正则表达式       value为正则表达式，如"^abc.*$"
    # judge = request.json.get('judge')
        event_ingestion_id: 事件接入ID (str)
        script_ids: 剧本ID (list)
    """
    conditions = request.json.get('judge')
    event_ingestion_id = request.json.get('event_ingestion_id')
    script_ids = request.json.get('script_ids')
    event = await EventsIngestion.filter(id=event_ingestion_id).first()
    if not event:
        raise CustomError(ErrorCode.NotFound)

    event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_ingestion_id).all()
    if not event_related_scripts:
        event_related_scripts = []
    for event_related_script in event_related_scripts:
        has_common = bool(set(script_ids) & set(event_related_script.script_id))
        if has_common:
            raise CustomError(ErrorCode.ScriptExistsError)

    for script_id in script_ids:
        script = await Playbook.filter(id=script_id, deleted=False).first()
        if not script:
            raise CustomError(ErrorCode.NotFound)

    timestamp_int = str(int(time.time()))
    topic = f"{event.vector_name}_{timestamp_int}"

    conditional_statements = ""

    if conditions:
        connect_type = conditions["type"] if conditions["type"] in ["&&", "||"] else None
        for index, condition in enumerate(conditions["conditions"]):
            is_last_condition = index == len(conditions["conditions"]) - 1
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
            if connect_type and not is_last_condition:
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

    data["sinks"][f"{topic}_sinks"] = {
        "inputs": [f"{topic}_transforms"],
        "type": "kafka",
        "bootstrap_servers": "lion-kafka:9092",
        "topic": topic,
        "encoding": {
            "codec": "json"
        }}

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
                    event = await EventsIngestion.filter(id=event_ingestion_id).first()
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


@event_ingestion_bp.post('/healthy/script')
@requires_permission('event:r')
async def healthy_check_script(request):
    """
    联动执行健康检查(需要事件查看权限)
    请求参数:
        id: 事件接入ID (str)
    """
    event_id = request.json.get('id')
    if not event_id:
        raise CustomError(ErrorCode.ParameterError)
    event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_id).all()
    if not event_related_scripts:
        raise CustomError(ErrorCode.NotFound)

    healthy_list = []
    for event_related_script in event_related_scripts:
        for script_id in event_related_script.script_id:
            process_name = hashlib.md5(f"{event_id}_{script_id}".encode()).hexdigest()
            status = supervisor.process_status(process_name)
            healthy_list.append({"script_id": script_id, "status": status})

    overall_status = "healthy" if all(h["status"] == "healthy" for h in healthy_list) else "unhealthy"
    return response_ok({"status": overall_status, "data": healthy_list})


@event_ingestion_bp.post('/healthy/enabled')
@requires_permission('event:r')
async def healthy_check_enabled(request):
    """
    上游接入健康检查(需要事件查看权限)
    请求参数:
        id: 事件接入ID (str)
    """
    event_id = request.json.get('id')
    event = await EventsIngestion.filter(id=event_id, deleted=False).first()
    if not event:
        raise CustomError(ErrorCode.NotFound)

    file_path = "./data/sample_sinks/" + event.vector_name + ".log"

    if not os.path.exists(file_path):
        return response_ok({"status": "unhealthy"})

    async with aiofile.AIOFile(file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        logs = content.strip().split("\n")
    if not logs:
        return response_ok({"status": "unhealthy"})

    last_log = logs[-1]

    time_str = json.loads(last_log)["timestamp"]
    timestamp = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")

    current_time = datetime.now(timezone.utc)

    five_minutes_ago = current_time - timedelta(minutes=5)

    if five_minutes_ago <= timestamp <= current_time:
        return response_ok({"status": "healthy"})
    else:
        return response_ok({"status": "unhealthy"})
