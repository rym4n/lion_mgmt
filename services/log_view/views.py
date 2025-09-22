# coding=utf-8

from elasticsearch import AsyncElasticsearch
from sanic import Blueprint
from sanic.log import logger

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from settings import ES_HOST, ES_PASS, ES_USER

logs_bp = Blueprint('logs', url_prefix='/logs')
_all__ = ["logs_bp"]


@logs_bp.route('/query', methods=['POST'])
@requires_permission('logs:r')
async def get_logs_by_sql(request):
    """
    查询ES日志数据

    请求参数：
        sql: str, 查询语句，必填
    """
    sql = request.json.get('sql')
    if not sql:
        raise CustomError(ErrorCode.ParameterError)

    async with AsyncElasticsearch(ES_HOST, http_auth=(ES_USER, ES_PASS)) as es:
        try:
            resp = await es.sql.query(body={"query": sql})
            return response_ok(resp.body)
        except Exception as e:
            logger.warning(e)
            return response_ok([])


@logs_bp.route('/indexes', methods=['POST'])
@requires_permission('logs:r')
async def get_index_list(request):
    """
    获取索引列表
    """
    async with AsyncElasticsearch(ES_HOST, http_auth=(ES_USER, ES_PASS)) as es:
        resp = await es.cat.aliases(format='json')
        result = [i['alias'] for i in resp.body if i.get('alias', '').startswith('logs-')]
        # resp = await es.cat.indices(index='logs-*', format='json')
        return response_ok(list(set(result)))


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

    请求参数：
        index: str, 索引别名，必填
    """
    index_name = request.json.get('index')
    if not index_name:
        raise CustomError(ErrorCode.ParameterError)
    async with AsyncElasticsearch(ES_HOST, http_auth=(ES_USER, ES_PASS)) as es:
        alias_info = await es.indices.get_alias(name=index_name)
        indices = list(alias_info.keys())  # 提取索引名列表

        all_fields = {}
        for index in indices:
            mapping = await es.indices.get_mapping(index=index)
            index_mapping = mapping[index]['mappings']['properties']
            tmp = extract_fields(index_mapping)
            all_fields[index_name] = {**tmp, **all_fields.get(index_name, {})}
        return response_ok(all_fields)
