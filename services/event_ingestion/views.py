# coding=utf-8
import ast
import hashlib
import io
import os
import random
import string
import subprocess
import tempfile
import time
import urllib.parse
import zipfile

import aiofile
import orjson
import yaml
from openai import OpenAI
from sanic import Blueprint, response
from sanic.log import logger
from sanic_ext import openapi
from tortoise.functions import Max

from core.error_code import ErrorCode
from core.response import response_ok
from libs import supervisor
from libs.chat import get_ai_config
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import run_linux_command, query_vlog
from models import PlaybookVersion, Playbook, Config
from models.event import EventsIngestion, EventRelatedScript
from settings import VECTOR_CFG, KAFKA_SERVERS, SUPERVISOR_PATH, VL_HOST

event_ingestion_bp = Blueprint('event_ingestion', url_prefix='/event-ingestion')
_all__ = ["event_ingestion_bp"]


def get_conditional_statements(conditions):
    conditional_statements = ""
    for index, condition in enumerate(conditions):
        if condition["type"] in ["==", "!="]:
            conditional_statements += f""".log."{condition["name"]}" {condition['type']} "{condition['value']}" """
        elif condition["type"] in [">", "<", ">=", "<="]:
            conditional_statements += f"""(to_int(.log."{condition["name"]}") ?? 0) {condition['type']} {int(condition['value'])}"""
        elif condition["type"] in ["!contains", "contains"]:
            conditional_statements += f"""{condition['type']}(string!(.log."{condition['name']}"), "{condition['value']}")"""
        elif condition["type"] in ["includes", "!includes"]:
            conditional_statements += f"""{condition['type']}({condition['value']}, string!(.log."{condition['name']}"))"""
        elif condition["type"] in ["exists", "!exists"]:
            conditional_statements += f"""{condition['type']}(.log."{condition['name']}")"""
        elif condition["type"] in ["is_empty", "!is_empty"]:
            conditional_statements += f"""{condition['type']}(to_string!(.log."{condition['name']}"))"""
        elif condition["type"] == "match":
            conditional_statements += f"""match(string!(.log."{condition['name']}"), r'{condition['value']}')"""
        else:
            raise CustomError(ErrorCode.ParameterError)
        connect_type = condition["connect_type"] if condition.get("connect_type") in ["&&", "||"] else None
        if connect_type:
            conditional_statements += f" {connect_type} "

    return conditional_statements


def check_vector_config(vector_config, vector_config_yaml=None):
    with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.yaml',
            encoding='utf-8',
            delete=False
    ) as tmp_file:
        if vector_config_yaml:
            yaml_str = vector_config_yaml
        else:
            yaml_str = yaml.dump(vector_config, default_flow_style=False, allow_unicode=True)
        logger.info(yaml_str)
        tmp_file.write(yaml_str)
        tmp_file.flush()

        tmp_path = tmp_file.name

        result = run_linux_command(f'vector validate --config-yaml {tmp_path}')

    try:
        if result["code"] != 0:
            return False
        else:
            return True
    finally:
        os.unlink(tmp_path)


# 生成配置文件
# noinspection PyTypeChecker
async def gen_vector_config(output_path, sources, topic, project_id):
    config = {
        "sources": sources,
        "sinks": {
            f"{topic}_sample_sinks": {
                "type": "elasticsearch",
                "inputs": [f"{topic}_date_transforms"],
                "api_version": "v8",
                "compression": "gzip",
                "doc_type": "_doc",
                "endpoints": [f"{VL_HOST}/insert/elasticsearch/"],
                "mode": "bulk",
                "healthcheck": {
                    "enabled": False,
                },
                "query": {
                    '_msg_field': "message",
                    '_time_field': "@timestamp",
                    "_stream_fields": 'host,source_type'
                },
                "request": {
                    "headers": {
                        "AccountID": "1",
                        "ProjectID": str(project_id)
                    }
                }
            }
        },
        "transforms": {
            f"{topic}_date_transforms": {
                "type": "remap",
                "inputs": [f"{topic}"],
                "source": r'''
.datetime_str = format_timestamp(now(), "%Y%m%d%H%M%S", "Asia/Shanghai") ?? "19700101000000"
'''
            }
        }
    }
    yaml_str = yaml.dump(config, default_flow_style=False, allow_unicode=True)

    result = check_vector_config(config)

    if not result:
        return None

    async with aiofile.AIOFile(output_path, mode='w', encoding='utf-8') as file:
        await file.write(yaml_str)

    return config


@event_ingestion_bp.post('/new')
@openapi.definition(
    summary="创建新的事件接入",
    description="创建新的事件接入(需要事件接入创建权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "event_name": {
                        "type": "string",
                        "description": "事件名称"
                    },
                    "event_describe": {
                        "type": "string",
                        "description": "事件描述"
                    },
                    "event_source_type": {
                        "type": "string",
                        "description": "事件来源类型 目前支持kafka、socket"
                    },
                    "syslog_port": {
                        "type": "integer",
                        "description": "日志端口 事件来源类型为syslog时才传，默认为None"
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "单条消息的最大长度，单位为KB，默认100KB"
                    },
                    "event_source_address": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "事件来源地址 事件来源类型为kafka时才传 默认为None"
                    },
                    "communication_type": {
                        "type": "string",
                        "description": "通信类型 事件来源类型为syslog时才传，默认为None tcp/udp"
                    },
                    "kafka_group_id": {
                        "type": "string",
                        "description": "kafka消费组ID 事件来源类型为kafka时才传，默认为None"
                    },
                    "kafka_topic": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "kafka主题 事件来源类型为kafka时才传，默认为None"
                    },
                    "sasl_mechanism": {
                        "type": "string",
                        "description": "sasl机制 事件来源类型为kafka时才传，默认为None"
                    },
                    "sasl_username": {
                        "type": "string",
                        "description": "sasl用户名 事件来源类型为kafka时才传，默认为None"
                    },
                    "sasl_password": {
                        "type": "string",
                        "description": "sasl密码 事件来源类型为kafka时才传，默认为None"
                    },
                    "enabled_status": {
                        "type": "integer",
                        "description": "上游接入 启用为 1 ，禁用为 0 默认启用"
                    },
                    "linked_execution": {
                        "type": "integer",
                        "description": "联动执行 启用为 1 ，禁用为 0 默认禁用"
                    },
                    "retention_days": {
                        "type": "integer",
                        "description": "日志数据的保留天数, 默认为 7"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "object"},
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:c')
async def event_ingestion(request):
    data = request.json  # 只解析一次
    event_name = data.get('event_name')
    event_describe = data.get('event_describe')
    event_source_type = data.get('event_source_type')
    syslog_port = data.get('syslog_port')
    event_source_address = data.get('event_source_address') or []
    communication_type = data.get('communication_type')
    kafka_group_id = data.get('kafka_group_id', "Aioe_lion_ship")
    kafka_topic = data.get('kafka_topic') or []
    sasl_mechanism = data.get('sasl_mechanism')
    sasl_username = data.get('sasl_username')
    sasl_password = data.get('sasl_password')
    enabled_status = int(data.get('enabled_status', 1))
    linked_execution = int(data.get('linked_execution', 0))
    retention_days = int(data.get('retention_days', 7))
    max_length = int(data.get('max_length', 100))

    if event_source_type not in ["syslog", "socket", "kafka"]:
        raise CustomError(ErrorCode.ParameterError)

    syslog_config = None
    if syslog_port and communication_type:
        syslog_config = communication_type + str(syslog_port)

    vector_name = ''.join(random.choices(string.ascii_letters, k=12))

    max_project_id = await EventsIngestion.all().annotate(max_pid=Max("project_id")).values_list("max_pid", flat=True)
    project_id = max_project_id[0] + 1 if max_project_id and max_project_id[0] else 1

    output_path = os.path.join(VECTOR_CFG, vector_name + ".yaml")

    # await setup_ilm_policy(f"logs_retention_policy-{event_name}", f"{retention_days}d")
    # await setup_logs_template(f"logs-{event_name}", f"logs_retention_policy-{event_name}")

    vector_config = None

    if event_source_type in ["syslog", "socket"]:
        sources = {vector_name: {
            "type": event_source_type,
            "address": f"0.0.0.0:{syslog_port}",
            "max_length": max_length * 1024,
            "mode": communication_type
        }}
        vector_config = await gen_vector_config(output_path, sources, vector_name, project_id)
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
                    "password": urllib.parse.quote(sasl_password)
                }}}
        else:
            sources = {vector_name: {
                "type": "kafka",
                "bootstrap_servers": ",".join(event_source_address),
                "group_id": kafka_group_id,
                "topics": kafka_topic
            }}
        vector_config = await gen_vector_config(output_path, sources, vector_name, project_id)

    if vector_config is None:
        raise CustomError(ErrorCode.ConfigFileGenerateError)

    events_ingestion = await EventsIngestion.create(event_ingestion_name=event_name, event_ingestion_describe=event_describe,
                                                    event_source_type=event_source_type, enabled_status=enabled_status,
                                                    creator=request.ctx.session['account_info'], updator=request.ctx.session['account_info'],
                                                    file_path=output_path.replace("\\", "/"),
                                                    linked_execution=linked_execution, syslog_config=syslog_config, vector_name=vector_name,
                                                    vector_config=vector_config, vector_config_raw=vector_config,
                                                    enabled_health_status="healthy", retention_days=retention_days, project_id=project_id)

    return response_ok(events_ingestion.json)


@event_ingestion_bp.post('/list')
@openapi.definition(
    summary="获取所有事件接入列表",
    description="获取所有事件接入列表(需要事件查看权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "页码"
                    },
                    "size": {
                        "type": "integer",
                        "description": "页面大小"
                    },
                    "name": {
                        "type": "string",
                        "description": "事件名称 非必填"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "events": {
                                        "type": "array",
                                        "items": {"type": "object"}
                                    },
                                    "total": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"}
                                }
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:r')
async def get_all_events_ingestion(request):
    page = request.json.get('page', 1)
    size = request.json.get("size", 15)
    offset = (page - 1) * size
    limit = size

    filters = {"deleted": False}
    if request.json.get("name"):
        filters["event_ingestion_name__icontains"] = request.json.get("name")

    events = await EventsIngestion.filter(**filters).order_by("-ctime").offset(offset).select_related("creator", "updator").limit(
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
@openapi.definition(
    summary="删除事件接入",
    description="删除事件接入(需要事件删除权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "事件ID"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:d')
async def delete_event_ingestion(request):
    event_id = request.json.get('id')
    event = await EventsIngestion.get(id=event_id)
    es = await EventRelatedScript.get_or_none(event_ingestion_id=event_id)
    if es:
        for script_id in es.script_id:
            process_name = hashlib.md5(f"{es.event_ingestion_id}_{script_id}".encode()).hexdigest()
            supervisor.stop_process(process_name)
        await es.delete()
    if not event:
        raise CustomError(ErrorCode.NotFound)
    if event.enabled_status == 1:
        return response_ok("请先禁用事件接入再删除")

    await event.delete()

    return response_ok("事件删除成功")


@event_ingestion_bp.post('/update')
@openapi.definition(
    summary="更新事件接入",
    description="更新事件接入(需要事件更新权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "事件ID"
                    },
                    "event_describe": {
                        "type": "string",
                        "description": "事件描述 非必填"
                    },
                    "event_source_address": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "事件来源地址 事件来源类型为kafka时才传 非必填"
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "单条消息的最大长度，单位为KB，默认100KB"
                    },
                    "syslog_port": {
                        "type": "integer",
                        "description": "日志端口 端口范围500-2000 事件来源类型为syslog时才传，默认为None 非必填"
                    },
                    "communication_type": {
                        "type": "string",
                        "description": "通信类型 事件来源类型为syslog时才传，默认为None 非必填"
                    },
                    "kafka_group_id": {
                        "type": "string",
                        "description": "kafka消费组ID 事件来源类型为kafka时才传，默认为None 非必填"
                    },
                    "kafka_topic": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "kafka主题 事件来源类型为kafka时才传，默认为None 非必填"
                    },
                    "transforms_type": {
                        "type": "string",
                        "description": "转换类型 目前支持Grok、CEF、key-value、JSON、Delimiter(分割符) 非必填"
                    },
                    "transforms_config": {
                        "type": "string",
                        "description": "转换配置 只有Grok时需要填写，默认情况下为None 非必填"
                    },
                    "key_value_delimiter": {
                        "type": "string",
                        "description": "键值对分隔符 只有key-value时需要填写，默认情况下为None 非必填"
                    },
                    "key_delimiter_value": {
                        "type": "string",
                        "description": "键值分隔符 只有key-value时需要填写，默认情况下为None 非必填"
                    },
                    "sasl_mechanism": {
                        "type": "string",
                        "description": "sasl机制 事件来源类型为kafka时才传，默认为None 非必填"
                    },
                    "sasl_username": {
                        "type": "string",
                        "description": "sasl用户名 事件来源类型为kafka时才传，默认为None 非必填"
                    },
                    "sasl_password": {
                        "type": "string",
                        "description": "sasl密码 事件来源类型为kafka时才传，默认为None 非必填"
                    },
                    "enabled_status": {
                        "type": "integer",
                        "description": "上游接入 启用为 1 ，禁用为 0 非必填"
                    },
                    "retention_days": {
                        "type": "integer",
                        "description": "日志数据的保留天数, 默认为 7"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:u')
async def update_event_ingestion(request):
    data = request.json  # 只解析一次
    event_id = data.get('id')
    event_describe = data.get('event_describe')
    syslog_port = data.get('syslog_port')
    event_source_address = data.get('event_source_address', [])
    communication_type = data.get('communication_type')
    kafka_group_id = data.get('kafka_group_id')
    transforms_type = data.get('transforms_type')
    transformsconfig = data.get('transforms_config')
    sasl_mechanism = data.get('sasl_mechanism')
    sasl_username = data.get('sasl_username')
    sasl_password = data.get('sasl_password')
    enabled_status = data.get('enabled_status')
    retention_days = data.get('retention_days')
    key_delimiter_value = data.get('key_delimiter_value')
    key_value_delimiter = data.get('key_value_delimiter')
    kafka_topic = data.get('kafka_topic', [])
    event = await EventsIngestion.get(id=event_id)
    max_length = int(data.get('max_length', 100))

    if enabled_status == 0:
        if os.path.exists(event.file_path):
            os.remove(event.file_path)
        event.enabled_status = 0
        await event.save()
        return response_ok("禁用成功")

    data = event.vector_config_raw

    if kafka_topic:
        data["sources"][event.vector_name]["topics"] = kafka_topic
    if event_source_address:
        data["sources"][event.vector_name]["bootstrap_servers"] = ",".join(event_source_address)
    if communication_type:
        data["sources"][event.vector_name]["mode"] = communication_type
        event.syslog_config = communication_type + event.syslog_config[3:]
    if kafka_group_id:
        data["sources"][event.vector_name]["group_id"] = kafka_group_id
    if transforms_type:
        first_transform = f"{event.vector_name}_transforms"
        data["sinks"][f"{event.vector_name}_sample_sinks"]["inputs"] = [first_transform]
        data.setdefault("transforms", {}).setdefault(first_transform, {})["type"] = "remap"
        data["transforms"][first_transform]["inputs"] = [f"{event.vector_name}_date_transforms"]
        data["transforms"][first_transform]["drop_on_error"] = False

        config = await Config.get_or_none(key=transforms_type, module="transforms")
        if not config:
            raise CustomError(ErrorCode.TypeNotSupportedError)
        args = {
            "transformsconfig": transformsconfig,
            "key_value_delimiter": key_value_delimiter,
            "key_delimiter_value": key_delimiter_value
        }
        value = config.value
        if '{' in value and transforms_type in ["kv", "grok"]:
            value = value.format(**args)

        data["transforms"][first_transform]["source"] = value
        event.config_info = {transforms_type: args}

    if syslog_port:
        data["sources"][event.vector_name]["address"] = f"0.0.0.0:{syslog_port}"
        event.syslog_config = event.syslog_config[:3] + str(syslog_port)
    if max_length:
        data["sources"][event.vector_name]["max_length"] = max_length * 1024
    if sasl_mechanism:
        data["sources"][event.vector_name].setdefault("sasl", {})["enabled"] = True
        data["sources"][event.vector_name]["sasl"]["mechanism"] = sasl_mechanism
    if sasl_username:
        data["sources"][event.vector_name]["sasl"]["username"] = sasl_username
    if sasl_password:
        data["sources"][event.vector_name]["sasl"]["password"] = urllib.parse.quote(sasl_password)

    result = check_vector_config(data)
    if not result:
        raise CustomError(ErrorCode.ConfigFileGenerateError)

    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    if event_describe:
        event.event_ingestion_describe = event_describe
    if transforms_type:
        event.transforms_type = transforms_type
    if enabled_status == 1:
        event.enabled_status = enabled_status

    if retention_days:
        event.retention_days = retention_days

    event.updator = request.ctx.session["account_info"]

    event.vector_config = data

    event.vector_config_raw = data

    await event.save()

    return response_ok("事件更新成功")


@event_ingestion_bp.post('/detail')
@openapi.definition(
    summary="获取事件接入详情",
    description="获取事件接入详情(需要事件查看权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "事件ID"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "event": {"type": "object"},
                                    "sources": {"type": "object"}
                                }
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:r')
async def get_event_ingestion_detail(request):
    event_id = request.json.get('id')
    event = await EventsIngestion.get(id=event_id).prefetch_related("creator", "updator")
    if not event:
        raise CustomError(ErrorCode.NoEventAccess)
    sources = event.vector_config.get("sources", {})
    max_length = sources.get(event.vector_name, {}).get("max_length")
    if max_length:
        max_length = int(max_length) / 1024
    data = event.json
    data["max_length"] = max_length if max_length else 100
    return response_ok({"event": data, "sources": sources})


# noinspection PyTypeChecker
@event_ingestion_bp.post('/sample')
@openapi.definition(
    summary="获取事件接入处理后的日志样例",
    description="获取事件接入处理后的日志样例(需要事件查看权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "事件id"
                    },
                    "size": {
                        "type": "integer",
                        "description": "每页条数，默认10"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {"type": "object"}
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:r')
async def get_event_ingestion_kafka_sample(request):
    data = request.json
    event_id = data.get('id')
    size = data.get("size", 15)
    if size < 1:
        raise CustomError(ErrorCode.ParameterError)

    event = await EventsIngestion.get_or_none(id=event_id, deleted=False)
    if not event:
        raise CustomError(ErrorCode.NoEventAccess)

    messages = await query_vlog("* | first 1 by (_time desc)", size, event.project_id)

    if not messages:
        return response_ok("当前不存在日志")

    data = messages[0]

    if "_msg" in data.keys():
        event.enabled_health_status = "healthy"
    else:
        event.enabled_health_status = "unhealthy"

    if "log" in data.keys():
        event.convert_health_status = "healthy"
    else:
        event.convert_health_status = "unhealthy"

    await event.save()
    return response_ok(data["_msg"])


@event_ingestion_bp.post('/binding')
@openapi.definition(
    summary="绑定事件接入和剧本",
    description="绑定事件接入和剧本(需要事件创建权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["event_ingestion_id", "binding_data"],
                "properties": {
                    "event_ingestion_id": {
                        "type": "string",
                        "description": "事件接入ID"
                    },
                    "binding_data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "conditions": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string"},
                                            "name": {"type": "string"},
                                            "value": {"type": "string"},
                                            "connect_type": {"type": "string"}
                                        }
                                    },
                                    "description": "条件"
                                },
                                "linked_execution": {
                                    "type": "integer",
                                    "description": "联动执行 启用为 1 ，禁用为 0 非必填"
                                },
                                "script_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "剧本ID 非必填，选择联动执行时必填"
                                },
                                "event_name": {
                                    "type": "string",
                                    "description": "事件名称 用户选择的事件名称，传default"
                                },
                                "numbers": {
                                    "type": "integer",
                                    "description": "去重条数 非必填，选择去重时才填写 默认5000, 最大10000"
                                },
                                "fields": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "字段 非必填，选择去重时才填写"
                                },
                                "Concurrency": {
                                    "type": "integer",
                                    "description": "并发数量，默认是1，最大是4"
                                }
                            }
                        }
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "绑定成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:u')
async def binding_event_ingestion_script(request):
    event_ingestion_id = request.json.get('event_ingestion_id')
    event = await EventsIngestion.get(id=event_ingestion_id).prefetch_related("creator")
    if not event:
        raise CustomError(ErrorCode.NotFound)
    event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_ingestion_id, deleted=False).all()
    for event_related_script in event_related_scripts:
        if event_related_script.script_id:
            for script_id in event_related_script.script_id:
                process_name = hashlib.md5(f"{event_ingestion_id}_{script_id}".encode()).hexdigest()
                supervisor.stop_process(process_name)
                supervisor.remove_process(process_name)
                supervisor_path = os.path.join(SUPERVISOR_PATH, "{}.conf".format(process_name))
                if os.path.exists(supervisor_path):
                    os.remove(supervisor_path)
        await event_related_script.delete()

    data = orjson.loads(orjson.dumps(event.vector_config_raw))
    request_datas = request.json.get("binding_data")
    for request_data in request_datas:
        conditions = request_data.get('conditions')
        script_ids = request_data.get('script_ids')
        event_name = request_data.get('event_name')
        linked_execution = request_data.get('linked_execution')

        timestamp_int = str(int(time.time() * 1000))
        topic = f"{event.vector_name}_{timestamp_int}"
        # 如果选择去重则添加去重transforms
        if request_data.get('fields') and request_data.get('numbers'):
            data.setdefault("transforms", {})[f"{topic}_dedupe_transforms"] = {
                "type": "dedupe",
                # 如果之前配置了转换配置则用转换后的配置，否则用原始配置
                "inputs": [f"{event.vector_name}_transforms"] if event.transforms_type else [event.vector_name],
                "cache": {
                    "num_events": int(request_data['numbers'])
                },
                "fields": {
                    "match": request_data.get('fields')
                }
            }
        # 如果配置了判断规则，则添加判断transforms
        if conditions:
            conditional_statements = get_conditional_statements(conditions)

            data.setdefault("transforms", {})[f"{topic}_transforms"] = {
                "type": "filter",
                # 如果之前配置了去重则用去重后的配置，否则用原始配置
                "inputs": [f"{topic}_dedupe_transforms"] if request_data.get('fields') and request_data.get(
                    'numbers') else (
                    [f"{event.vector_name}_transforms"] if event.transforms_type else [event.vector_name]),
                "condition": f"{conditional_statements}"
            }
        data.setdefault("transforms", {})[f"{topic}_event_transforms"] = {
            "type": "remap",
            "inputs": [f"{topic}_transforms"] if conditions else (
                [f"{topic}_dedupe_transforms"] if request_data.get('fields') and request_data.get('numbers') else (
                    [f"{event.vector_name}_transforms"] if event.transforms_type else [event.vector_name])),
            "source": """
.aioe_event_id = uuid_v4()
.log_data = to_string(encode_json(.log))
.id = .aioe_event_id
.ctime = format_timestamp(now(), "%Y-%m-%dT%H:%M:%S%:z", "Asia/Shanghai") ?? "1970-01-01T00:00:00+08:00"
.utime = format_timestamp(now(), "%Y-%m-%dT%H:%M:%S%:z", "Asia/Shanghai") ?? "1970-01-01T00:00:00+08:00"
.deleted = false
.event_name = {}
.event_process_status = "待处理"
.event_source = "{}"
.event_ingestion_id = "{}"
del(.datetime_str)
""".format(event_name, event.event_ingestion_name, event_ingestion_id)}

        data["sinks"][f"{topic}_sinks"] = {
            "inputs": [f"{topic}_event_transforms"],
            "type": "kafka",
            "bootstrap_servers": KAFKA_SERVERS,
            "topic": topic,
            "encoding": {
                "codec": "json"
            }}
        endpoint = f'postgresql://{os.environ.get("RY_DB_USER")}:{urllib.parse.quote(os.environ.get("RY_DB_PASS"))}@{os.environ.get("RY_DB_HOST")}:{os.environ.get("RY_DB_PORT", 5432)}/{os.environ.get("RY_DB_NAME")}'
        data["sinks"][f"{topic}_event_sinks"] = {
            "inputs": [f"{topic}_event_transforms"],
            "type": "postgres",
            "endpoint": endpoint,
            "table": "events",
            "pool_size": 20
        }
        # "buffer": {
        #     "type": "memory",
        #     "max_events": 5000,
        #     "when_full": "block"
        # }
        result = check_vector_config(data)
        if not result:
            raise CustomError(ErrorCode.ConfigFileGenerateError)

        event_related_script = await EventRelatedScript.create(condition=conditions,
                                                               event_ingestion_id=event_ingestion_id,
                                                               script_id=script_ids, topic=topic,
                                                               event_name=event_name,
                                                               numbers=request_data.get('numbers'),
                                                               fields=request_data.get('fields'))

        if linked_execution in [0, 1]:
            if linked_execution == 0:
                for script_id in script_ids:
                    process_name = hashlib.md5(f"{event.id}_{script_id}".encode()).hexdigest()
                    supervisor.stop_process(process_name)
            else:
                concurrency = int(request.json.get("Concurrency", 1))
                if concurrency > 4:
                    concurrency = 4
                for script_id in script_ids:
                    process_name = hashlib.md5(f"{event.id}_{script_id}".encode()).hexdigest()
                    command = f"python start.py -m auto -f {script_id} -t {topic}"
                    supervisor.add_process(process_name, command, concurrency)
                    result = supervisor.start_process(process_name)
                    if result:
                        event.execution_health_status = "healthy"
            event_related_script.status = linked_execution
            await event_related_script.save()

    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    event.vector_config = data

    await event.save()

    return response_ok("绑定成功")


@event_ingestion_bp.post('/unbundled')
@openapi.definition(
    summary="解绑事件接入和剧本",
    description="解绑事件接入和剧本(需要事件创建权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["event_ingestion_id", "script_ids"],
                "properties": {
                    "event_ingestion_id": {
                        "type": "string",
                        "description": "事件接入ID"
                    },
                    "script_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "剧本ID"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "解绑成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:u')
async def unbundled_event_ingestion_script(request):
    event_ingestion_id = request.json.get('event_ingestion_id')
    script_ids = request.json.get('script_ids')

    event_related_scripts = await EventRelatedScript.filter(event_ingestion_id=event_ingestion_id, deleted=False).all()

    event = await EventsIngestion.get_or_none(id=event_ingestion_id)
    if not event:
        raise CustomError(ErrorCode.NoEventAccess)

    data = event.vector_config

    for script_id in script_ids:
        for ers in event_related_scripts:
            if script_id in ers.script_id:  # ers.script_id 是列表
                if len(ers.script_id) > 1:  # 剧本id大于1代表多个剧本绑定到同一个判断条件，只需删除数据库数据即可
                    ers.script_id.remove(script_id)
                    await ers.save()
                    break
                else:  # 剧本id小于等于1代表单个剧本绑定到同一个判断条件，需删除数据库数据和vector配置文件
                    if f"{ers.topic}_transforms" in data["transforms"]:
                        del data["transforms"][f"{ers.topic}_transforms"]

                    if f"{ers.topic}_dedupe_transforms" in data["transforms"]:
                        del data["transforms"][f"{ers.topic}_dedupe_transforms"]

                    if f"{ers.topic}_event_transforms" in data["transforms"]:
                        del data["transforms"][f"{ers.topic}_event_transforms"]

                    if f"{ers.topic}_sinks" in data["sinks"]:
                        del data["sinks"][f"{ers.topic}_sinks"]

                    if f"{ers.topic}_event_sinks" in data["sinks"]:
                        del data["sinks"][f"{ers.topic}_event_sinks"]

                    await ers.delete()
                    break

        process_name = hashlib.md5(f"{event_ingestion_id}_{script_id}".encode()).hexdigest()
        supervisor.stop_process(process_name)
        supervisor.remove_process(process_name)

    result = check_vector_config(data)
    if not result:
        raise CustomError(ErrorCode.ConfigFileGenerateError)

    async with aiofile.AIOFile(event.file_path, 'w', encoding='utf-8') as f:
        yaml_str = yaml.dump(data, allow_unicode=True, default_flow_style=False)
        await f.write(yaml_str)

    event.vector_config = data

    await event.save()

    return response_ok("剧本解绑成功")


@event_ingestion_bp.post('/export')
@openapi.definition(
    summary="导出事件接入",
    description="导出事件接入(需要事件查看权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["ids"],
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "事件接入ID"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "导出成功",
            "content": {
                "application/zip": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    }
)
@requires_permission('ingestion:r')
async def export_event_ingestion_script(request):
    event_ids = request.json.get('ids', [])
    if not isinstance(event_ids, list) or not event_ids:
        raise CustomError(ErrorCode.ParameterError)

    events = await EventsIngestion.filter(id__in=event_ids).prefetch_related("creator", "updator")

    def sanitize_filename(name):
        return "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in name)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for event in events:
            event_data = event.json
            event_data["vector_config"] = str(event.vector_config)
            event_data.pop("file_path", None)
            event_data["vector_config_raw"] = event.vector_config_raw

            filename = f"{sanitize_filename(event.event_ingestion_name or 'unnamed')}.json"
            zipf.writestr(filename, orjson.dumps(event_data))

    buffer.seek(0)
    return response.raw(buffer.read(), headers={
        "Content-Type": "application/zip",
        "Content-Disposition": f"attachment; filename=event_configs_{str(int(time.time()))}.zip"
    })


@event_ingestion_bp.post('/import')
@openapi.definition(
    summary="导入事件",
    description="导入事件",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "string",
                        "format": "binary",
                        "description": "上传的文件"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "导入成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {"type": "object"}
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:c')
async def import_event_ingestion_script(request):
    files = request.files["files"]
    if not files:
        raise CustomError(ErrorCode.ParameterError)
    file_results = []

    for file in files:
        zip_data = io.BytesIO(file.body)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            for name in zf.namelist():
                if not name.endswith(".json"):
                    continue
                vector_config = orjson.loads(zf.read(name))

                if await EventsIngestion.filter(event_ingestion_name=vector_config.get("event_ingestion_name"),
                                                deleted=False).exists():
                    file_results.append({f"{vector_config['event_ingestion_name']}": "已存在相同名称的事件接入"})
                    continue

                if await EventsIngestion.filter(syslog_config=vector_config.get("syslog_config"),
                                                deleted=False).exists():
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
                                             syslog_config=vector_config.get("syslog_config"), vector_name=vector_name,
                                             vector_config_raw=vector_config.get("vector_config_raw"),
                                             vector_config=data,
                                             convert_health_status=vector_config.get("convert_health_status"),
                                             enabled_health_status=vector_config.get("enabled_health_status"),
                                             execution_health_status=vector_config.get("execution_health_status"))

                file_results.append({f"{vector_config['event_ingestion_name']}": "上传成功"})

    return response_ok(file_results)


@event_ingestion_bp.post('/parse')
@openapi.definition(
    summary="解析日志",
    description="解析日志（需要事件查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "transforms_type"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "事件接入id"
                    },
                    "transforms_type": {
                        "type": "string",
                        "description": "转换类型"
                    },
                    "transforms_config": {
                        "type": "string",
                        "description": "转换配置"
                    },
                    "key_value_delimiter": {
                        "type": "string",
                        "description": "键值对分隔符"
                    },
                    "key_delimiter_value": {
                        "type": "string",
                        "description": "键值分隔符"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "解析成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:r')
async def parse_log(request):
    params = request.json
    transforms_type = params.get('transforms_type')
    transformsconfig = params.get('transforms_config')
    key_value_delimiter = params.get('key_value_delimiter')
    key_delimiter_value = params.get('key_delimiter_value')

    tmp_data = {
        "sources": {"tmp": {"type": "stdin"}},
        "sinks": {
            "tmp_sink": {
                "encoding": {"codec": "json"},
                "type": "console",
                "inputs": ["tmp_transforms"]
            }
        }
    }

    tmp_data.setdefault("transforms", {}).setdefault("tmp_transforms", {})["type"] = "remap"
    tmp_data["transforms"]["tmp_transforms"]["inputs"] = ["tmp"]
    tmp_data["transforms"]["tmp_transforms"]["drop_on_error"] = False

    config = await Config.get_or_none(key=transforms_type, module="transforms")
    if not config:
        raise CustomError(ErrorCode.TypeNotSupportedError)

    event = await EventsIngestion.get_or_none(id=params.get('id'), deleted=False)
    if not event:
        raise CustomError(ErrorCode.ParameterError)

    args = {
        "transformsconfig": transformsconfig,
        "key_value_delimiter": key_value_delimiter,
        "key_delimiter_value": key_delimiter_value
    }
    value = config.value
    if any(key in value for key in ['{transformsconfig}', '{key_value_delimiter}', '{key_delimiter_value}']):
        value = value.format(**args)

    tmp_data["transforms"]["tmp_transforms"]["source"] = value

    result = check_vector_config(tmp_data)
    if not result:
        raise CustomError(ErrorCode.LogParseError)

    messages = await query_vlog("*", 1, event.project_id)

    if not messages:
        return response_ok("当前不存在日志")

    try:
        with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.yaml',
                encoding='utf-8',
                delete=False
        ) as tmp_file:

            yaml_str = yaml.dump(tmp_data, default_flow_style=False, allow_unicode=True)
            tmp_file.write(yaml_str)
            tmp_file.flush()

            tmp_path = tmp_file.name

            proc = subprocess.Popen(["vector", "--config", tmp_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

            proc.stdin.write((messages[0].get("message") + "\n").encode())
            proc.stdin.flush()

            output = proc.stdout.readline().strip()
    finally:
        os.unlink(tmp_path)

    if orjson.loads(output).get("log"):
        await EventsIngestion.update_or_create(id=request.json.get('id'), deleted=False,
                                               defaults={"convert_health_status": "healthy"})
        log = orjson.loads(output).get("log")
    else:
        await EventsIngestion.update_or_create(id=request.json.get('id'), deleted=False,
                                               defaults={"convert_health_status": "unhealthy"})
        log = "日志解析失败，请修改解析类型"

    return response_ok(log)


@event_ingestion_bp.post('/config/list')
@openapi.definition(
    summary="获取事件接入配置列表",
    description="获取事件接入配置列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "事件接入id"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {"type": "object"}
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
@requires_permission('ingestion:r')
async def event_ingestion_config_list(request):
    event_id = request.json.get('id')
    if not event_id:
        raise CustomError(ErrorCode.ParameterError)

    config_list = []
    events = await EventRelatedScript.filter(event_ingestion_id=event_id).all()
    if not events:
        return response_ok("当前未配置接入规则")

    for event in events:
        event_dict = event.json
        event_dict["condition"] = ast.literal_eval(event.condition)
        playbooks = []
        if event.script_id:
            for script_id in event.script_id:
                pv = await PlaybookVersion.get_or_none(id=script_id, deleted=False)
                if pv:
                    playbooks.append(pv.playbook_id)
        event_dict["playbooks"] = playbooks
        config_list.append(event_dict)

    return response_ok(config_list)


@event_ingestion_bp.post('/playbook-list')
@openapi.definition(
    summary="获取playbook名称和id列表",
    description="获取playbook名称和id列表",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "name": {"type": "string"}
                                    }
                                }
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
async def playbook_list(request):
    playbooks = await Playbook.filter(deleted=False).all().values_list("id", "name")

    return response_ok([{"id": playbook[0], "name": playbook[1]} for playbook in playbooks])


@event_ingestion_bp.post('/transforms-list')
@openapi.definition(
    summary="获取transforms列表",
    description="获取transforms列表",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string"},
                                        "type": {"type": "string"}
                                    }
                                }
                            },
                            "message": {"type": "string"}
                        }
                    }
                }
            }
        }
    }
)
async def transforms_list(request):
    transforms = await Config.filter(deleted=False, module="transforms").all()

    return response_ok([{"key": transform.key, "type": transform.type} for transform in transforms])


@event_ingestion_bp.post('/grok-ai')
@openapi.definition(
    summary="使用AI进行日志解析生成grok表达式",
    description="使用ai进行日志解析生成grok表达式并返回",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["log"],
                "properties": {
                    "log": {
                        "type": "string",
                        "description": "日志内容"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "生成成功",
            "content": {
                "text/plain": {
                    "schema": {"type": "string"}
                }
            }
        }
    }
)
async def grok_ai(request):
    log = request.json.get('log')
    SYSTEM_PROMPT = """
# 角色
你是一位日志解析专家，擅长使用vecotr工具,能够根据提供的日志样例生成 *单个* Grok 表达式。

# 目标
只返回最终 Grok 表达式字符串（单行）,不得输出除表达式本身以外的任何内容

# 规则
1. 基于我提供的日志样例自动识别固定文本、字段边界与字段类型
2. 优先使用内置模式: USERNAME, USER, EMAILLOCALPART, EMAILADDRESS, INT, BASE10NUM, NUMBER, BASE16NUM, BASE16FLOAT, WORD, NOTSPACE, SPACE, DATA, GREEDYDATA, QUOTEDSTRING, UUID, MAC, IPV6, IPV4, IP, HOSTNAME, IPORHOST, HOSTPORT, YEAR, HOUR, MINUTE, SECOND, TIME, DATE_US, DATE_EU, ISO8601_TIMEZONE, ISO8601_SECOND, TIMESTAMP_ISO8601, DATE, DATESTAMP, TZ, DATESTAMP_RFC822, DATESTAMP_RFC2822, DATESTAMP_OTHER, DATESTAMP_EVENTLOG, SYSLOGTIMESTAMP
3. 尽量精简,避免过度捕获；不需要刻意区分 DATA/GREEDYDATA

# 输出格式（严格）：
仅输出 Grok 表达式本身（单行）。不要包裹引号,不要回显“grok”字样,不要多余换行。

# 示例（仅供参考，真实输出时不含此类内容）：
日志样例：2020-10-02T23:22:12.223222Z info Hello world
输出结果：%{TIMESTAMP_ISO8601:timestamp} %{LOGLEVEL:level} %{GREEDYDATA:message}
    """

    ai_cfg = await get_ai_config()
    host = ai_cfg.get('llm_host')
    if host is None:
        return response_ok("当前未配置ai服务模型，请前往系统设置进行配置。")

    api_key = ai_cfg.get('llm_api_key')
    model = ai_cfg.get('llm_model')
    if model is None:
        return response_ok("当前未配置ai服务模型，请前往系统设置进行配置。")

    client = OpenAI(
        base_url=host,
        api_key=api_key
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"提供给你的日志样例是：{log}"}
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=4000,
        temperature=0.7
    )

    full_response = resp.choices[0].message.content if resp.choices else ""

    return full_response
