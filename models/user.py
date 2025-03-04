# coding=utf-8

from tortoise import Model, fields


class User(Model):
    """用户表"""
    id = fields.BigIntField(pk=True, description="用户id")
    name = fields.CharField(max_length=20, null=False, default="", description="姓名")
    ctime = fields.DatetimeField(null=False, auto_now_add=True, description="创建时间")
    utime = fields.DatetimeField(null=False, auto_now=True, description="更新时间")
