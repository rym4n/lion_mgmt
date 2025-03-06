#!/usr/bin/env python
# coding=utf-8

import uuid

from tortoise import Model, fields


class BaseModel(Model):
    """
    Base Sanic Security model that all other models derive from.

    Attributes:
        id (str): Primary key of model.
        ctime (datetime): Time this model was created in the database.
        utime (datetime): Time this model was updated in the database.
        deleted (bool): Renders the model filterable without removing from the database.
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
