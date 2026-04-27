#!/usr/bin/env python
# coding=utf-8

import uuid

from tortoise import Model, fields

from libs.utils import decrypt_aes256_gcm, encrypt_aes256_gcm


_ENCRYPTED_PREFIX = "enc::"


def unwrap_encrypted_value(value):
    if not isinstance(value, str):
        return value

    if value.startswith(_ENCRYPTED_PREFIX):
        value = value[len(_ENCRYPTED_PREFIX) :]

    try:
        return decrypt_aes256_gcm(value)
    except ValueError:
        return value


def parse_encrypted_json(value, default=None):
    import orjson

    value = unwrap_encrypted_value(value)
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value.copy() if isinstance(value, dict) else list(value)
    if isinstance(value, str):
        try:
            return orjson.loads(value)
        except orjson.JSONDecodeError:
            return default
    return default


class BaseModel(Model):
    """
    基础模型，维护所有模型的公共字段

    公共字段:
        id (str): 主键
        ctime (datetime): 创建时间
        utime (datetime): 更新时间
        deleted (bool): 删除标识
    """

    id: str = fields.CharField(pk=True, max_length=36, default=lambda: str(uuid.uuid4()))
    ctime = fields.DatetimeField(null=False, auto_now_add=True, description="创建时间")
    utime = fields.DatetimeField(null=False, auto_now=True, description="更新时间")
    deleted: bool = fields.BooleanField(default=False, description="删除标识")
    tenant_id: str = fields.CharField(max_length=255, default="8bce552d-c27c-41d4-8a1b-8c5bf38dfe18", null=True, description="默认租户ID")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        raise NotImplementedError

    class Meta:
        abstract = True


class EncryptedCharField(fields.CharField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def to_db_value(self, value, instance):
        if value is None:
            return value
        if isinstance(value, str):
            if value.startswith(_ENCRYPTED_PREFIX):
                return value[len(_ENCRYPTED_PREFIX) :]
            try:
                decrypt_aes256_gcm(value)
                return value
            except ValueError:
                return encrypt_aes256_gcm(value)
        return value

    def to_python_value(self, value):
        return unwrap_encrypted_value(value)
