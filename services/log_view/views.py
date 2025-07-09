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
@requires_permission('logs.get')
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
