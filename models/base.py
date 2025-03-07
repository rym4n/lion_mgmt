#!/usr/bin/env python
# coding=utf-8

import uuid

from tortoise import Model, fields


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

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        raise NotImplementedError

    class Meta:
        abstract = True
