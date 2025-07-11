# coding=utf-8

from elasticsearch import AsyncElasticsearch
from sanic import Blueprint

from core.error_code import ErrorCode
from core.response import response_ok, response_fail
from libs.security.authorization import requires_permission
from settings import ES_HOST, ES_PASS, ES_USER

logs_bp = Blueprint('logs', url_prefix='/logs')
_all__ = ["logs_bp"]


@logs_bp.route('/query', methods=['POST'])
@requires_permission('logs:r')
async def get_logs(request):
    """
    查询ES日志数据

    请求参数：
        index: str, 索引名称，必填
        keyword: str, 搜索关键词，必填
    """
    param = request.json
    if not param.get('index') or not param.get('conditions'):
        return response_fail(ErrorCode.ParameterError)
    async with AsyncElasticsearch(ES_HOST, http_auth=(ES_USER, ES_PASS)) as es:
        resp = await es.search(index=param['index'], query={
            "multi_match": {
                "query": param['keyword'],
                "fields": ["*"],
                "type": "best_fields",
                "fuzziness": "AUTO",
                "prefix_length": 2,
            }
        })
        return response_ok(resp["hits"]["hits"])


@logs_bp.route('/indexes', methods=['POST'])
@requires_permission('logs:r')
async def get_index_list(request):
    """
    获取索引列表
    """
    async with AsyncElasticsearch(ES_HOST, http_auth=(ES_USER, ES_PASS)) as es:
        resp = await es.cat.indices(index='logs-*', format='json')
        return response_ok(resp.body)


def extract_fields(mapping, prefix=''):
    fields = {}
    for field, props in mapping.items():
        full_path = f"{prefix}.{field}" if prefix else field
        if 'properties' in props:  # 嵌套字段递归处理
            nested_fields = extract_fields(props['properties'], full_path)
            fields.update(nested_fields)
        elif 'type' in props:  # 基础字段记录类型
            fields[full_path] = props['type']
    return fields


@logs_bp.route('/fields', methods=['POST'])
@requires_permission('logs:r')
async def get_field_list(request):
    """
    获取索引字段列表
    """
    index_name = request.json.get('index')
    async with AsyncElasticsearch(ES_HOST, http_auth=(ES_USER, ES_PASS)) as es:
        all_fields = {}
        mapping = await es.indices.get_mapping(index=index_name)
        index_mapping = mapping[index_name]['mappings']['properties']
        all_fields[index_name] = extract_fields(index_mapping)
        return response_ok(all_fields)
