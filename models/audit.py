#!/usr/bin/env python
# coding=utf-8

from tortoise import fields

from models import BaseModel


class AuditLog(BaseModel):
    """
    操作审计记录
    """
    username: str = fields.CharField(max_length=32, index=True, description="用户名")
    action: str = fields.CharField(max_length=50, description="操作")
    module: str = fields.CharField(max_length=80, index=True, description="模块")
    result: str = fields.CharField(max_length=20, description="结果")
    client_ip: str = fields.CharField(max_length=50, index=True, description="客户端IP")
    client_ua: str = fields.CharField(max_length=255, description="客户端UA")
    uri: str = fields.CharField(max_length=255, null=True, description="请求URL")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "ctime": str(self.ctime),
            "action": self.action,
            "module": self.module,
            "result": self.result,
            "client_ip": self.client_ip,
            "client_ua": self.client_ua,
            "uri": self.uri
        }

    class Meta:
        table = "audit_log"
