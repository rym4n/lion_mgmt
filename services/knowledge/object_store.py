# coding=utf-8
"""知识库文档的 RustFS 读写（SPEC §3.4 D4）。

每篇文档两个对象：
  knowledge/{document_id}/raw   —— 原始字节，重建索引的唯一依据
  knowledge/{document_id}/text  —— lion_agent 写入的派生纯文本，kb_read 用

键格式与 lion_agent/knowledge/object_store.py 逐字一致，两侧读写同一批对象。
调用范式对齐 libs/utils.py。
"""

import re

import aioboto3
from botocore.config import Config
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from settings import FS_AK, FS_SK, RY_USER_BUCKET, S3_HOST

_KEY_PREFIX = "knowledge"
# document_id 是 UUID。拼进对象键前必须校验：路径穿越是本仓已知风险类别
# （见 docs/白盒审计结果.md）。
_DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _validate(document_id: str) -> str:
    if not isinstance(document_id, str) or not _DOCUMENT_ID_RE.match(document_id):
        raise ValueError(f"invalid document_id: {document_id!r}")
    return document_id


def raw_key(document_id: str) -> str:
    return f"{_KEY_PREFIX}/{_validate(document_id)}/raw"


def text_key(document_id: str) -> str:
    return f"{_KEY_PREFIX}/{_validate(document_id)}/text"


def _client():
    return aioboto3.Session().client(
        "s3",
        endpoint_url=S3_HOST,
        aws_access_key_id=FS_AK,
        aws_secret_access_key=FS_SK,
        config=Config(signature_version="s3v4"),
    )


async def put_raw(document_id: str, data: bytes) -> str:
    """写入原始字节，返回对象键。"""
    key = raw_key(document_id)
    try:
        async with _client() as s3:
            await s3.put_object(Bucket=RY_USER_BUCKET, Key=key, Body=data)
    except Exception as exc:
        logger.error("knowledge_put_raw_failed error_type=%s", type(exc).__name__)
        raise CustomError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE) from None
    return key


async def get_raw(document_id: str) -> bytes:
    async with _client() as s3:
        response = await s3.get_object(Bucket=RY_USER_BUCKET, Key=raw_key(document_id))
        async with response["Body"] as stream:
            return await stream.read()


async def get_text(document_id: str) -> str:
    """读派生纯文本。对象不存在时返回空串——文档可能仍在解析中。"""
    try:
        async with _client() as s3:
            response = await s3.get_object(
                Bucket=RY_USER_BUCKET, Key=text_key(document_id)
            )
            async with response["Body"] as stream:
                return (await stream.read()).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("knowledge_get_text_missing error_type=%s", type(exc).__name__)
        return ""


async def delete_objects(document_id: str) -> None:
    """删两个对象。失败只记日志——元数据已删，残留对象不影响正确性。"""
    async with _client() as s3:
        for key in (raw_key(document_id), text_key(document_id)):
            try:
                await s3.delete_object(Bucket=RY_USER_BUCKET, Key=key)
            except Exception as exc:
                logger.warning(
                    "knowledge_delete_object_failed key=%s error_type=%s",
                    key, type(exc).__name__,
                )
